import hashlib
import shutil
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from queue import Queue
from threading import Event, Thread, current_thread
from unittest.mock import ANY, patch

import pymupdf

from analise.relatorio import RelatorioIndisponivel, recuperar_relatorio
from banco import conexao as banco
from processamento import processador
from teste_recuperacao_relatorio import CasoLoteTemporario


class TesteDuplicidadeProcessamento(CasoLoteTemporario):
    def pdf_identificado(self, caminho, titulo):
        with pymupdf.open() as pdf:
            pagina = pdf.new_page()
            pagina.insert_text((35, 45), titulo)
            pagina.insert_text((35, 65), "NOME: TITULAR FICTICIO")
            pagina.insert_text((35, 85), "CPF DO TITULAR: 00000000191")
            pagina.insert_text((35, 105), "RG: 12.345.678-9")
            if titulo == "CARTEIRA NACIONAL DE HABILITACAO":
                pagina.insert_text((35, 125), "CAT. HAB.: B")
            pdf.save(caminho)

    def test_copias_binarias_de_nomes_diferentes_entram_uma_vez(self):
        original = self.criar_pdf("a_original.pdf")
        copia = self.entrada / "z_copia.pdf"
        shutil.copyfile(original, copia)
        arquivos_antes = self.hashes_dos_pdfs()
        with closing(self.conectar(self.caminho_banco)) as conexao, patch.object(
            processador, "ler_pdf", wraps=processador.ler_pdf,
        ) as ler:
            ident, _ = self.criar_analise(conexao)
            resultado = processador.processar_pasta(conexao, ident, self.entrada)
            ler.assert_called_once_with(str(original), leitor_ocr=ANY)
            duplicata, = resultado["duplicatas"]
            self.assertEqual(duplicata["arquivo"], copia.name)
            self.assertEqual(duplicata["duplicado_de"], original.name)
            self.assertIn("conteúdo idêntico", duplicata["mensagem"])
            self.assertEqual(resultado["relatorio"]["total_documentos"], 1)
            resumo = hashlib.sha256(original.read_bytes()).hexdigest()
            self.assertNotIn(resumo, "\n".join(conexao.iterdump()).lower())
        self.assertEqual(self.hashes_dos_pdfs(), arquivos_antes)

    def test_nome_igual_em_diretorios_diferentes_nao_identifica_duplicata(self):
        primeiro = self.entrada / "mesmo_nome.pdf"
        outra_pasta = self.pasta / "outra_entrada"
        outra_pasta.mkdir()
        segundo = outra_pasta / primeiro.name
        self.pdf_identificado(primeiro, "CARTEIRA DE IDENTIDADE")
        self.pdf_identificado(segundo, "CARTEIRA NACIONAL DE HABILITACAO")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            for arquivo in (primeiro, segundo):
                ident, _ = self.criar_analise(conexao)
                resultado = processador.processar_lote(conexao, ident, arquivo.parent)
                self.assertEqual(resultado["duplicatas"], [])
            caminhos = {r[0] for r in conexao.execute("SELECT caminho_origem FROM documentos")}
            self.assertEqual(caminhos, {str(primeiro.resolve()), str(segundo.resolve())})

    def test_identidade_e_cnh_do_mesmo_titular_permanecem_documentos_distintos(self):
        self.pdf_identificado(self.entrada / "identidade.pdf", "CARTEIRA DE IDENTIDADE")
        self.pdf_identificado(self.entrada / "cnh.pdf", "CARTEIRA NACIONAL DE HABILITACAO")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, _ = self.criar_analise(conexao)
            r = processador.processar_pasta(conexao, ident, self.entrada)
        self.assertEqual(r["duplicatas"], [])
        self.assertEqual({doc["tipo"] for doc in r["relatorio"]["documentos"]},
                         {"CNH", "DOCUMENTO_IDENTIDADE"})
        self.assertIn(("CPF", "COMPATIVEL"),
                      {(c["campo"], c["resultado"]) for c in r["conferencias"]})

    def test_mesmo_conteudo_em_analises_distintas_nao_e_deduplicado_globalmente(self):
        self.criar_pdf("a.pdf")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            for _ in range(2):
                ident, _ = self.criar_analise(conexao)
                r = processador.processar_lote(conexao, ident, self.entrada)
                self.assertEqual(len(r["documentos"]), 1)
                self.assertEqual(r["duplicatas"], [])
        self.assertEqual(self.estado_banco(), (["CONCLUIDA", "CONCLUIDA"], (2, 4, 0)))

    def test_analise_confirmada_sem_documentos_tambem_bloqueia_reprocessamento(self):
        self.criar_pdf("a.pdf")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, _ = self.criar_analise(conexao)
            with conexao:
                conexao.execute("UPDATE analises SET status='CONCLUIDA' WHERE id=?", (ident,))
            with patch.object(processador, "ler_pdf") as ler:
                with self.assertRaisesRegex(processador.ErroEstadoAnalise, "já foi concluída"):
                    processador.processar_lote(conexao, ident, self.entrada)
            ler.assert_not_called()
            self.assertFalse(conexao.in_transaction)
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (0, 0, 0)))

    def concorrer(self, ids, *, falhar_primeiro=False):
        pronta = Event()
        segunda_tentou = Event()
        resultados = Queue()
        leituras = []
        ler = processador.ler_pdf
        salvar = processador.salvar_resultado_analise

        class ConexaoObservada(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "BEGIN IMMEDIATE" and current_thread().name == "ceuma-segunda":
                    segunda_tentou.set()
                return super().execute(sql, *args)

        def ler_com_sincronizacao(caminho, *, leitor_ocr):
            operacao = current_thread().name
            leituras.append(operacao)
            if operacao == "ceuma-primeira" and not pronta.is_set():
                pronta.set()
                if not segunda_tentou.wait(10):
                    raise TimeoutError("A segunda conexão não tentou obter a escrita.")
            return ler(caminho, leitor_ocr=leitor_ocr)

        def salvar_com_falha(*args, **kwargs):
            resposta = salvar(*args, **kwargs)
            if falhar_primeiro and current_thread().name == "ceuma-primeira":
                raise sqlite3.OperationalError("Falha fictícia depois da primeira gravação.")
            return resposta

        def rodar(ident):
            try:
                with closing(sqlite3.connect(self.caminho_banco, timeout=10,
                                            factory=ConexaoObservada)) as conexao:
                    conexao.execute("PRAGMA foreign_keys = ON")
                    resposta = processador.processar_lote(conexao, ident, self.entrada)
            except Exception as erro:
                resposta = erro
            resultados.put((current_thread().name, resposta))

        primeira = Thread(target=rodar, args=(ids[0],), name="ceuma-primeira", daemon=True)
        segunda = Thread(target=rodar, args=(ids[1],), name="ceuma-segunda", daemon=True)
        with patch.object(processador, "ler_pdf", side_effect=ler_com_sincronizacao), patch.object(
            processador, "salvar_resultado_analise", side_effect=salvar_com_falha,
        ):
            primeira.start()
            try:
                self.assertTrue(pronta.wait(10))
                segunda.start()
            finally:
                primeira.join(15)
                if segunda.ident is not None:
                    segunda.join(15)
            self.assertFalse(primeira.is_alive() or segunda.is_alive())
        self.assertTrue(segunda_tentou.is_set())
        return dict(resultados.get_nowait() for _ in range(2)), leituras

    def test_duas_conexoes_na_mesma_analise_e_recuperacao_apos_bloqueio(self):
        self.criar_pdf("a.pdf")
        arquivos_antes = self.hashes_dos_pdfs()
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, codigo = self.criar_analise(conexao)
            operacoes, leituras = self.concorrer((ident, ident))
            self.assertEqual(leituras, ["ceuma-primeira"])
            self.assertIsInstance(operacoes["ceuma-segunda"], processador.ErroEstadoAnalise)
            self.assertEqual(len(operacoes["ceuma-primeira"]["documentos"]), 1)
            with patch.object(processador, "ler_pdf") as ler:
                recuperado, _ = recuperar_relatorio(conexao, codigo)
            ler.assert_not_called()
            self.assertEqual(recuperado["total_documentos"], 1)
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), arquivos_antes)

    def test_duas_conexoes_em_analises_diferentes_preservam_os_dois_lotes(self):
        self.criar_pdf("a.pdf")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ids = tuple(self.criar_analise(conexao)[0] for _ in range(2))
        operacoes, leituras = self.concorrer(ids)
        self.assertEqual(leituras, ["ceuma-primeira", "ceuma-segunda"])
        self.assertTrue(all(len(r["documentos"]) == 1 for r in operacoes.values()))
        self.assertEqual(self.estado_banco(), (["CONCLUIDA", "CONCLUIDA"], (2, 4, 0)))

    def test_falha_concorrente_reverte_e_permite_repetir_a_mesma_analise(self):
        self.criar_pdf("a.pdf")
        arquivos_antes = self.hashes_dos_pdfs()
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, _ = self.criar_analise(conexao)
        operacoes, leituras = self.concorrer((ident, ident), falhar_primeiro=True)
        self.assertEqual(leituras, ["ceuma-primeira", "ceuma-segunda"])
        self.assertIsInstance(operacoes["ceuma-primeira"], processador.ErroPersistencia)
        self.assertEqual(len(operacoes["ceuma-segunda"]["documentos"]), 1)
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), arquivos_antes)

    def test_timeout_no_sqlite_nao_grava_e_permite_nova_tentativa(self):
        self.criar_pdf("a.pdf")
        with closing(self.conectar(self.caminho_banco)) as dona, closing(
            sqlite3.connect(self.caminho_banco, timeout=0),
        ) as outra:
            ident, _ = self.criar_analise(dona)
            dona.execute("BEGIN IMMEDIATE")
            with patch.object(processador, "ler_pdf") as ler:
                with self.assertRaisesRegex(processador.ErroPersistencia, "banco está ocupado"):
                    processador.processar_lote(outra, ident, self.entrada)
            ler.assert_not_called()
            self.assertFalse(outra.in_transaction)
            self.assertEqual(self.estado_banco(), (["EM_ANDAMENTO"], (0, 0, 0)))
            dona.rollback()
            processador.processar_lote(outra, ident, self.entrada)
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))

    def test_retencao_remove_resultados_em_cascata_e_preserva_pdfs(self):
        original = self.criar_pdf("a.pdf")
        shutil.copyfile(original, self.entrada / "b_copia.pdf")
        self.pdf_identificado(self.entrada / "c.pdf", "CARTEIRA NACIONAL DE HABILITACAO")
        arquivos_antes = self.hashes_dos_pdfs()
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, codigo = self.criar_analise(conexao)
            r = processador.processar_lote(conexao, ident, self.entrada)
            self.assertEqual(len(r["duplicatas"]), 1)
            self.assertGreater(len(r["conferencias"]), 0)
            self.relogio.return_value = self.agora + timedelta(hours=24)
            self.assertEqual(banco.limpar_analises_expiradas(conexao), 1)
            with self.assertRaises(RelatorioIndisponivel):
                recuperar_relatorio(conexao, codigo)
        self.assertEqual(self.estado_banco(), ([], (0, 0, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), arquivos_antes)
