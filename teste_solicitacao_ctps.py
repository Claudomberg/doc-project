import csv
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from analise.analisador_documento import analisar_documento
from analise.conferidor_banco import conferir_analise
from analise.exportador import exportar_analise_csv
from analise.extrator_campos import STATUS_CPF_TITULAR
from analise.persistencia import salvar_resultado_analise
from analise.relatorio import recuperar_relatorio
from banco import atribuicoes, conexao
from banco.operacoes import adicionar_documento, salvar_campo_extraido
from documentos.identificador import identificar_documento
from teste_analise_familiar import cpf_sintetico
from teste_ctps_digital import dados_pessoais
from teste_ctps_fisica import CAPA, QUALIFICACAO, documento


TITULO = "COMPROVANTE DE SOLICITACAO DA CTPS"


def comprovante():
    return (f"{TITULO}\nPROTOCOLO: 000123-A\nDADOS DO SOLICITANTE\n"
            f"NOME: SOLICITANTE FICTICIO\nCPF: {cpf_sintetico(1)}\n"
            "DATA DA SOLICITACAO: 01/02/2025\nDATA DO ATENDIMENTO: 02/02/2025\n"
            "DATA DO AGENDAMENTO: 03/02/2025\nDATA DE EMISSAO DO COMPROVANTE: 04/02/2025\n"
            "TIPO DE SOLICITACAO: PRIMEIRA VIA\nORGAO EMISSOR: UNIDADE FICTICIA\n"
            "SITUACAO DA SOLICITACAO: RECEBIDA")


class TesteSolicitacaoCTPS(unittest.TestCase):
    def test_identificacao_distingue_procedimentos_e_documentos_trabalhistas(self):
        casos = {
            comprovante(): "SOLICITACAO_CTPS",
            "COMPROVANTE DE AGENDAMENTO DA CTPS\nAGENDADO PARA: 03/02/2025": "AGENDAMENTO_CTPS",
            "COMPROVANTE DE EMISSAO DA CTPS\nDATA DE EMISSAO DA CTPS: 03/02/2025": "EMISSAO_CTPS",
            "PROTOCOLO DE ATENDIMENTO\nSERVICO: CTPS\nPROTOCOLO: 000001": "PROTOCOLO_ATENDIMENTO_CTPS",
            "PROTOCOLO DE ATENDIMENTO\nPROTOCOLO: 000001": "DOCUMENTO_DESCONHECIDO",
            "ANEXAR CTPS APOS A SOLICITACAO": "DOCUMENTO_DESCONHECIDO",
            TITULO: "DOCUMENTO_DESCONHECIDO",
            f"{CAPA}\n{QUALIFICACAO}": "CTPS_FISICA",
            dados_pessoais(): "CTPS_DIGITAL",
            "DECLARACAO DE VINCULO\nEMPREGADOR: FICTICIO\nEMPREGADO: FICTICIO": "DECLARACAO_VINCULO",
        }
        for texto, tipo in casos.items():
            with self.subTest(tipo=tipo):
                self.assertEqual(identificar_documento(texto)["tipo"], tipo)

    def test_conflito_de_finalidades_nao_gera_dados_para_conferencia(self):
        r = analisar_documento(documento(comprovante() + "\nCOMPROVANTE DE EMISSAO DA CTPS"))
        self.assertEqual(r["identificacao"]["status"], "AMBIGUO")
        self.assertEqual(r["campos"]["cpfs"], [])
        self.assertIn("CLASSIFICACAO_INCONCLUSIVA", r["pendencias_leitura"])

    def test_campos_pessoais_protocolo_e_datas_distintas(self):
        r = analisar_documento(documento(comprovante()))
        c = r["campos"]["solicitacao_ctps"]
        self.assertEqual({k: a["valor"] for k, a in c.items()}, {
            "NOME": "SOLICITANTE FICTICIO", "CPF": cpf_sintetico(1),
            "NUMERO_PROTOCOLO": "000123-A", "DATA_SOLICITACAO": "01/02/2025",
            "DATA_ATENDIMENTO": "02/02/2025", "DATA_AGENDAMENTO": "03/02/2025",
            "DATA_EMISSAO_COMPROVANTE": "04/02/2025", "TIPO_SOLICITACAO": "PRIMEIRA VIA",
            "ORGAO_EMISSOR": "UNIDADE FICTICIA", "SITUACAO_SOLICITACAO": "RECEBIDA",
        })
        self.assertTrue(all(x["pagina_origem"] == 1 for x in c.values()))

    def test_terceiros_e_cpf_sem_contexto(self):
        r = analisar_documento(documento(comprovante() + f"\nDADOS DO ATENDENTE\nNOME: ATENDENTE FICTICIO"
                                          f"\nCPF: {cpf_sintetico(2)}\nCPF DO RESPONSAVEL: {cpf_sintetico(3)}"))
        self.assertEqual(r["campos"]["solicitacao_ctps"]["CPF"]["valor"], cpf_sintetico(1))
        r = analisar_documento(documento(f"{TITULO}\nPROTOCOLO: 00001\nCPF: {cpf_sintetico(2)}"))
        c = r["campos"]["solicitacao_ctps"]
        self.assertEqual(c["CPF"]["status"], "EVIDENCIA_INSUFICIENTE")
        self.assertEqual(c["NOME"]["status"], "NAO_ENCONTRADO")
        self.assertEqual(r["campos"]["cpfs"], [])

    def test_geometria_nao_atravessa_grupos_e_conflitos(self):
        d = documento(f"{TITULO}\nPROTOCOLO: 00001", "")
        d["paginas"][1]["grupos_leitura"] = [[
            {"texto": "NOME DO SOLICITANTE:", "caixa": [0, 0, 105, 10]},
            {"texto": "PESSOA FICTICIA", "caixa": [110, 0, 185, 10]},
            {"texto": "DATA DA SOLICITACAO: 01/01/2025"},
            {"texto": "DATA DA SOLICITACAO: 10/01/2025"},
            {"texto": "CPF DO SOLICITANTE:", "caixa": [0, 30, 105, 40]},
        ], [{"texto": cpf_sintetico(1), "caixa": [110, 30, 185, 40]}]]
        r = analisar_documento(d)
        c = r["campos"]["solicitacao_ctps"]
        self.assertEqual(c["NOME"]["valor"], "PESSOA FICTICIA")
        self.assertEqual(c["NOME"]["trecho_origem"], "ROTULO_E_GEOMETRIA")
        self.assertEqual(c["NOME"]["pagina_origem"], 2)
        self.assertEqual(c["DATA_SOLICITACAO"]["status"], "AMBIGUO")
        self.assertEqual(c["CPF"]["status"], "EVIDENCIA_INSUFICIENTE")

    def test_data_generica_e_situacao_declarada_nao_inferem_emissao(self):
        r = analisar_documento(documento(f"{TITULO}\nPROTOCOLO: 0001\nDATA: 01/01/2025\nSITUACAO: EMITIDA"))
        c = r["campos"]["solicitacao_ctps"]
        self.assertEqual(r["identificacao"]["tipo"], "SOLICITACAO_CTPS")
        self.assertEqual(c["SITUACAO_SOLICITACAO"]["valor"], "EMITIDA")
        self.assertEqual(c["DATA_EMISSAO_COMPROVANTE"]["status"], "NAO_ENCONTRADO")
        self.assertEqual(c["DATA_SOLICITACAO"]["status"], "NAO_ENCONTRADO")

    def test_recuperacao_csv_e_leitura_parcial(self):
        d = documento(comprovante().replace("UNIDADE FICTICIA", "=SOMA(1;2)"),
                      "DATA DA SOLICITACAO: 02/12/2025")
        d["paginas"][1]["diagnostico_ocr"] = {"pendencias": ["ORCAMENTO_ESGOTADO"], "metricas": {}}
        with closing(conexao.conectar_banco(":memory:")) as b:
            conexao.criar_tabelas(b)
            codigo = conexao.criar_analise(b)
            doc = salvar_resultado_analise(b, 1, analisar_documento(d))["documento_id"]
            with b:
                b.execute("UPDATE analises SET status='CONCLUIDA'")
            rel, _ = recuperar_relatorio(b, codigo)
            self.assertEqual(rel["itens_documentais"], [])
            self.assertEqual(rel["leituras_pendentes"], [doc])
            estados = {c["campo"]: c["status"] for c in rel["estados_campos"]}
            self.assertEqual(estados["DATA_SOLICITACAO"], "AMBIGUO")
            with TemporaryDirectory() as pasta:
                destino = Path(pasta, "fixture.csv")
                exportar_analise_csv(b, destino, codigo=codigo)
                with destino.open(encoding="utf-8-sig", newline="") as arquivo:
                    linhas = list(csv.reader(arquivo, delimiter=";"))
                self.assertIn(["fixture.pdf", "NUMERO_PROTOCOLO", "000123-A", "1",
                               "ENCONTRADO", "ROTULO_EXPLICITO"], linhas)
                self.assertIn(["fixture.pdf", "ORGAO_EMISSOR", "'=SOMA(1;2)", "1",
                               "ENCONTRADO", "ROTULO_EXPLICITO"], linhas)
            self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(b.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_falha_na_gravacao_reverte_documento_e_campos(self):
        with closing(conexao.conectar_banco(":memory:")) as b:
            conexao.criar_tabelas(b)
            conexao.criar_analise(b)
            b.execute("""
                CREATE TEMP TRIGGER negar_cpf_fixture
                BEFORE INSERT ON campos_extraidos WHEN NEW.nome_campo = 'CPF'
                BEGIN SELECT RAISE(ABORT, 'fixture'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                salvar_resultado_analise(b, 1, analisar_documento(documento(comprovante())))
            for tabela in ("documentos", "campos_extraidos", "itens_documentais"):
                self.assertEqual(b.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)

    def test_conferencia_individual_familiar_e_reatribuicao(self):
        for modo in ("INDIVIDUAL", "FAMILIAR"):
            with self.subTest(modo=modo), closing(conexao.conectar_banco(":memory:")) as b:
                conexao.criar_tabelas(b)
                conexao.criar_analise(b, modo=modo)
                comp = salvar_resultado_analise(b, 1, analisar_documento(documento(comprovante())))["documento_id"]
                docs = []
                for tipo in ("DOCUMENTO_IDENTIDADE", "CIN", "CNH", "CERTIDAO_NASCIMENTO", "CTPS_FISICA", "CTPS_DIGITAL"):
                    d = adicionar_documento(b, 1, "fixture.pdf", "fixtures/fixture.pdf", tipo)
                    docs.append(d)
                    salvar_campo_extraido(b, d, "NOME", "SOLICITANTE FICTICIO")
                    salvar_campo_extraido(b, d, "CPF", cpf_sintetico(1), status=STATUS_CPF_TITULAR)
                    salvar_campo_extraido(b, d, "DATA_NASCIMENTO", "01/02/2025")
                if modo == "FAMILIAR":
                    pessoa = atribuicoes.criar_pessoa(b, 1)
                    outra = atribuicoes.criar_pessoa(b, 1)
                    for d in [comp, *docs]:
                        atribuicoes.atribuir_documento(b, d, pessoa)
                    outro = adicionar_documento(b, 1, "fixture2.pdf", "fixtures/fixture2.pdf")
                    salvar_campo_extraido(b, outro, "NOME", "OUTRA PESSOA FICTICIA")
                    atribuicoes.atribuir_documento(b, outro, outra)
                resultados = [r for r in conferir_analise(b, 1) if comp in (r["documento_1_id"], r["documento_2_id"])]
                pessoais = [r for r in resultados if r["campo"] != "DATA_NASCIMENTO"]
                self.assertEqual(len(pessoais), 12)
                self.assertEqual({(r["campo"], r["resultado"]) for r in pessoais},
                                 {("NOME", "COMPATIVEL"), ("CPF", "COMPATIVEL")})
                self.assertTrue(all(r["resultado"] == "NAO_CONFERIDO" for r in resultados
                                    if r["campo"] == "DATA_NASCIMENTO"))
                if modo == "FAMILIAR":
                    atribuicoes.atribuir_documento(b, comp, outra)
                    self.assertEqual(b.execute("""
                        SELECT COUNT(*) FROM conferencias WHERE documento_1_id = ? OR documento_2_id = ?
                    """, (comp, comp)).fetchone()[0], 0)
