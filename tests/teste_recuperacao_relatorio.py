import hashlib
import shutil
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf

from ceuma.relatorios import relatorio
from ceuma.banco import conexao as banco
from ceuma.interface import janela_principal as gui
from ceuma.processamento import lote as processador
from teste_retencao_interface import Agenda, Controle, ThreadImediata


class CasoLoteTemporario(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(self.enterContext(TemporaryDirectory()))
        self.caminho_banco = self.pasta / "ceuma_ficticio.db"
        self.entrada = self.pasta / "entrada"
        self.entrada.mkdir()
        self.conectar = banco.conectar_banco
        with closing(self.conectar(self.caminho_banco)) as conexao:
            banco.criar_tabelas(conexao)
        self.agora = datetime(2040, 1, 2, 12, tzinfo=timezone.utc)
        self.relogio = self.enterContext(patch.object(banco, "agora_utc", return_value=self.agora))
        self.console = self.enterContext(redirect_stdout(StringIO()))

    def criar_pdf(self, nome):
        caminho = self.entrada / nome
        with pymupdf.open() as pdf:
            pagina = pdf.new_page()
            pagina.insert_text((35, 45), "CARTEIRA DE IDENTIDADE")
            pagina.insert_text((35, 65), "NOME: TITULAR FICTICIO")
            pagina.insert_text((35, 85), "CPF DO TITULAR: 00000000191")
            pdf.save(caminho)
        return caminho

    def hashes_dos_pdfs(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.entrada.iterdir() if p.is_file()}

    def estado_banco(self):
        with closing(self.conectar(self.caminho_banco)) as c:
            estados = [r[0] for r in c.execute("SELECT status FROM analises ORDER BY id")]
            quantidades = tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                                for t in ("documentos", "campos_extraidos", "conferencias"))
        return estados, quantidades

    @staticmethod
    def criar_analise(conexao):
        codigo = banco.criar_analise(conexao)
        ident = conexao.execute("SELECT id FROM analises WHERE codigo = ?", (codigo,)).fetchone()[0]
        return ident, codigo


class TesteTransacaoLote(CasoLoteTemporario):
    def test_operacoes_nao_revertem_transacao_ja_aberta_pelo_chamador(self):
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, codigo = self.criar_analise(conexao)
            conexao.execute("BEGIN")
            conexao.execute("UPDATE analises SET status='CONCLUIDA' WHERE id=?", (ident,))
            with self.assertRaises(processador.ErroPersistencia):
                processador.processar_lote(conexao, ident, self.entrada)
            with self.assertRaises(relatorio.ErroApresentacao):
                relatorio.recuperar_relatorio(conexao, codigo)
            self.assertTrue(conexao.in_transaction)
            self.assertEqual(self.estado_banco(), (["EM_ANDAMENTO"], (0, 0, 0)))
            self.assertEqual(conexao.execute("SELECT status FROM analises").fetchone()[0], "CONCLUIDA")

    def test_pdf_invalido_depois_de_um_valido_desfaz_gravacoes(self):
        self.criar_pdf("a_valido.pdf")
        (self.entrada / "z_corrompido.pdf").write_bytes(b"PDF ficticio invalido")
        originais = self.hashes_dos_pdfs()
        with closing(self.conectar(self.caminho_banco)) as conexao, patch.object(
            processador, "salvar_resultado_analise", wraps=processador.salvar_resultado_analise,
        ) as salvar, patch.object(relatorio, "gerar_relatorio") as apresentar:
            ident, _ = self.criar_analise(conexao)
            with self.assertRaises(processador.ErroProcessamento):
                processador.processar_pasta(conexao, ident, self.entrada)
            salvar.assert_called_once()
            apresentar.assert_not_called()
            self.assertFalse(conexao.in_transaction)
        self.assertEqual(self.estado_banco(), (["EM_ANDAMENTO"], (0, 0, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), originais)

    def test_falha_de_persistencia_depois_de_inserir_uma_parte(self):
        self.criar_pdf("a_valido.pdf")
        originais = self.hashes_dos_pdfs()
        salvar = processador.salvar_resultado_analise

        def gravar_e_falhar(*args, **kwargs):
            salvar(*args, **kwargs)
            self.assertEqual(self.estado_banco(), (["EM_ANDAMENTO"], (0, 0, 0)))
            raise ValueError("Falha fictícia na persistência")

        with closing(self.conectar(self.caminho_banco)) as conexao, patch.object(
            processador, "salvar_resultado_analise", side_effect=gravar_e_falhar,
        ):
            ident, _ = self.criar_analise(conexao)
            with self.assertRaises(processador.ErroPersistencia):
                processador.processar_lote(conexao, ident, self.entrada)
            self.assertFalse(conexao.in_transaction)
        self.assertEqual(self.estado_banco(), (["EM_ANDAMENTO"], (0, 0, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), originais)

    def test_falha_imediatamente_antes_de_confirmar_reverte_ate_status(self):
        self.criar_pdf("a.pdf")
        self.criar_pdf("b.pdf")
        originais = self.hashes_dos_pdfs()

        class FalhaNoCommit(sqlite3.Connection):
            falhar = False
            antes_da_confirmacao = None

            def commit(self):
                if self.falhar:
                    status = self.execute("SELECT status FROM analises").fetchone()[0]
                    conferencia = self.execute("SELECT COUNT(*) FROM conferencias").fetchone()[0]
                    self.antes_da_confirmacao = (status, conferencia, self.in_transaction)
                    raise sqlite3.OperationalError("Falha fictícia antes da confirmação")
                super().commit()

        with closing(sqlite3.connect(self.caminho_banco, factory=FalhaNoCommit)) as conexao:
            conexao.execute("PRAGMA foreign_keys = ON")
            ident, _ = self.criar_analise(conexao)
            conexao.falhar = True
            with self.assertRaises(processador.ErroPersistencia):
                processador.processar_lote(conexao, ident, self.entrada)
            self.assertEqual(conexao.antes_da_confirmacao, ("CONCLUIDA", 2, True))
            self.assertFalse(conexao.in_transaction)
        self.assertEqual(self.estado_banco(), (["EM_ANDAMENTO"], (0, 0, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), originais)

    def test_falha_de_relatorio_apos_commit_mantem_lote_visivel_e_recuperavel(self):
        self.criar_pdf("a.pdf")
        self.criar_pdf("b.pdf")
        originais = self.hashes_dos_pdfs()

        def falhar(c, _):
            self.assertTrue(c.in_transaction)  # A recuperação abriu uma transação de leitura.
            self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (2, 4, 2)))
            raise RuntimeError("Falha fictícia no relatório")

        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, codigo = self.criar_analise(conexao)
            with patch.object(relatorio, "gerar_relatorio", side_effect=falhar):
                with self.assertRaises(relatorio.ErroApresentacao) as erro:
                    processador.processar_pasta(conexao, ident, self.entrada)
                self.assertEqual(erro.exception.codigo, codigo)
            self.assertFalse(conexao.in_transaction)
            with patch.object(processador, "ler_pdf") as ler:
                dados, prazo = relatorio.recuperar_relatorio(conexao, codigo)
                ler.assert_not_called()
            self.assertEqual(prazo, self.agora + timedelta(hours=24))
            self.assertEqual(dados["total_documentos"], 2)
            self.assertEqual(dados["total_conferencias"], 2)
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (2, 4, 2)))
        self.assertEqual(self.hashes_dos_pdfs(), originais)

    def test_api_direta_bloqueia_relatorio_ja_expirado_antes_de_gerar(self):
        self.criar_pdf("ficticio.pdf")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, _ = self.criar_analise(conexao)
            self.relogio.return_value = self.agora + timedelta(hours=24)
            with patch.object(relatorio, "gerar_relatorio") as montar:
                with self.assertRaises(relatorio.RelatorioIndisponivel):
                    processador.processar_pasta(conexao, ident, self.entrada)
            montar.assert_not_called()
            # O acesso direto ao gerador também deve bloquear o relatório vencido.
            with self.assertRaises(relatorio.RelatorioIndisponivel):
                relatorio.gerar_relatorio(conexao, ident)

    def test_api_direta_descarta_relatorio_que_expira_enquanto_e_gerado(self):
        self.criar_pdf("ficticio.pdf")
        gerar = relatorio.gerar_relatorio

        def gerar_e_expirar(*args):
            dados = gerar(*args)
            self.relogio.return_value = self.agora + timedelta(hours=24)
            return dados

        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, codigo = self.criar_analise(conexao)
            with patch.object(relatorio, "gerar_relatorio", side_effect=gerar_e_expirar):
                with self.assertRaises(relatorio.RelatorioIndisponivel):
                    processador.processar_pasta(conexao, ident, self.entrada)
            self.assertFalse(conexao.in_transaction)
            self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))
            with self.assertRaises(relatorio.RelatorioIndisponivel):
                relatorio.recuperar_relatorio(conexao, codigo)

    def test_api_direta_preserva_lote_apos_falha_de_apresentacao_ate_expirar(self):
        self.criar_pdf("ficticio.pdf")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, codigo = self.criar_analise(conexao)
            with patch.object(relatorio, "gerar_relatorio", side_effect=RuntimeError("texto privado fictício")):
                with self.assertRaises(relatorio.ErroApresentacao) as erro:
                    processador.processar_pasta(conexao, ident, self.entrada)
            self.assertNotIn("texto privado fictício", str(erro.exception))
            self.assertEqual(relatorio.recuperar_relatorio(conexao, codigo)[0]["total_documentos"], 1)
            self.relogio.return_value = self.agora + timedelta(hours=24)
            with self.assertRaises(relatorio.RelatorioIndisponivel):
                relatorio.recuperar_relatorio(conexao, codigo)
            self.assertEqual(banco.limpar_analises_expiradas(conexao), 1)
            self.assertEqual(self.estado_banco(), ([], (0, 0, 0)))

    def test_busca_sem_codigo_ignora_analises_ativas_incompletas_e_expiradas(self):
        self.criar_pdf("a.pdf")
        with closing(self.conectar(self.caminho_banco)) as conexao:
            ident, valido = self.criar_analise(conexao)
            processador.processar_lote(conexao, ident, self.entrada)
            _, incompleto = self.criar_analise(conexao)
            _, expirado = self.criar_analise(conexao)
            with conexao:
                conexao.execute("UPDATE analises SET status='CONCLUIDA', data_expiracao=? WHERE codigo=?",
                                (self.agora.isoformat(), expirado))
            with self.assertRaises(relatorio.RelatorioIndisponivel):
                relatorio.recuperar_relatorio(conexao, incompleto)
            with self.assertRaises(relatorio.RelatorioIndisponivel):
                relatorio.recuperar_relatorio(conexao, expirado)
            dados, _ = relatorio.recuperar_relatorio(conexao)
            self.assertEqual(dados["codigo"], valido)


class TesteRecuperacaoInterface(CasoLoteTemporario):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(banco, "conectar_banco", side_effect=lambda: self.conectar(self.caminho_banco)))
        self.enterContext(patch.object(gui, "Thread", ThreadImediata))
        self.enterContext(patch.object(gui.JanelaPrincipal, "criar_interface", self.criar_controles))
        self.logs = self.enterContext(patch.object(gui.JanelaPrincipal, "registrar_log"))
        self.enterContext(patch.object(gui.messagebox, "showinfo"))
        self.erro = self.enterContext(patch.object(gui.messagebox, "showerror"))
        self.aviso = self.enterContext(patch.object(gui.messagebox, "showwarning"))
        self.codigo = self.enterContext(patch.object(gui.simpledialog, "askstring"))
        self.agenda, self.janela = self.nova_janela()

    @staticmethod
    def criar_controles(janela):
        janela.botao_pasta = Controle()
        janela.botao_processar = Controle()
        janela.botao_recuperar = Controle()
        janela.botao_exportar = Controle()
        janela.status = Controle()

    @staticmethod
    def nova_janela():
        agenda = Agenda()
        return agenda, gui.JanelaPrincipal(agenda)

    def iniciar(self):
        self.janela.pasta_selecionada = self.entrada
        self.janela.iniciar_analise()
        self.agenda.executar(150)

    def falhar_relatorio(self):
        self.criar_pdf("a.pdf")
        with patch.object(relatorio, "gerar_relatorio", side_effect=RuntimeError("Falha fictícia")):
            self.iniciar()
        self.assertEqual(self.erro.call_args.args[0], "Erro de apresentação")
        self.assertIsNone(self.janela.relatorio_atual)
        self.assertEqual(self.janela.botao_exportar.opcoes["state"], "disabled")
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))
        return self.janela.codigo_recuperacao

    def test_interface_remove_apenas_analise_vazia_de_pdf_invalido(self):
        self.criar_pdf("a.pdf")
        (self.entrada / "z_invalido.pdf").write_bytes(b"PDF ficticio invalido")
        originais = self.hashes_dos_pdfs()
        self.iniciar()
        self.assertEqual(self.erro.call_args.args[0], "Erro de processamento")
        self.assertEqual(self.estado_banco(), ([], (0, 0, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), originais)

    def test_interface_distingue_erro_de_persistencia(self):
        self.criar_pdf("a.pdf")
        with patch.object(processador, "salvar_resultado_analise", side_effect=sqlite3.OperationalError):
            self.iniciar()
        self.assertEqual(self.erro.call_args.args[0], "Erro de persistência")
        self.assertEqual(self.estado_banco(), ([], (0, 0, 0)))

    def test_interface_remove_analise_vazia_se_consulta_por_codigo_falhar(self):
        self.criar_pdf("a.pdf")

        class FalhaAoConsultar(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("SELECT id FROM analises WHERE codigo ="):
                    raise sqlite3.OperationalError("Falha fictícia na consulta")
                return super().execute(sql, *args)

        def abrir():
            conexao = sqlite3.connect(self.caminho_banco, factory=FalhaAoConsultar)
            conexao.row_factory = sqlite3.Row
            return conexao

        with patch.object(banco, "conectar_banco", side_effect=abrir):
            self.iniciar()
        self.assertEqual(self.erro.call_args.args[0], "Erro de persistência")
        self.assertEqual(self.estado_banco(), ([], (0, 0, 0)))

    def test_interface_recupera_por_codigo_apos_falha_sem_reler_pdfs(self):
        codigo = self.falhar_relatorio()
        originais = self.hashes_dos_pdfs()
        self.codigo.return_value = f" {codigo.lower()} "
        with patch.object(processador, "ler_pdf") as ler:
            self.janela.recuperar_relatorio()
            self.agenda.executar(150)
        ler.assert_not_called()
        self.assertEqual(self.codigo.call_args.kwargs["initialvalue"], codigo)
        self.assertEqual(self.janela.relatorio_atual["codigo"], codigo)
        self.assertEqual(self.janela.botao_exportar.opcoes["state"], "normal")
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))
        self.assertEqual(self.hashes_dos_pdfs(), originais)

    def test_reabrir_interface_recupera_ultima_analise_sem_codigo_em_memoria(self):
        codigo = self.falhar_relatorio()
        agenda, janela = self.nova_janela()
        self.assertIsNone(janela.codigo_recuperacao)
        agenda.executar(0)
        agenda.executar(150)
        self.codigo.return_value = ""
        with patch.object(processador, "ler_pdf") as ler:
            janela.recuperar_relatorio()
            agenda.executar(150)
        ler.assert_not_called()
        self.assertEqual(janela.relatorio_atual["codigo"], codigo)

    def test_expiracao_durante_recuperacao_descarta_resultado(self):
        codigo = self.falhar_relatorio()
        self.codigo.return_value = codigo
        gerar = relatorio.gerar_relatorio

        def gerar_e_expirar(*args):
            dados = gerar(*args)
            self.relogio.return_value = self.agora + timedelta(hours=24)
            return dados

        with patch.object(relatorio, "gerar_relatorio", side_effect=gerar_e_expirar):
            self.janela.recuperar_relatorio()
            self.agenda.executar(150)
        self.assertIsNone(self.janela.relatorio_atual)
        self.assertEqual(self.janela.botao_exportar.opcoes["state"], "disabled")
        self.assertEqual(self.aviso.call_args.args[0], "Relatório indisponível")
        with patch.object(relatorio, "gerar_relatorio") as gerar_expirado:
            self.janela.recuperar_relatorio()
            self.agenda.executar(150)
        gerar_expirado.assert_not_called()

    def test_erro_ao_apresentar_no_tk_nao_remove_analise_nem_para_a_fila(self):
        self.criar_pdf("a.pdf")
        with patch.object(self.janela, "finalizar_sucesso", side_effect=RuntimeError("Erro fictício no Tk")):
            self.iniciar()
        self.assertEqual(self.erro.call_args.args[0], "Erro de apresentação")
        self.assertIsNone(self.janela.relatorio_atual)
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))
        self.codigo.return_value = self.janela.codigo_recuperacao
        self.janela.recuperar_relatorio()
        self.agenda.executar(150)
        self.assertIsNotNone(self.janela.relatorio_atual)

    def test_limpeza_de_erro_nao_exclui_lote_que_ja_foi_confirmado(self):
        self.criar_pdf("a.pdf")
        gravar = processador.processar_lote

        def confirmar_e_falhar(*args):
            gravar(*args)
            raise processador.ErroPersistencia("Falha fictícia após a confirmação")

        with patch.object(processador, "processar_lote", side_effect=confirmar_e_falhar):
            self.iniciar()
        self.assertEqual(self.erro.call_args.args[0], "Erro de persistência")
        self.assertEqual(self.estado_banco(), (["CONCLUIDA"], (1, 2, 0)))
        self.codigo.return_value = ""
        self.janela.recuperar_relatorio()
        self.agenda.executar(150)
        self.assertIsNotNone(self.janela.relatorio_atual)

    def test_logs_omitiram_nome_caminho_e_excecao_bruta_de_leitura(self):
        origem = self.criar_pdf("Pessoa Ficticia reservada.pdf")
        marcador = "texto bruto fictício"
        with patch.object(processador, "ler_pdf", side_effect=OSError(f"{marcador}: {origem}")):
            self.iniciar()

        saida = "\n".join(chamada.args[0] for chamada in self.logs.call_args_list)
        saida += "\n" + self.erro.call_args.args[1] + self.console.getvalue()
        self.assertIn("documento #1 do lote", saida)
        for privado in (origem.name, str(origem), marcador):
            self.assertNotIn(privado, saida)

    def test_erro_de_exportacao_omite_destino_e_mensagem_bruta(self):
        self.criar_pdf("ficticio.pdf")
        self.iniciar()
        destino = self.pasta / "Relatorio Pessoa Ficticia.csv"
        marcador = "erro bruto fictício"
        with (
            patch.object(gui.filedialog, "asksaveasfilename", return_value=str(destino)),
            patch("ceuma.relatorios.exportador_csv.exportar_relatorio_csv", side_effect=PermissionError(f"{marcador}: {destino}")),
        ):
            self.janela.exportar_relatorio()
            self.agenda.executar(150)
        saida = "\n".join(chamada.args[0] for chamada in self.logs.call_args_list)
        saida += "\n" + self.erro.call_args.args[1]
        self.assertIn("permissão de gravação", saida)
        for privado in (destino.name, str(destino), marcador):
            self.assertNotIn(privado, saida)

    def test_aviso_de_duplicidade_aparece_uma_vez_com_numeros_locais(self):
        original = self.criar_pdf("a_Pessoa Ficticia.pdf")
        copia = self.entrada / "b_Pessoa Ficticia.pdf"
        shutil.copyfile(original, copia)
        self.iniciar()

        mensagens = [chamada.args[0] for chamada in self.logs.call_args_list]
        avisos = [mensagem for mensagem in mensagens if "Cópia ignorada" in mensagem]
        self.assertEqual(len(avisos), 1)
        self.assertIn("documento #2", avisos[0])
        self.assertIn("documento #1", avisos[0])
        console = self.console.getvalue()
        self.assertNotIn("Cópia ignorada", console)
        for nome in (original.name, copia.name):
            self.assertNotIn(nome, "\n".join(mensagens) + console)
        self.assertEqual(self.janela.relatorio_atual["total_documentos"], 1)


if __name__ == "__main__":
    unittest.main()
