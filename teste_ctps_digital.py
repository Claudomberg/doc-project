import csv
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from analise.analisador_documento import analisar_documento
from analise.conferidor_banco import conferir_analise
from analise.exportador import exportar_analise_csv
from analise.extrator_campos import STATUS_CPF_TITULAR
from analise.persistencia import salvar_resultado_analise
from analise.relatorio import recuperar_relatorio
from banco import atribuicoes, conexao, itens_documentais
from banco.operacoes import adicionar_documento, salvar_campo_extraido
from documentos.identificador import identificar_documento
from teste_analise_familiar import cpf_sintetico
from teste_ctps_fisica import CAPA, QUALIFICACAO, documento


def dados_pessoais():
    return (f"CARTEIRA DE TRABALHO DIGITAL\nDADOS PESSOAIS\nNOME: TITULAR FICTICIO\n"
            f"CPF: {cpf_sintetico(1)}\nDATA DE NASCIMENTO: 01/01/2000")


def contrato(referencia, data):
    return (f"CONTRATO DE TRABALHO {referencia}\nEMPREGADOR: EMPRESA FICTICIA\n"
            f"ADMISSAO: {data}\nCARGO: FUNCAO FICTICIA")


class TesteRegrasCTPSDigital(unittest.TestCase):
    def resultado(self, *paginas):
        return analisar_documento(documento(*paginas))

    def test_identificacao_por_contexto_e_conflito(self):
        self.assertEqual(identificar_documento(dados_pessoais())["tipo"], "CTPS_DIGITAL")
        self.assertEqual(identificar_documento(CAPA + "\n" + QUALIFICACAO)["tipo"], "CTPS_FISICA")
        self.assertEqual(identificar_documento("PENDENTE ENVIAR CTPS DIGITAL")["tipo"],
                         "DOCUMENTO_DESCONHECIDO")
        self.assertEqual(identificar_documento(dados_pessoais() + "\n" + CAPA + "\n" + QUALIFICACAO)
                         ["status"], "AMBIGUO")
        for texto, tipo in (
            ("COMPROVANTE DE SOLICITACAO DA CTPS\nPROTOCOLO: 0001", "SOLICITACAO_CTPS"),
            ("DECLARACAO DE VINCULO\nEMPREGADOR: FICTICIO\nEMPREGADO: FICTICIO", "DECLARACAO_VINCULO"),
            ("CONTRATO INDIVIDUAL DE TRABALHO\nEMPREGADOR: A\nEMPREGADO: B\nCLAUSULA 1",
             "CONTRATO_TRABALHO_AVULSO"),
        ):
            self.assertEqual(identificar_documento(texto)["tipo"], tipo)

    def test_trabalhador_e_terceiros(self):
        r = self.resultado(dados_pessoais() + f"\nCPF DO REPRESENTANTE: {cpf_sintetico(2)}\n"
                           f"CPF: {cpf_sintetico(3)}", contrato("A1", "02/02/2021"))
        achados = r["campos"]["ctps_digital"]
        self.assertEqual(achados["CPF"]["valor"], cpf_sintetico(1))
        self.assertEqual(achados["DATA_NASCIMENTO"]["valor"], "01/01/2000")
        self.assertEqual(set(achados), {"NOME", "CPF", "DATA_NASCIMENTO"})

    def test_contratos_distintos_e_desligamento_ausente(self):
        r = self.resultado(dados_pessoais(),
                           contrato("A1", "01/01/2020") + "\nSITUACAO: ATIVO\n" + contrato("A2", "01/01/2022"),
                           contrato("A3", "01/01/2023"))
        cs = [x["campos"] for x in r["ctps_digital"]["contratos"]]
        self.assertEqual([c["ADMISSAO"]["valor"] for c in cs], ["01/01/2020", "01/01/2022", "01/01/2023"])
        self.assertTrue(all(c["DESLIGAMENTO"]["status"] == "NAO_ENCONTRADO" for c in cs))
        self.assertEqual([c["SITUACAO"]["valor"] for c in cs], ["ATIVO", None, None])
        self.assertEqual([c["EMPREGADOR"]["valor"] for c in cs], ["EMPRESA FICTICIA"] * 3)

    def test_empregadores_separados_no_mesmo_bloco_explicito(self):
        r = self.resultado(dados_pessoais(), "CONTRATOS DE TRABALHO\n"
            "EMPREGADOR: EMPRESA A\nADMISSAO: 01/01/2020\nEMPREGADOR: EMPRESA B\nADMISSAO: 01/01/2022")
        itens = r["ctps_digital"]["contratos"]
        self.assertEqual(len(itens), 2)
        self.assertEqual([x["campos"]["EMPREGADOR"]["valor"] for x in itens], ["EMPRESA A", "EMPRESA B"])

    def test_continuacao_explicita_e_fora_de_ordem(self):
        base = contrato("A1", "01/01/2020")
        mais = "CONTINUACAO DO CONTRATO DE TRABALHO A1\nDESLIGAMENTO: 02/02/2022"
        r = self.resultado(dados_pessoais(), base, mais)
        cs = r["ctps_digital"]["contratos"]
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["campos"]["DESLIGAMENTO"]["pagina_origem"], 3)
        r = self.resultado(dados_pessoais(), base, "", mais)
        self.assertEqual(len(r["ctps_digital"]["contratos"]), 2)
        self.assertTrue(r["ctps_digital"]["contratos"][1]["continuacao_inconclusiva"])
        self.assertIn("CONTRATO_REQUER_REVISAO", r["pendencias_leitura"])

    def test_datas_conflitantes_e_anotacoes(self):
        r = self.resultado(dados_pessoais(), contrato("A1", "01/01/2020") +
                           "\nADMISSAO: 02/02/2022\nANOTACOES POSTERIORES\nDESLIGAMENTO: 02/02/2024")
        campos = r["ctps_digital"]["contratos"][0]["campos"]
        self.assertEqual(campos["ADMISSAO"]["status"], "AMBIGUO")
        self.assertIsNone(campos["ADMISSAO"]["valor"])
        self.assertIsNone(campos["DESLIGAMENTO"]["valor"])

    def test_identificadores_empresariais_e_datas_sem_bloco(self):
        r = self.resultado(dados_pessoais(), contrato("A1", "01/01/2020") +
                           "\nCNPJ: 00.000.000/0001-00\nCAEPF: 00012345")
        c = r["ctps_digital"]["contratos"][0]["campos"]
        self.assertEqual(c["CNPJ"]["valor"], "00000000000100")
        self.assertEqual(c["IDENTIFICADOR_EMPREGADOR"]["status"], "EVIDENCIA_INSUFICIENTE")
        self.assertEqual(c["IDENTIFICADOR_EMPREGADOR"]["valor"], "00012345")
        r = self.resultado(dados_pessoais(), "DATA DE ADMISSAO: 01/01/2020")
        self.assertEqual(r["ctps_digital"]["contratos"], [])
        self.assertIn("CAMPO_CONTRATUAL_SEM_BLOCO", r["pendencias_leitura"])


class TesteLayoutHipotetico(unittest.TestCase):
    def test_duas_colunas_com_valores_em_caixas_separadas(self):
        d = documento(dados_pessoais(), "")
        linhas = [
            ("CONTRATO A1", [0, 10, 80, 20]), ("CONTRATO A2", [150, 10, 250, 20]),
            ("EMPREGADOR:", [0, 30, 60, 40]), ("FICTICIO A", [64, 30, 92, 40]),
            ("EMPREGADOR:", [150, 30, 210, 40]), ("FICTICIO B", [214, 30, 246, 40]),
            ("ADMISSAO: 01/01/2020", [0, 55, 90, 65]), ("ADMISSAO: 01/01/2022", [150, 55, 248, 65]),
        ]
        d["paginas"][1]["linhas"] = [{"texto": t, "caixa": c} for t, c in linhas]
        cs = analisar_documento(d)["ctps_digital"]["contratos"]
        self.assertEqual([(c["campos"]["EMPREGADOR"]["valor"], c["campos"]["ADMISSAO"]["valor"]) for c in cs],
                         [("FICTICIO A", "01/01/2020"), ("FICTICIO B", "01/01/2022")])


class TestePersistenciaCTPSDigital(unittest.TestCase):
    def setUp(self):
        self.b = self.enterContext(closing(conexao.conectar_banco(":memory:")))
        conexao.criar_tabelas(self.b)
        self.resultado = analisar_documento(documento(
            dados_pessoais(), contrato("A1", "01/01/2020"), contrato("A2", "01/01/2022"),
        ))

    def test_rollback_atualiza_documento_e_itens_atomicamente(self):
        conexao.criar_analise(self.b)
        real = itens_documentais.criar_item_documental
        def falha(banco, doc, tipo, ordem, **kwargs):
            if ordem == 2:
                raise sqlite3.IntegrityError("fixture")
            return real(banco, doc, tipo, ordem, **kwargs)
        with patch("analise.persistencia.criar_item_documental", side_effect=falha):
            with self.assertRaises(sqlite3.IntegrityError):
                salvar_resultado_analise(self.b, 1, self.resultado)
        for tabela in ("documentos", "itens_documentais", "campos_item", "campos_extraidos"):
            self.assertEqual(self.b.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)

    def test_csv_neutraliza_formula_no_cargo_do_item(self):
        codigo = conexao.criar_analise(self.b)
        resultado = analisar_documento(documento(
            dados_pessoais(), contrato("A1", "01/01/2020").replace(
                "FUNCAO FICTICIA", '=SOMA(1;2)',
            ),
        ))
        salvar_resultado_analise(self.b, 1, resultado)
        with self.b:
            self.b.execute("UPDATE analises SET status='CONCLUIDA'")
        with TemporaryDirectory() as pasta:
            destino = Path(pasta, "fixture.csv")
            exportar_analise_csv(self.b, destino, codigo=codigo)
            with destino.open(encoding="utf-8-sig", newline="") as arquivo:
                self.assertIn(["1", "CARGO", "'=SOMA(1;2)", "ENCONTRADO", "2", ""],
                              list(csv.reader(arquivo, delimiter=";")))

    def test_recuperacao_conferencia_csv_e_reatribuicao(self):
        for modo in ("INDIVIDUAL", "FAMILIAR"):
            with self.subTest(modo=modo):
                codigo = conexao.criar_analise(self.b, modo=modo)
                analise = self.b.execute("SELECT id FROM analises WHERE codigo=?", (codigo,)).fetchone()[0]
                ctps = salvar_resultado_analise(self.b, analise, self.resultado)["documento_id"]
                outros = []
                for tipo in ("DOCUMENTO_IDENTIDADE", "CIN", "CNH", "CERTIDAO_NASCIMENTO", "CTPS_FISICA"):
                    ident = adicionar_documento(self.b, analise, "fixture.pdf", "fixtures/fixture.pdf", tipo)
                    outros.append(ident)
                    for c, v, s in (("NOME", "TITULAR FICTICIO", "ENCONTRADO"),
                                    ("CPF", cpf_sintetico(1), STATUS_CPF_TITULAR),
                                    ("DATA_NASCIMENTO", "01/01/2000", "ENCONTRADO")):
                        salvar_campo_extraido(self.b, ident, c, v, status=s)
                if modo == "FAMILIAR":
                    pessoa = atribuicoes.criar_pessoa(self.b, analise)
                    outra = atribuicoes.criar_pessoa(self.b, analise)
                    atribuicoes.atribuir_documento(self.b, ctps, pessoa)
                    for ident in outros:
                        atribuicoes.atribuir_documento(self.b, ident, pessoa)
                    nada = adicionar_documento(self.b, analise, "outra.pdf", "fixtures/outra.pdf")
                    atribuicoes.atribuir_documento(self.b, nada, outra)
                res = [r for r in conferir_analise(self.b, analise)
                       if ctps in (r["documento_1_id"], r["documento_2_id"])]
                self.assertEqual({(x["campo"], x["resultado"]) for x in res}, {
                    ("NOME", "COMPATIVEL"), ("CPF", "COMPATIVEL"), ("DATA_NASCIMENTO", "COMPATIVEL"),
                })
                self.assertEqual(len(res), 15)
                with self.b:
                    self.b.execute("UPDATE analises SET status='CONCLUIDA' WHERE id=?", (analise,))
                rel, _ = recuperar_relatorio(self.b, codigo)
                self.assertEqual(len(rel["itens_documentais"]), 2)
                item = rel["itens_documentais"][0]["item_id"]
                with TemporaryDirectory() as pasta:
                    dest = Path(pasta, "fixture.csv")
                    exportar_analise_csv(self.b, dest, codigo=codigo)
                    with dest.open(encoding="utf-8-sig", newline="") as arquivo:
                        self.assertIn([str(item), "ADMISSAO", "01/01/2020", "ENCONTRADO", "2", ""],
                                      list(csv.reader(arquivo, delimiter=";")))
                if modo == "FAMILIAR":
                    itens_documentais.alterar_atribuicao_item(self.b, item, pessoa)
                    atribuicoes.atribuir_documento(self.b, ctps, outra)
                    atual, _ = recuperar_relatorio(self.b, codigo)
                    self.assertIsNone(atual["itens_documentais"][0]["pessoa_id"])
                    self.assertFalse(any(ctps in (r["documento_1_id"], r["documento_2_id"])
                                         for r in atual["conferencias_estruturadas"]))
        self.assertEqual(self.b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.b.execute("PRAGMA foreign_key_check").fetchall(), [])
