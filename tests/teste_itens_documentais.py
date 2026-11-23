import csv
import sqlite3
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ceuma.relatorios import exportador_csv as exportador
from ceuma.relatorios import relatorio
from ceuma.banco import atribuicoes
from ceuma.banco import conexao
from ceuma.banco import esquema
from ceuma.banco import itens_documentais as itens
from ceuma.banco.operacoes import adicionar_documento, salvar_campo_extraido
from teste_analise_familiar import _ESQUEMA_ANTERIOR


def campos_contrato(empregador, admissao, *, desligamento=None, cargo=None):
    return [
        {"nome_campo": "EMPREGADOR", "valor": empregador, "pagina_origem": 2},
        {"nome_campo": "CNPJ", "valor": "00000000000000"},
        {"nome_campo": "ADMISSAO", "valor": admissao},
        {"nome_campo": "DESLIGAMENTO", "valor": desligamento,
         "status": "ENCONTRADO" if desligamento else "NAO_ENCONTRADO"},
        {"nome_campo": "CARGO", "valor": cargo,
         "status": "ENCONTRADO" if cargo else "AMBIGUO"},
    ]


class TesteMigracaoItens(unittest.TestCase):
    def banco_legado(self, versao):
        banco = conexao.conectar_banco(":memory:")
        self.addCleanup(banco.close)
        banco.executescript(_ESQUEMA_ANTERIOR)
        agora = conexao.agora_utc()
        banco.execute("""
            INSERT INTO analises (codigo, data_criacao, data_expiracao, status)
            VALUES ('FIXTURE', ?, ?, 'CONCLUIDA')
        """, (agora.isoformat(), (agora + timedelta(hours=24)).isoformat()))
        banco.execute("""
            INSERT INTO documentos (analise_id, nome_arquivo, caminho_origem, tipo_identificado)
            VALUES (1, 'fixture.pdf', 'fixtures/fixture.pdf', 'CIN')
        """)
        if versao == 1:
            for sql in esquema._MIGRACAO_1:
                banco.execute(sql)
            banco.execute("PRAGMA user_version = 1")
        banco.commit()
        return banco

    def test_migracoes_0_e_1_recuperam_historico_e_sao_idempotentes(self):
        for versao in (0, 1):
            with self.subTest(versao=versao):
                banco = self.banco_legado(versao)
                salvar_campo_extraido(banco, 1, "CPF", None, status="AMBIGUO")
                conexao.criar_tabelas(banco)
                self.assertEqual(banco.execute("PRAGMA user_version").fetchone()[0], 2)
                dados, _ = relatorio.recuperar_relatorio(banco, "FIXTURE")
                self.assertEqual(dados["itens_documentais"], [])
                self.assertEqual(dados["campos_extraidos"][0]["status"], "AMBIGUO")
                self.assertIn({"documento_id": 1, "campo": "CPF", "status": "AMBIGUO"},
                              dados["estados_campos"])
                self.assertEqual(dados["pessoas"], [])
                antes = list(banco.iterdump())
                conexao.criar_tabelas(banco)
                self.assertEqual(list(banco.iterdump()), antes)
                self.assertEqual(banco.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(banco.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migracao_preserva_atribuicoes_familiares_v1(self):
        banco = self.banco_legado(1)
        codigo = conexao.criar_analise(banco, modo="FAMILIAR")
        pessoa = atribuicoes.criar_pessoa(banco, 2, "ESTUDANTE_TITULAR")
        doc = adicionar_documento(banco, 2, "fixture.pdf", "fixtures/fixture.pdf")
        atribuicoes.atribuir_documento(banco, doc, pessoa)
        with banco:
            banco.execute("UPDATE analises SET status = 'CONCLUIDA' WHERE id = 2")
        conexao.criar_tabelas(banco)
        dados, _ = relatorio.recuperar_relatorio(banco, codigo)
        self.assertEqual(dados["pessoas"], [{
            "pessoa_id": pessoa, "papel": "ESTUDANTE_TITULAR", "documentos_ids": [doc],
        }])
        self.assertEqual(dados["itens_documentais"], [])

    def test_falha_na_v2_reverte_ddl_e_versao_de_origem(self):
        for versao in (0, 1):
            with self.subTest(versao=versao):
                banco = self.banco_legado(versao)
                antes = list(banco.iterdump())

                def negar(acao, nome, *_):
                    return (sqlite3.SQLITE_DENY
                            if acao == sqlite3.SQLITE_CREATE_TABLE and nome == "campos_item"
                            else sqlite3.SQLITE_OK)

                banco.set_authorizer(negar)
                try:
                    with self.assertRaises(sqlite3.DatabaseError):
                        conexao.criar_tabelas(banco)
                finally:
                    banco.set_authorizer(None)
                self.assertFalse(banco.in_transaction)
                self.assertEqual(banco.execute("PRAGMA user_version").fetchone()[0], versao)
                self.assertEqual(list(banco.iterdump()), antes)
                conexao.criar_tabelas(banco)

    def test_abertura_rejeita_versoes_desconhecidas(self):
        with TemporaryDirectory() as pasta:
            caminho = Path(pasta, "fixture.db")
            for versao in (-1, 3):
                with closing(sqlite3.connect(caminho)) as banco:
                    banco.execute(f"PRAGMA user_version = {versao}")
                with self.assertRaisesRegex(sqlite3.DatabaseError, "incompatível"):
                    conexao.conectar_banco(caminho)


class TesteItensDocumentais(unittest.TestCase):
    def setUp(self):
        pasta = Path(self.enterContext(TemporaryDirectory()))
        self.destino = pasta / "fixture.csv"
        self.caminho = pasta / "fixture.db"
        self.banco = self.enterContext(closing(conexao.conectar_banco(self.caminho)))
        self.banco.execute("PRAGMA journal_mode = WAL")
        conexao.criar_tabelas(self.banco)
        self.agora = conexao.agora_utc().replace(microsecond=0)
        self.relogio = self.enterContext(patch.object(conexao, "agora_utc", return_value=self.agora))
        self.analise, self.codigo, self.doc = self.criar_documento()

    def criar_documento(self, modo="INDIVIDUAL"):
        codigo = conexao.criar_analise(self.banco, modo=modo)
        analise = self.banco.execute("SELECT id FROM analises WHERE codigo = ?", (codigo,)).fetchone()[0]
        doc = adicionar_documento(self.banco, analise, "fixture.pdf", "fixtures/fixture.pdf", "CTPS")
        with self.banco:
            self.banco.execute("UPDATE analises SET status = 'CONCLUIDA' WHERE id = ?", (analise,))
        return analise, codigo, doc

    def criar_item(self, doc=None, ordem=1, **opcoes):
        return itens.criar_item_documental(self.banco, doc or self.doc, "VINCULO", ordem, **opcoes)

    def dados(self, codigo=None):
        return relatorio.recuperar_relatorio(self.banco, codigo or self.codigo)[0]

    @staticmethod
    def valores(dados):
        return {i["item_id"]: {c["nome_campo"]: c["valor"] for c in i["campos"]} for i in dados}

    def test_tres_vinculos_preservam_empregadores_periodos_e_estados_no_csv(self):
        contratos = [
            campos_contrato("EMPRESA FICTICIA A", "01/01/2020", desligamento="31/12/2020", cargo="FUNCAO A"),
            campos_contrato("EMPRESA FICTICIA B", "01/02/2021", cargo="FUNCAO B"),
            campos_contrato("EMPRESA FICTICIA A", "01/03/2022"),
        ]
        ids = [self.criar_item(ordem=n, campos=campos) for n, campos in enumerate(contratos, 1)]
        esperado = {i: {c["nome_campo"]: c["valor"] for c in campos}
                    for i, campos in zip(ids, contratos)}
        with closing(conexao.conectar_banco(self.caminho)) as recuperacao:
            dados, _ = relatorio.recuperar_relatorio(recuperacao, self.codigo)
            exportador.exportar_analise_csv(recuperacao, self.destino, codigo=self.codigo)
        itens_lidos = dados["itens_documentais"]
        self.assertEqual(self.valores(itens_lidos), esperado)
        self.assertEqual(itens.listar_itens_por_documento(self.banco, self.doc), itens_lidos)
        self.assertEqual([r["pessoa_id"] for r in itens_lidos], [None, None, None])
        self.assertEqual(dados["campos_extraidos"], [])
        self.assertEqual(dados["estados_campos"], [])
        self.assertEqual(dados["conferencias"], [])
        with self.destino.open(encoding="utf-8-sig", newline="") as arquivo:
            linhas = list(csv.reader(arquivo, delimiter=";"))
        for ordem, ident in enumerate(ids, 1):
            self.assertIn([str(ident), str(self.doc), "VINCULO", str(ordem), "", "PENDENTE"], linhas)
            for campo in contratos[ordem - 1]:
                self.assertIn([str(ident), campo["nome_campo"], campo["valor"] or "", campo.get(
                    "status", "ENCONTRADO",
                ), str(campo.get("pagina_origem", "")), ""], linhas)

        # As mesmas datas e empregadores, mas emparelhados de outra forma, são outro relatório.
        for ident, admissao in zip(ids, ("01/02/2021", "01/01/2020")):
            itens.registrar_campos_item(self.banco, ident, [{"nome_campo": "ADMISSAO", "valor": admissao}])
        trocados = self.valores(self.dados()["itens_documentais"])
        self.assertCountEqual([r["ADMISSAO"] for r in trocados.values()],
                              [r["ADMISSAO"] for r in esperado.values()])
        self.assertNotEqual(trocados, esperado)
        self.assertEqual(trocados[ids[0]]["EMPREGADOR"], esperado[ids[0]]["EMPREGADOR"])

    def test_mesmo_nome_e_ordem_em_documentos_distintos(self):
        outro = adicionar_documento(self.banco, self.analise, "fixture.pdf", "fixtures/outra.pdf")
        primeiro, segundo = self.criar_item(), self.criar_item(outro)
        self.assertNotEqual(primeiro, segundo)
        with self.assertRaises(sqlite3.IntegrityError):
            self.criar_item()
        self.assertEqual([r["documento_id"] for r in self.dados()["itens_documentais"]],
                         [self.doc, outro])

    def test_integridade_escopo_e_processos(self):
        analise, _, doc = self.criar_documento("FAMILIAR")
        outras, _, _ = self.criar_documento("FAMILIAR")
        pessoa = atribuicoes.criar_pessoa(self.banco, analise)
        pessoa_outra = atribuicoes.criar_pessoa(self.banco, outras)
        with self.assertRaises(sqlite3.IntegrityError):
            self.criar_item(doc, pessoa_id=pessoa)
        atribuicoes.marcar_documento_compartilhado(self.banco, doc)
        with self.assertRaises(sqlite3.IntegrityError):
            self.criar_item(doc, pessoa_id=pessoa)
        atribuicoes.marcar_documento_compartilhado(self.banco, doc, [pessoa])
        with self.assertRaises(sqlite3.IntegrityError):
            self.criar_item(doc, pessoa_id=pessoa_outra)
        com_vinculo = self.criar_item(doc, pessoa_id=pessoa)
        sem_vinculo = self.criar_item(doc, ordem=2)
        self.assertEqual([r["item_id"] for r in itens.listar_itens_por_pessoa(self.banco, analise, pessoa)],
                         [com_vinculo])
        self.assertNotEqual(com_vinculo, sem_vinculo)
        with self.assertRaises(ValueError):
            itens.listar_itens_por_pessoa(self.banco, outras, pessoa)
        with self.assertRaises(sqlite3.IntegrityError), self.banco:
            self.banco.execute("""
                INSERT INTO itens_documentais (analise_id, documento_id, tipo, ordem)
                VALUES (?, ?, 'VINCULO', 99)
            """, (outras, doc))
        self.assertEqual(self.banco.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_reatribuicao_documental_libera_apenas_vinculo_removido(self):
        analise, codigo, doc = self.criar_documento("FAMILIAR")
        antiga = atribuicoes.criar_pessoa(self.banco, analise)
        atual = atribuicoes.criar_pessoa(self.banco, analise)
        atribuicoes.marcar_documento_compartilhado(self.banco, doc, [antiga, atual])
        primeiro = self.criar_item(doc, pessoa_id=antiga, estado_revisao="REVISADO")
        self.criar_item(doc, ordem=2, pessoa_id=atual, estado_revisao="REVISADO")
        atribuicoes.atribuir_documento(self.banco, doc, atual)
        dados = self.dados(codigo)["itens_documentais"]
        self.assertEqual([(r["pessoa_id"], r["estado_revisao"]) for r in dados],
                         [(None, "PENDENTE"), (atual, "REVISADO")])
        itens.alterar_atribuicao_item(self.banco, primeiro, atual)
        self.assertEqual(self.dados(codigo)["itens_documentais"][0]["pessoa_id"], atual)
        itens.alterar_atribuicao_item(self.banco, primeiro, None)
        self.assertIsNone(self.dados(codigo)["itens_documentais"][0]["pessoa_id"])
        atribuicoes.marcar_documento_inconclusivo(self.banco, doc)
        self.assertTrue(all(r["pessoa_id"] is None for r in self.dados(codigo)["itens_documentais"]))

    def test_falha_reatribuicao_reverte_invalidacao_dos_itens(self):
        analise, codigo, doc = self.criar_documento("FAMILIAR")
        pessoa = atribuicoes.criar_pessoa(self.banco, analise)
        atribuicoes.atribuir_documento(self.banco, doc, pessoa)
        self.criar_item(doc, pessoa_id=pessoa, estado_revisao="REVISADO")
        antes = self.dados(codigo)
        with self.assertRaises(sqlite3.IntegrityError):
            atribuicoes.atribuir_documento(self.banco, doc, 999)
        self.assertEqual(self.dados(codigo), antes)

    def test_campos_e_itens_nao_podem_mudar_de_origem(self):
        primeiro = self.criar_item(campos=[{"nome_campo": "ORIGEM"}])
        segundo = self.criar_item(ordem=2)
        outro = adicionar_documento(self.banco, self.analise, "fixture.pdf", "fixtures/outro.pdf")
        with self.assertRaises(sqlite3.IntegrityError), self.banco:
            self.banco.execute("UPDATE itens_documentais SET documento_id = ? WHERE id = ?",
                               (outro, primeiro))
        with self.assertRaises(sqlite3.IntegrityError), self.banco:
            self.banco.execute("UPDATE campos_item SET item_id = ? WHERE item_id = ?", (segundo, primeiro))

    def test_atualizacao_remocao_e_cascata_de_documento(self):
        analise, codigo, doc = self.criar_documento("FAMILIAR")
        pessoa = atribuicoes.criar_pessoa(self.banco, analise)
        atribuicoes.atribuir_documento(self.banco, doc, pessoa)
        primeiro = self.criar_item(doc, pessoa_id=pessoa, campos=[{"nome_campo": "EXEMPLO"}])
        self.criar_item(doc, ordem=2, pessoa_id=pessoa, campos=[{"nome_campo": "EXEMPLO"}])
        itens.atualizar_item_documental(self.banco, primeiro, ordem=3, estado_revisao="REVISADO")
        self.assertEqual([r["ordem"] for r in self.dados(codigo)["itens_documentais"]], [2, 3])
        itens.remover_item_documental(self.banco, primeiro)
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM campos_item").fetchone()[0], 1)
        with self.banco:
            self.banco.execute("DELETE FROM documentos WHERE id = ?", (doc,))
        self.assertEqual(self.dados(codigo)["itens_documentais"], [])
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM campos_item").fetchone()[0], 0)
        self.assertEqual(self.banco.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_falha_campos_reverte_item_e_preserva_transacao_do_chamador(self):
        self.banco.execute("UPDATE analises SET responsavel = 'FIXTURE'")
        campos = [{"nome_campo": "VALIDO"}, {"nome_campo": "INVALIDO", "pagina_origem": -1}]
        with self.assertRaises(sqlite3.IntegrityError):
            self.criar_item(campos=campos)
        self.assertTrue(self.banco.in_transaction)
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM itens_documentais").fetchone()[0], 0)
        self.assertEqual(self.banco.execute("SELECT responsavel FROM analises").fetchone()[0], "FIXTURE")
        self.banco.rollback()
        self.assertIsNone(self.banco.execute("SELECT responsavel FROM analises").fetchone()[0])
        ident = self.criar_item(campos=[{"nome_campo": "VALIDO", "valor": "ORIGINAL"}])
        with self.assertRaises(sqlite3.IntegrityError):
            itens.registrar_campos_item(self.banco, ident, [
                {"nome_campo": "VALIDO", "valor": "ALTERADO"}, campos[1],
            ])
        self.assertEqual(self.dados()["itens_documentais"][0]["campos"][0]["valor"], "ORIGINAL")

    def test_restricao_ao_tamanho_da_evidencia(self):
        for trecho in ("x" * 161, "x" + chr(0) + "y" * 161, b"fixture"):
            with self.subTest(trecho=trecho), self.assertRaises(sqlite3.IntegrityError):
                self.criar_item(campos=[{"nome_campo": "EXEMPLO", "trecho_origem": trecho}])
        self.assertEqual(self.dados()["itens_documentais"], [])

    def test_expiracao_no_meio_da_escrita_reverte_item(self):
        def campos():
            yield {"nome_campo": "EXEMPLO", "valor": "FIXTURE"}
            self.relogio.return_value = self.agora + timedelta(hours=24)

        with self.assertRaises(conexao.AnaliseIndisponivel):
            self.criar_item(campos=campos())
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM itens_documentais").fetchone()[0], 0)
        self.assertFalse(self.banco.in_transaction)

    def test_retencao_original_exclui_itens_e_bloqueia_operacoes_expiradas(self):
        analise, codigo, doc = self.criar_documento("FAMILIAR")
        pessoa = atribuicoes.criar_pessoa(self.banco, analise)
        atribuicoes.atribuir_documento(self.banco, doc, pessoa)
        self.relogio.return_value = self.agora + timedelta(hours=23)
        ident = self.criar_item(doc, pessoa_id=pessoa, campos=[{"nome_campo": "EXEMPLO"}])
        prazo = self.agora + timedelta(hours=24)
        self.assertEqual(conexao.consultar_expiracao_ativa(self.banco, codigo), prazo)
        self.assertEqual(conexao.limpar_analises_expiradas(self.banco), 0)
        self.relogio.return_value = prazo
        for operacao in (
            lambda: itens.atualizar_item_documental(self.banco, ident, ordem=2),
            lambda: itens.registrar_campos_item(self.banco, ident, [{"nome_campo": "OUTRO"}]),
            lambda: itens.alterar_atribuicao_item(self.banco, ident, None),
            lambda: itens.remover_item_documental(self.banco, ident),
            lambda: itens.listar_itens_por_documento(self.banco, doc),
            lambda: self.criar_item(doc, ordem=2),
        ):
            with self.assertRaises(conexao.AnaliseIndisponivel):
                operacao()
        self.assertEqual(conexao.limpar_analises_expiradas(self.banco), 2)
        for tabela in ("documentos", "pessoas", "documentos_pessoas", "itens_documentais", "campos_item"):
            self.assertEqual(self.banco.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)
        self.assertEqual(self.banco.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.banco.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_csv_neutraliza_formula_e_preserva_evidencia_acentos_aspas_e_zeros(self):
        ident = self.criar_item(campos=[
            {"nome_campo": "EXEMPLO", "valor": "  =1+1", "trecho_origem": "\t=2+2"},
            {"nome_campo": "CNPJ", "valor": "00000000000000"},
            {"nome_campo": "DESCRICAO", "valor": 'Função; "A"'},
        ])
        with self.assertRaises(ValueError):
            exportador.exportar_relatorio_csv(self.dados(), self.destino)
        exportador.exportar_analise_csv(self.banco, self.destino, codigo=self.codigo)
        with self.destino.open(encoding="utf-8-sig", newline="") as arquivo:
            linhas = list(csv.reader(arquivo, delimiter=";"))
        self.assertIn([str(ident), "EXEMPLO", "'  =1+1", "ENCONTRADO", "", "'\t=2+2"], linhas)
        self.assertIn([str(ident), "CNPJ", "00000000000000", "ENCONTRADO", "", ""], linhas)
        self.assertIn([str(ident), "DESCRICAO", 'Função; "A"', "ENCONTRADO", "", ""], linhas)
        self.assertEqual(self.valores(self.dados()["itens_documentais"])[ident]["EXEMPLO"], "  =1+1")

    def test_csv_bloqueia_item_acrescentado_durante_recuperacao_nos_dois_modos(self):
        ler = relatorio.projetar_itens_analise
        for modo in ("INDIVIDUAL", "FAMILIAR"):
            with self.subTest(modo=modo):
                _, codigo, doc = self.criar_documento(modo)

                def ler_e_inserir(con, analise_id):
                    snapshot = ler(con, analise_id)
                    if not snapshot:
                        with closing(conexao.conectar_banco(self.caminho)) as outra:
                            itens.criar_item_documental(outra, doc, "VINCULO", 1)
                    return snapshot

                with patch.object(relatorio, "projetar_itens_analise", side_effect=ler_e_inserir):
                    with self.assertRaisesRegex(relatorio.RelatorioIndisponivel, "mudou"):
                        exportador.exportar_analise_csv(self.banco, self.destino, codigo=codigo)
                self.assertFalse(self.destino.exists())
                self.assertEqual(len(self.dados(codigo)["itens_documentais"]), 1)

    def test_csv_retem_destino_ao_mudar_itens_ou_expirar_nos_dois_modos(self):
        gravar = exportador._gravar_relatorio
        self.destino.write_bytes(b"fixture anterior")
        for modo in ("INDIVIDUAL", "FAMILIAR"):
            with self.subTest(modo=modo):
                self.relogio.return_value = self.agora
                _, codigo, doc = self.criar_documento(modo)
                ident = self.criar_item(doc)

                def gravar_e_alterar(dados, arquivo):
                    gravar(dados, arquivo)
                    itens.atualizar_item_documental(self.banco, ident, estado_revisao="REVISADO")

                def gravar_e_expirar(dados, arquivo):
                    gravar(dados, arquivo)
                    self.relogio.return_value = self.agora + timedelta(hours=24)

                for evento in (gravar_e_alterar, gravar_e_expirar):
                    with patch.object(exportador, "_gravar_relatorio", side_effect=evento):
                        with self.assertRaises(relatorio.RelatorioIndisponivel):
                            exportador.exportar_analise_csv(
                                self.banco, self.destino, codigo=codigo,
                                destino_confirmado=exportador.identificar_destino_csv(self.destino),
                            )
                    self.assertEqual(self.destino.read_bytes(), b"fixture anterior")
                    self.assertEqual(list(self.destino.parent.glob(".ceuma-exportacao-*")), [])
                    self.assertFalse(self.banco.in_transaction)

    def test_expiracao_entre_reconfirmar_destino_e_publicar_preserva_csv(self):
        self.criar_item()
        self.destino.write_bytes(b"fixture anterior")
        confirmado = exportador.identificar_destino_csv(self.destino)
        conferir = exportador._conferir_destino

        def conferir_e_expirar(destino, esperado):
            conferir(destino, esperado)
            if self.banco.in_transaction:
                self.relogio.return_value = self.agora + timedelta(hours=24)

        with patch.object(exportador, "_conferir_destino", side_effect=conferir_e_expirar):
            with self.assertRaises(relatorio.RelatorioIndisponivel):
                exportador.exportar_analise_csv(
                    self.banco, self.destino, codigo=self.codigo, destino_confirmado=confirmado,
                )
        self.assertEqual(self.destino.read_bytes(), b"fixture anterior")
        self.assertEqual(list(self.destino.parent.glob(".ceuma-exportacao-*")), [])

    def test_publicacao_reserva_escrita_ate_renomear(self):
        self.criar_item()
        publicar = exportador._publicar_sem_sobrescrever

        def publicar_com_concorrente(temporario, destino):
            with closing(sqlite3.connect(self.caminho, timeout=0)) as outra:
                with self.assertRaises(sqlite3.OperationalError) as bloqueio:
                    outra.execute("BEGIN IMMEDIATE")
                self.assertEqual(bloqueio.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
            publicar(temporario, destino)

        with patch.object(exportador, "_publicar_sem_sobrescrever", side_effect=publicar_com_concorrente):
            exportador.exportar_analise_csv(self.banco, self.destino, codigo=self.codigo)
        self.assertTrue(self.destino.exists())
        self.assertFalse(self.banco.in_transaction)


if __name__ == "__main__":
    unittest.main()
