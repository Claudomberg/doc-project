import os
import csv
import sqlite3
import unittest
from collections import Counter
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ceuma.conferencia import automatica as conferidor_banco
from ceuma.relatorios import exportador_csv as exportador
from ceuma.conferencia.automatica import conferir_analise
from ceuma.extracao.campos import STATUS_CPF_TITULAR
from ceuma.relatorios.relatorio import RelatorioIndisponivel, recuperar_relatorio
from ceuma.banco import atribuicoes
from ceuma.banco import conexao
from ceuma.banco.esquema import VERSAO_ESQUEMA
from ceuma.banco.operacoes import adicionar_documento, registrar_conferencia, salvar_campo_extraido
from ceuma.processamento import lote as processador


_ESQUEMA_ANTERIOR = """
CREATE TABLE analises (
    id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT UNIQUE, data_criacao TEXT NOT NULL,
    data_expiracao TEXT NOT NULL, responsavel TEXT, tipo_procedimento TEXT,
    status TEXT NOT NULL DEFAULT 'EM_ANDAMENTO'
);
CREATE TABLE documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analise_id INTEGER NOT NULL
        REFERENCES analises(id) ON DELETE CASCADE, nome_arquivo TEXT NOT NULL,
    caminho_origem TEXT NOT NULL, tipo_identificado TEXT, quantidade_paginas INTEGER,
    status_leitura TEXT NOT NULL DEFAULT 'PENDENTE'
);
CREATE TABLE campos_extraidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, documento_id INTEGER NOT NULL
        REFERENCES documentos(id) ON DELETE CASCADE, nome_campo TEXT NOT NULL, valor TEXT,
    pagina_origem INTEGER, trecho_origem TEXT, confianca REAL,
    status TEXT NOT NULL DEFAULT 'ENCONTRADO'
);
CREATE TABLE conferencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analise_id INTEGER NOT NULL
        REFERENCES analises(id) ON DELETE CASCADE, campo_verificado TEXT NOT NULL,
    resultado TEXT NOT NULL, observacao TEXT
);
"""


def cpf_sintetico(sequencia):
    digitos = [int(digito) for digito in f"{sequencia:09d}"]
    for peso in (10, 11):
        resto = sum(digito * fator for digito, fator in zip(digitos, range(peso, 1, -1))) % 11
        digitos.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, digitos))


class TesteMigracao(unittest.TestCase):
    def setUp(self):
        self.banco = conexao.conectar_banco(":memory:")
        self.addCleanup(self.banco.close)
        self.banco.executescript(_ESQUEMA_ANTERIOR)
        agora = conexao.agora_utc()
        self.banco.execute(
            "INSERT INTO analises VALUES (1, 'ANTIGA', ?, ?, NULL, NULL, 'CONCLUIDA')",
            (agora.isoformat(), (agora + timedelta(hours=24)).isoformat()),
        )
        self.banco.execute("""
            INSERT INTO documentos (id, analise_id, nome_arquivo, caminho_origem, tipo_identificado)
            VALUES (1, 1, 'fixture.pdf', 'fixtures/fixture.pdf', 'CIN')
        """)
        self.banco.execute("""
            INSERT INTO campos_extraidos (documento_id, nome_campo, status)
            VALUES (1, 'CPF', 'AMBIGUO')
        """)
        self.banco.execute("""
            INSERT INTO conferencias (analise_id, campo_verificado, resultado, observacao)
            VALUES (1, 'CPF', 'NAO_CONFERIDO', 'AUTO: resultado histórico fictício')
        """)
        self.banco.commit()

    def test_migra_sem_presumir_titularidade(self):
        conexao.criar_tabelas(self.banco)
        self.assertEqual(self.banco.execute("PRAGMA user_version").fetchone()[0], VERSAO_ESQUEMA)
        self.assertEqual(self.banco.execute("SELECT modo FROM analises").fetchone()[0], "INDIVIDUAL")
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0], 0)
        self.assertIsNone(self.banco.execute("SELECT escopo FROM documentos").fetchone()[0])
        self.assertEqual(self.banco.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.banco.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migracao_repetida_preserva_esquema_e_dados(self):
        conexao.criar_tabelas(self.banco)
        antes = list(self.banco.iterdump())
        conexao.criar_tabelas(self.banco)
        self.assertEqual(list(self.banco.iterdump()), antes)

    def test_falha_no_meio_reverte_ddl_e_versao(self):
        antes = list(self.banco.iterdump())
        self.banco.set_authorizer(
            lambda acao, nome, *_: sqlite3.SQLITE_DENY
            if acao == sqlite3.SQLITE_CREATE_TABLE and nome == "documentos_pessoas"
            else sqlite3.SQLITE_OK
        )
        with self.assertRaises(sqlite3.DatabaseError):
            conexao.criar_tabelas(self.banco)
        self.banco.set_authorizer(None)
        self.assertFalse(self.banco.in_transaction)
        self.assertEqual(self.banco.execute("PRAGMA user_version").fetchone()[0], 0)
        self.assertEqual(list(self.banco.iterdump()), antes)
        conexao.criar_tabelas(self.banco)

    def test_recupera_campos_e_conferencias_historicas(self):
        conexao.criar_tabelas(self.banco)
        relatorio, _ = recuperar_relatorio(self.banco, "ANTIGA")
        self.assertEqual(relatorio["campos_extraidos"][0]["status"], "AMBIGUO")
        self.assertIn({"documento_id": 1, "campo": "CPF", "status": "AMBIGUO"},
                      relatorio["estados_campos"])
        self.assertEqual(relatorio["estado_conferencia"], "HISTORICO_SEM_ATRIBUICAO")
        self.assertEqual(relatorio["conferencias"][0]["resultado"], "NAO_CONFERIDO")
        self.assertEqual(relatorio["conferencias_estruturadas"], [])
        self.assertEqual(relatorio["pessoas"], [])

    def test_abertura_rejeita_versao_desconhecida(self):
        with TemporaryDirectory() as pasta:
            caminho = Path(pasta, "versao.db")
            banco = conexao.conectar_banco(caminho)
            banco.execute("PRAGMA user_version = 99")
            banco.close()
            with self.assertRaisesRegex(sqlite3.DatabaseError, "incompatível"):
                conexao.conectar_banco(caminho)

    def test_migracao_nao_confirma_transacao_alheia(self):
        self.banco.execute("UPDATE analises SET status = 'PENDENTE'")
        with self.assertRaises(sqlite3.ProgrammingError):
            conexao.criar_tabelas(self.banco)
        self.assertTrue(self.banco.in_transaction)
        self.banco.rollback()
        self.assertEqual(self.banco.execute("SELECT status FROM analises").fetchone()[0], "CONCLUIDA")

    def test_migracao_reverte_quando_chave_legada_e_invalida(self):
        self.banco.execute("PRAGMA foreign_keys = OFF")
        self.banco.execute("""
            INSERT INTO documentos (analise_id, nome_arquivo, caminho_origem)
            VALUES (99, 'fixture.pdf', 'fixtures/fixture.pdf')
        """)
        self.banco.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conexao.criar_tabelas(self.banco)
        self.assertEqual(self.banco.execute("PRAGMA user_version").fetchone()[0], 0)


class TesteAtribuicaoEConferencia(unittest.TestCase):
    def setUp(self):
        self.banco = conexao.conectar_banco(":memory:")
        self.addCleanup(self.banco.close)
        self.agora = conexao.agora_utc().replace(microsecond=0)
        self.relogio = self.enterContext(patch.object(conexao, "agora_utc", return_value=self.agora))
        conexao.criar_tabelas(self.banco)
        self.analise = self.criar_analise("FAMILIAR")
        self.pessoa = atribuicoes.criar_pessoa(self.banco, self.analise, "ESTUDANTE_TITULAR")
        self.integrante = atribuicoes.criar_pessoa(self.banco, self.analise, "INTEGRANTE")

    def criar_analise(self, modo=None):
        opcoes = {} if modo is None else {"modo": modo}
        codigo = conexao.criar_analise(self.banco, **opcoes)
        return self.banco.execute("SELECT id FROM analises WHERE codigo = ?", (codigo,)).fetchone()[0]

    def documento(self, sequencia=1, *, analise=None, nome="fixture.pdf", completo=True):
        ident = adicionar_documento(self.banco, analise or self.analise, nome, f"fixtures/{nome}", "CIN")
        if completo:
            for campo, valor, estado in (
                ("NOME", f"PERSONAGEM FICTICIO {sequencia}", "ENCONTRADO"),
                ("CPF", cpf_sintetico(sequencia), STATUS_CPF_TITULAR),
                ("DATA_NASCIMENTO", f"0{sequencia}/01/2000", "ENCONTRADO"),
            ):
                salvar_campo_extraido(self.banco, ident, campo, valor, status=estado)
        return ident

    def documento_atribuido(self, pessoa=None, sequencia=1, **opcoes):
        ident = self.documento(sequencia, **opcoes)
        atribuicoes.atribuir_documento(self.banco, ident, pessoa or self.pessoa)
        return ident

    def relatorio(self, analise=None):
        ident = self.analise if analise is None else analise
        with self.banco:
            self.banco.execute("UPDATE analises SET status = 'CONCLUIDA' WHERE id = ?", (ident,))
        return recuperar_relatorio(self.banco, analise_id=ident)[0]

    def test_individual_por_padrao_preserva_conferencia(self):
        analise = self.criar_analise()
        self.documento(analise=analise)
        self.documento(analise=analise)
        self.assertEqual(len(conferir_analise(self.banco, analise)), 3)
        relatorio = self.relatorio(analise)
        self.assertEqual(relatorio["modo"], "INDIVIDUAL")
        self.assertEqual(relatorio["estado_conferencia"], "SEM_DIVERGENCIAS")
        self.assertTrue(all(r["pessoa_id"] is None for r in relatorio["conferencias_estruturadas"]))

    def test_familiar_explicito_e_pessoas_sem_identificadores_civis(self):
        pessoa = atribuicoes.criar_pessoa(self.banco, self.analise)
        relatorio = self.relatorio()
        self.assertEqual(relatorio["modo"], "FAMILIAR")
        self.assertIn({"pessoa_id": pessoa, "papel": "INDEFINIDO", "documentos_ids": []},
                      relatorio["pessoas"])

    def test_maximo_um_titular_por_analise(self):
        with self.assertRaises(sqlite3.IntegrityError):
            atribuicoes.criar_pessoa(self.banco, self.analise, "ESTUDANTE_TITULAR")
        outra = self.criar_analise("FAMILIAR")
        self.assertNotEqual(atribuicoes.criar_pessoa(self.banco, outra, "ESTUDANTE_TITULAR"), self.pessoa)

    def test_atribuicoes_nao_cruzam_analises(self):
        outra = self.criar_analise("FAMILIAR")
        pessoa_outra = atribuicoes.criar_pessoa(self.banco, outra)
        documento = self.documento_atribuido()
        with self.assertRaises(sqlite3.IntegrityError):
            atribuicoes.atribuir_documento(self.banco, documento, pessoa_outra)
        self.assertEqual(self.relatorio()["pessoas"][0]["documentos_ids"], [documento])
        self.assertEqual(self.banco.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_documentos_compartilhados_e_inconclusivos_ficam_fora_de_pares(self):
        primeiro = self.documento_atribuido()
        segundo = self.documento_atribuido()
        compartilhado = self.documento()
        geral = self.documento()
        inconclusivo = self.documento()
        atribuicoes.marcar_documento_compartilhado(
            self.banco, compartilhado, [self.pessoa, self.integrante],
        )
        atribuicoes.marcar_documento_compartilhado(self.banco, geral)
        self.assertEqual(
            [r[0] for r in atribuicoes.listar_documentos_da_pessoa(self.banco, self.analise, self.pessoa)],
            [primeiro, segundo, compartilhado],
        )
        self.assertEqual(
            [r[0] for r in atribuicoes.listar_documentos_sem_atribuicao(self.banco, self.analise)],
            [inconclusivo],
        )
        self.assertEqual(len(conferir_analise(self.banco, self.analise)), 3)
        relatorio = self.relatorio()
        self.assertEqual(relatorio["documentos_compartilhados"], [compartilhado, geral])
        self.assertEqual(relatorio["documentos_inconclusivos"], [inconclusivo])
        self.assertEqual(relatorio["estado_conferencia"], "PARCIALMENTE_CONFERIDO")
        self.assertTrue(relatorio["aguardando_atribuicao"])

    def test_mudanca_e_remocao_invalidam_so_pares_afetados(self):
        primeiro = self.documento_atribuido()
        segundo = self.documento_atribuido(sequencia=2)
        self.documento_atribuido(self.integrante)
        self.documento_atribuido(self.integrante)
        conferir_analise(self.banco, self.analise)
        self.assertEqual(self.relatorio()["total_divergencias"], 3)
        atribuicoes.atribuir_documento(self.banco, segundo, self.integrante)
        temporario = self.relatorio()
        self.assertEqual(temporario["total_divergencias"], 0)
        self.assertEqual(temporario["total_pares_elegiveis"], 3)
        self.assertEqual(temporario["total_conferencias"], 3)
        self.assertEqual(temporario["estado_conferencia"], "PARCIALMENTE_CONFERIDO")
        self.assertEqual(len(conferir_analise(self.banco, self.analise)), 9)
        self.assertEqual(self.relatorio()["total_divergencias"], 6)
        atribuicoes.remover_atribuicao(self.banco, segundo)
        dados = self.relatorio()
        self.assertEqual(dados["total_divergencias"], 0)
        self.assertEqual(dados["pessoas"][0]["documentos_ids"], [primeiro])
        self.assertEqual(dados["documentos_inconclusivos"], [segundo])

    def test_ensaio_tres_pessoas_seis_documentos_mesmo_nome(self):
        individual = self.criar_analise()
        terceira = atribuicoes.criar_pessoa(self.banco, self.analise)
        for sequencia, pessoa in enumerate((self.pessoa, self.integrante, terceira), 1):
            for _ in range(2):
                self.documento(sequencia, analise=individual)
                self.documento_atribuido(pessoa, sequencia)
        for analise, pares, esperados in (
            (individual, 15, {"COMPATIVEL": 9, "DIVERGENTE": 36}),
            (self.analise, 3, {"COMPATIVEL": 9}),
        ):
            resultados = conferir_analise(self.banco, analise)
            self.assertEqual(Counter(r["resultado"] for r in resultados), esperados)
            dados = self.relatorio(analise)
            self.assertEqual(dados["total_pares_elegiveis"], pares)
            if analise == self.analise:
                vinculos = {p["pessoa_id"]: p["documentos_ids"] for p in dados["pessoas"]}
                for linha in dados["conferencias_estruturadas"]:
                    self.assertIn(linha["documento_1_id"], vinculos[linha["pessoa_id"]])
                    self.assertIn(linha["documento_2_id"], vinculos[linha["pessoa_id"]])

    def test_detecta_divergencias_reais_e_sinais_independentes(self):
        self.documento_atribuido()
        segundo = self.documento_atribuido(completo=False)
        salvar_campo_extraido(self.banco, segundo, "NOME", "OUTRO PERSONAGEM FICTICIO")
        self.documento()
        resultados = conferir_analise(self.banco, self.analise)
        self.assertEqual(Counter(r["resultado"] for r in resultados),
                         {"DIVERGENTE": 1, "NAO_CONFERIDO": 2})
        relatorio = self.relatorio()
        self.assertEqual(relatorio["estado_conferencia"], "COM_DIVERGENCIAS")
        self.assertTrue(relatorio["conferencia_parcial"])
        self.assertTrue(relatorio["aguardando_atribuicao"])

    def test_sem_pares_e_aguardando_atribuicao_sao_estados_distintos(self):
        self.documento_atribuido()
        self.documento_atribuido(self.integrante)
        self.assertEqual(conferir_analise(self.banco, self.analise), [])
        self.assertEqual(self.relatorio()["estado_conferencia"], "SEM_PARES_ELEGIVEIS")
        self.documento()
        self.assertEqual(self.relatorio()["estado_conferencia"], "AGUARDANDO_ATRIBUICAO")

    def test_filicao_da_certidao_nao_e_tratada_como_nome_do_registrado(self):
        certidao = adicionar_documento(
            self.banco, self.analise, "fixture.pdf", "fixtures/fixture.pdf", "CERTIDAO_NASCIMENTO",
        )
        identidade = self.documento_atribuido(completo=False)
        atribuicoes.atribuir_documento(self.banco, certidao, self.pessoa)
        for documento in (certidao, identidade):
            salvar_campo_extraido(self.banco, documento, "NOME", "PERSONAGEM FICTICIO")
        salvar_campo_extraido(self.banco, certidao, "FILIACAO", "OUTRO PERSONAGEM FICTICIO")
        resultados = {r["campo"]: r["resultado"] for r in conferir_analise(self.banco, self.analise)}
        self.assertEqual(resultados, {
            "NOME": "COMPATIVEL", "CPF": "NAO_CONFERIDO", "DATA_NASCIMENTO": "NAO_CONFERIDO",
        })

    def test_reconferencia_idempotente_e_indice_protege_par(self):
        primeiro = self.documento_atribuido()
        segundo = self.documento_atribuido()
        for _ in range(2):
            self.assertEqual(len(conferir_analise(self.banco, self.analise)), 3)
        with self.assertRaises(sqlite3.IntegrityError), self.banco:
            registrar_conferencia(
                self.banco, self.analise, "NOME", "DIVERGENTE", confirmar=False,
                pessoa_id=self.pessoa, documento_1_id=primeiro, documento_2_id=segundo,
            )
        self.assertEqual(self.relatorio()["total_conferencias"], 3)

    def test_versao_anterior_le_mas_nao_grava_lote_ou_conferencia_familiar(self):
        comando_lote_v0 = """
            INSERT INTO documentos (analise_id, nome_arquivo, caminho_origem,
                                    tipo_identificado, quantidade_paginas, status_leitura)
            VALUES (?, 'fixture.pdf', 'fixtures/fixture.pdf', NULL, 1, 'OK')
        """
        with self.assertRaises(sqlite3.IntegrityError), self.banco:
            self.banco.execute(comando_lote_v0, (self.analise,))
        self.documento_atribuido()
        self.documento_atribuido()
        conferir_analise(self.banco, self.analise)
        with self.assertRaises(sqlite3.IntegrityError), self.banco:
            self.banco.execute(
                "DELETE FROM conferencias WHERE analise_id = ? AND observacao LIKE 'AUTO:%'",
                (self.analise,),
            )
            self.banco.execute("""
                INSERT INTO conferencias (analise_id, campo_verificado, resultado, observacao)
                VALUES (?, 'NOME', 'DIVERGENTE', 'AUTO: registro antigo')
            """, (self.analise,))
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM documentos").fetchone()[0], 2)
        self.assertEqual(self.relatorio()["total_conferencias"], 3)

    def test_atribuicao_reverte_sem_perder_transacao_do_chamador(self):
        primeiro = self.documento_atribuido()
        self.documento_atribuido()
        conferir_analise(self.banco, self.analise)
        self.banco.execute("UPDATE analises SET responsavel = 'FIXTURE'")
        with self.assertRaises(sqlite3.IntegrityError):
            atribuicoes.marcar_documento_compartilhado(self.banco, primeiro, [self.pessoa, 999])
        self.assertTrue(self.banco.in_transaction)
        self.assertEqual(self.banco.execute("SELECT responsavel FROM analises").fetchone()[0], "FIXTURE")
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM conferencias").fetchone()[0], 3)
        self.banco.rollback()
        self.assertIsNone(self.banco.execute("SELECT responsavel FROM analises").fetchone()[0])
        self.assertEqual(self.relatorio()["pessoas"][0]["documentos_ids"][0], primeiro)

    def test_reconferencia_reverte_se_falhar_apos_primeiro_resultado(self):
        self.documento_atribuido()
        self.documento_atribuido()
        conferir_analise(self.banco, self.analise)
        registros = self.relatorio()["conferencias_estruturadas"]
        gravar = conferidor_banco.registrar_conferencia

        def falhar(**kwargs):
            if kwargs["campo_verificado"] == "CPF":
                raise sqlite3.OperationalError("Falha sintética")
            return gravar(**kwargs)

        with patch.object(conferidor_banco, "registrar_conferencia", side_effect=falhar):
            with self.assertRaises(sqlite3.OperationalError):
                conferir_analise(self.banco, self.analise)
        self.assertEqual(self.relatorio()["conferencias_estruturadas"], registros)

    def test_reconferencia_nao_confirma_trabalho_do_chamador(self):
        self.documento_atribuido()
        self.documento_atribuido()
        self.banco.execute("BEGIN IMMEDIATE")
        conferir_analise(self.banco, self.analise, confirmar=False)
        self.assertTrue(self.banco.in_transaction)
        self.banco.rollback()
        self.assertEqual(self.relatorio()["total_conferencias"], 0)

    def test_expiracao_durante_conferencia_reverte_resultados(self):
        self.documento_atribuido()
        self.documento_atribuido()
        gravar = conferidor_banco.registrar_conferencia

        def expirar(**kwargs):
            self.relogio.return_value = self.agora + timedelta(hours=24)
            return gravar(**kwargs)

        with patch.object(conferidor_banco, "registrar_conferencia", side_effect=expirar):
            with self.assertRaises(conexao.AnaliseIndisponivel):
                conferir_analise(self.banco, self.analise)
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM conferencias").fetchone()[0], 0)

    def test_fluxo_de_lote_familiar_aguarda_atribuicao_e_depois_confere(self):
        def leitura(arquivo, **_):
            return {
                "nome_arquivo": Path(arquivo).name, "caminho_origem": arquivo,
                "quantidade_paginas": 1,
                "paginas": [{
                    "numero": 1, "metodo": "DIGITAL", "linhas_ocr": [],
                    "texto": "CARTEIRA DE IDENTIDADE\\nNOME: PERSONAGEM FICTICIO\\n"
                             "DATA DE NASCIMENTO: 01/01/2000",
                }],
            }

        with TemporaryDirectory() as pasta:
            for id_arquivo in ("a", "b"):
                Path(pasta, id_arquivo + ".pdf").write_bytes(id_arquivo.encode("ascii"))
            with (
                patch.dict(os.environ, {"CEUMA_OCR_MODO": "paddle"}),
                patch.object(processador, "LeitorOCR"),
                patch.object(processador, "ler_pdf", side_effect=leitura),
            ):
                lote = processador.processar_pasta(self.banco, self.analise, pasta)

        documentos = [d["documento_id"] for d in lote["documentos"]]
        self.assertEqual(len(documentos), 2)
        self.assertEqual(lote["relatorio"]["documentos_inconclusivos"], documentos)
        self.assertEqual(lote["relatorio"]["estado_conferencia"], "AGUARDANDO_ATRIBUICAO")
        for documento in documentos:
            atribuicoes.atribuir_documento(self.banco, documento, self.pessoa)
        resultados = conferir_analise(self.banco, self.analise)
        self.assertEqual(len(resultados), 3)
        self.assertEqual(self.relatorio()["total_pares_elegiveis"], 1)

    def test_leitor_concorrente_so_ve_atribuicao_e_invalidacao_confirmadas_juntas(self):
        with TemporaryDirectory() as pasta:
            caminho = Path(pasta, "fixture.db")
            with closing(conexao.conectar_banco(caminho)) as escrita, closing(
                conexao.conectar_banco(caminho),
            ) as leitura:
                escrita.execute("PRAGMA journal_mode = WAL")
                conexao.criar_tabelas(escrita)
                conexao.criar_analise(escrita, modo="FAMILIAR")
                pessoa = atribuicoes.criar_pessoa(escrita, 1)
                primeiro = adicionar_documento(escrita, 1, "a.pdf", "fixtures/a.pdf")
                segundo = adicionar_documento(escrita, 1, "b.pdf", "fixtures/b.pdf")
                for documento in (primeiro, segundo):
                    atribuicoes.atribuir_documento(escrita, documento, pessoa)
                conferir_analise(escrita, 1)
                with escrita:
                    escrita.execute("UPDATE analises SET status = 'CONCLUIDA' WHERE id = 1")
                escrita.execute("BEGIN IMMEDIATE")
                atribuicoes.remover_atribuicao(escrita, primeiro)
                antes, _ = recuperar_relatorio(leitura, analise_id=1)
                self.assertEqual(antes["total_conferencias"], 3)
                self.assertEqual(antes["documentos_inconclusivos"], [])
                escrita.commit()
                depois, _ = recuperar_relatorio(leitura, analise_id=1)
                self.assertEqual(depois["total_conferencias"], 0)
                self.assertEqual(depois["documentos_inconclusivos"], [primeiro])

    def test_retencao_cascata_e_prazo_original(self):
        self.documento_atribuido()
        self.relogio.return_value = self.agora + timedelta(hours=23)
        self.documento_atribuido()
        conferir_analise(self.banco, self.analise)
        codigo = self.relatorio()["codigo"]
        self.assertEqual(conexao.consultar_expiracao_ativa(self.banco, codigo),
                         self.agora + timedelta(hours=24))
        self.assertEqual(conexao.limpar_analises_expiradas(self.banco), 0)
        self.relogio.return_value = self.agora + timedelta(hours=24)
        with self.assertRaises(conexao.AnaliseIndisponivel):
            conferir_analise(self.banco, self.analise)
        self.assertEqual(conexao.limpar_analises_expiradas(self.banco), 1)
        for tabela in ("analises", "documentos", "campos_extraidos", "conferencias",
                       "pessoas", "documentos_pessoas"):
            self.assertEqual(self.banco.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)
        self.assertEqual(self.banco.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_exportacao_estruturada_e_neutralizacao(self):
        primeiro = self.documento_atribuido(nome="=1+1.pdf")
        segundo = self.documento_atribuido()
        conferir_analise(self.banco, self.analise)
        relatorio = self.relatorio()
        with TemporaryDirectory() as pasta:
            destino = Path(pasta, "fixture.csv")
            exportador.exportar_analise_csv(self.banco, destino, analise_id=self.analise)
            self.assertTrue(destino.read_bytes().startswith(b"\xef\xbb\xbf"))
            with destino.open(encoding="utf-8-sig", newline="") as arquivo:
                linhas = list(csv.reader(arquivo, delimiter=";"))
            self.assertIn([str(primeiro), "'=1+1.pdf", "CIN"], linhas)
            self.assertIn([str(self.pessoa), "ESTUDANTE_TITULAR", str(segundo)], linhas)
            for x in relatorio["conferencias_estruturadas"]:
                self.assertIn([str(x["conferencia_id"]), str(self.pessoa), str(primeiro),
                               str(segundo), x["campo"], "COMPATIVEL"], linhas)
            with self.assertRaises(FileExistsError):
                exportador.exportar_analise_csv(self.banco, destino, analise_id=self.analise)

    def test_exportacao_aborta_se_expirar_ou_mudar_durante_gravacao(self):
        documento = self.documento_atribuido()
        self.documento_atribuido()
        conferir_analise(self.banco, self.analise)
        relatorio = self.relatorio()
        gravar = exportador._gravar_relatorio

        def alterar(dados, arquivo):
            gravar(dados, arquivo)
            atribuicoes.remover_atribuicao(self.banco, documento)

        def expirar(dados, arquivo):
            gravar(dados, arquivo)
            self.relogio.return_value = self.agora + timedelta(hours=24)

        with TemporaryDirectory() as pasta:
            destino = Path(pasta, "fixture.csv")
            with self.assertRaises(ValueError):
                exportador.exportar_relatorio_csv(relatorio, destino)
            for evento in (alterar, expirar):
                with patch.object(exportador, "_gravar_relatorio", side_effect=evento):
                    with self.assertRaises(RelatorioIndisponivel):
                        exportador.exportar_analise_csv(self.banco, destino, analise_id=self.analise)
                self.assertEqual(list(Path(pasta).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
