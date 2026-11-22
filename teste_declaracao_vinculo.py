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
from teste_solicitacao_ctps import comprovante


def declaracao():
    return (
        "DECLARACAO DE VINCULO EMPREGATICIO\nTRABALHADOR: TITULAR FICTICIO\n"
        f"CPF DO TRABALHADOR: {cpf_sintetico(1)}\nEMPREGADOR: EMPRESA FICTICIA\n"
        "CNPJ DO EMPREGADOR: 00.000.000/0001-00\nCARGO: AUXILIAR FICTICIO\n"
        "DATA DE ADMISSAO: 01/02/2020\nREMUNERACAO BRUTA MENSAL: R$ 1.234,56\n"
        "DATA DA DECLARACAO: 10/03/2021\nRESPONSAVEL PELA DECLARACAO: FIGURA FICTICIA\n"
        "FUNCAO DO RESPONSAVEL: DIRETORA"
    )


def analisar(*paginas):
    return analisar_documento(documento(*paginas))


def campos(*paginas):
    return analisar(*paginas)["campos"]["declaracao_vinculo"]


class TesteDeclaracaoVinculo(unittest.TestCase):
    def test_tipologia_e_mencoes_isoladas(self):
        casos = {
            declaracao(): "DECLARACAO_VINCULO",
            declaracao().replace("EMPRESA FICTICIA", "EMPRESA DE PRESTACAO DE SERVICOS"): "DECLARACAO_VINCULO",
            "DECLARACAO DE PRESTACAO DE SERVICOS\nPRESTADOR: FICTICIO": "DECLARACAO_PRESTACAO_SERVICOS",
            "DECLARACAO DE TRABALHO AUTONOMO": "DECLARACAO_TRABALHO_AUTONOMO",
            "DECLARACAO DE RENDA": "DECLARACAO_RENDA",
            "DECLARACAO DE DESEMPREGO": "DECLARACAO_DESEMPREGO",
            "ANEXAR DECLARACAO DE VINCULO TRABALHISTA": "DOCUMENTO_DESCONHECIDO",
            "DECLARACAO DE TRABALHO\nEMPREGADOR: B\nTRABALHADOR: A": "DOCUMENTO_DESCONHECIDO",
            "CONTRATO INDIVIDUAL DE TRABALHO\nEMPREGADOR: A\nEMPREGADO: B\nCLAUSULA 1": "CONTRATO_TRABALHO_AVULSO",
            f"{CAPA}\n{QUALIFICACAO}": "CTPS_FISICA",
            dados_pessoais(): "CTPS_DIGITAL",
            comprovante(): "SOLICITACAO_CTPS",
        }
        for texto, tipo in casos.items():
            with self.subTest(tipo=tipo):
                self.assertEqual(identificar_documento(texto)["tipo"], tipo)
        r = analisar(declaracao() + "\nPRESTACAO DE SERVICOS SEM VINCULO EMPREGATICIO")
        self.assertEqual(r["identificacao"]["tipo"], "DOCUMENTO_DESCONHECIDO")
        self.assertEqual(r["campos"]["cpfs"], [])
        r = analisar(declaracao() + "\nDECLARACAO DE RENDA")
        self.assertEqual(r["identificacao"]["status"], "AMBIGUO")

    def test_campos_estruturados_e_ausencia_de_situacao_inferida(self):
        c = campos(declaracao())
        esperado = {
            "NOME": "TITULAR FICTICIO", "CPF": cpf_sintetico(1), "EMPREGADOR": "EMPRESA FICTICIA",
            "CNPJ_EMPREGADOR": "00000000000100", "CARGO": "AUXILIAR FICTICIO", "ADMISSAO": "01/02/2020",
            "REMUNERACAO_BRUTA": "1234.56", "REMUNERACAO_BRUTA_ORIGINAL": "R$ 1.234,56",
            "REMUNERACAO_BRUTA_PERIODICIDADE": "MENSAL", "DATA_DECLARACAO": "10/03/2021",
            "RESPONSAVEL_DECLARACAO": "FIGURA FICTICIA", "FUNCAO_RESPONSAVEL": "DIRETORA",
        }
        for k, v in esperado.items():
            self.assertEqual(c[k]["valor"], v, k)
        for k in ("SITUACAO_VINCULO", "DESLIGAMENTO"):
            self.assertEqual(c[k]["status"], "NAO_ENCONTRADO")

    def test_narrativa_com_quebras_e_evidencia_ancorada(self):
        c = campos("Declaração de trabalho\n"
                   f"Declaramos que PESSOA FICTÍCIA, CPF {cpf_sintetico(1)},\n"
                   "trabalha para EMPRESA FICTÍCIA, na função de AUXILIAR,\n"
                   "admitida em 01/02/2020, com remuneração mensal de R$ 1.234,58.\n"
                   "Declaração assinada por FIGURA FICTÍCIA, na qualidade de DIRETORA.")
        for k, v in {
            "NOME": "PESSOA FICTICIA", "CPF": cpf_sintetico(1), "EMPREGADOR": "EMPRESA FICTICIA",
            "CARGO": "AUXILIAR", "ADMISSAO": "01/02/2020", "REMUNERACAO_DECLARADA": "1234.58",
            "REMUNERACAO_DECLARADA_PERIODICIDADE": "MENSAL", "RESPONSAVEL_DECLARACAO": "FIGURA FICTICIA",
        }.items():
            self.assertEqual(c[k]["valor"], v, k)
            self.assertEqual(c[k]["trecho_origem"], "RELACAO_NARRATIVA_EXPLICITA")

    def test_cpf_de_representante_e_empregador_nao_vira_cpf_do_trabalhador(self):
        c = campos(declaracao().replace("CNPJ DO EMPREGADOR: 00.000.000/0001-00",
                 f"CPF DO EMPREGADOR: {cpf_sintetico(2)}") +
                 f"\nDADOS DO REPRESENTANTE\nCPF: {cpf_sintetico(3)}\nCPF DO REPRESENTANTE: {cpf_sintetico(3)}")
        self.assertEqual(c["CPF"]["valor"], cpf_sintetico(1))
        self.assertEqual(c["CPF_EMPREGADOR"]["valor"], cpf_sintetico(2))
        self.assertEqual(c["CNPJ_EMPREGADOR"]["status"], "NAO_ENCONTRADO")

    def test_cargo_e_salario_genericos_da_secao_de_terceiros_nao_entram_no_vinculo(self):
        c = campos(declaracao() + "\nDADOS DO REPRESENTANTE\nCARGO: DIRETOR\nSALARIO: R$ 9.000,00")
        self.assertEqual(c["CARGO"]["valor"], "AUXILIAR FICTICIO")
        self.assertEqual(c["REMUNERACAO_DECLARADA"]["status"], "NAO_ENCONTRADO")

    def test_cnpj_invalido_ou_nao_suportado(self):
        for valor, estado in (("0001", "INVALIDO"), ("ABC00000123456", "EVIDENCIA_INSUFICIENTE")):
            c = campos(declaracao().replace("00.000.000/0001-00", valor))
            self.assertEqual(c["CNPJ_EMPREGADOR"]["status"], estado)

    def test_situacao_explicita_e_cronologia(self):
        r = declaracao() + "\nDESLIGAMENTO: 03/04/2021\nSITUACAO DO VINCULO: ENCERRADO"
        c = campos(r)
        self.assertEqual(c["DESLIGAMENTO"]["valor"], "03/04/2021")
        self.assertEqual(c["SITUACAO_VINCULO"]["valor"], "ENCERRADO")
        c = campos(r.replace("03/04/2021", "03/04/2019"))
        self.assertEqual(c["ADMISSAO"]["status"], "INVALIDO")
        self.assertEqual(c["DESLIGAMENTO"]["status"], "INVALIDO")

    def test_periodicidade_sem_conversao_e_bruto_liquido(self):
        c = campos(declaracao() + "\nSALARIO LIQUIDO: R$ 1.200,01 POR SEMANA"
                   "\nREMUNERACAO: R$ 90,00\nBENEFICIO: R$ 15,00\nDESCONTO: R$ 20,00")
        self.assertEqual(c["REMUNERACAO_BRUTA"]["valor"], "1234.56")
        self.assertEqual(c["REMUNERACAO_LIQUIDA"]["valor"], "1200.01")
        self.assertEqual(c["REMUNERACAO_LIQUIDA_PERIODICIDADE"]["valor"], "SEMANAL")
        self.assertEqual(c["REMUNERACAO_DECLARADA"]["valor"], "90.00")
        self.assertEqual(c["REMUNERACAO_DECLARADA_PERIODICIDADE"]["status"], "NAO_ENCONTRADO")

    def test_narrativa_com_bruto_liquido_e_beneficio(self):
        c = campos("DECLARACAO DE TRABALHO\nDeclaramos que PESSOA FICTICIA trabalha para EMPRESA FICTICIA, "
                   "com salario bruto de R$ 1.500,50 e com remuneracao liquida de R$ 1.300,30 e beneficios de R$ 80,00.")
        self.assertEqual(c["REMUNERACAO_BRUTA"]["valor"], "1500.50")
        self.assertEqual(c["REMUNERACAO_LIQUIDA"]["valor"], "1300.30")
        self.assertEqual(c["REMUNERACAO_DECLARADA"]["status"], "NAO_ENCONTRADO")

    def test_multiplos_valores_e_multiplas_pessoas_em_revisao(self):
        r = analisar(declaracao() + "\nREMUNERACAO BRUTA MENSAL: R$ 1.500,40")
        self.assertIn("DECLARACAO_VINCULO_REQUER_REVISAO", r["pendencias_leitura"])
        self.assertEqual(r["campos"]["declaracao_vinculo"]["REMUNERACAO_BRUTA"]["status"], "AMBIGUO")
        c = campos(declaracao(), f"TRABALHADOR: OUTRO FICTICIO\nCPF DO TRABALHADOR: {cpf_sintetico(2)}")
        for k in ("NOME", "CPF", "EMPREGADOR", "ADMISSAO", "REMUNERACAO_BRUTA"):
            self.assertEqual(c[k]["status"], "AMBIGUO")
            self.assertIsNone(c[k]["valor"])

    def test_ausentes_em_documento_antigo(self):
        c = campos("DECLARACAO DE VINCULO\nTRABALHADOR: TITULAR FICTICIO\n"
                   "EMPREGADOR: EMPRESA FICTICIA\nDATA DA DECLARACAO: 05/06/1998")
        self.assertEqual(c["DATA_DECLARACAO"]["valor"], "05/06/1998")
        for k in ("CPF", "SITUACAO_VINCULO", "REMUNERACAO_DECLARADA"):
            self.assertEqual(c[k]["status"], "NAO_ENCONTRADO")

    def test_geometria_limitada_ao_grupo(self):
        d = documento("DECLARACAO DE VINCULO\nTRABALHADOR: TITULAR FICTICIO\nEMPREGADOR: EMPRESA FICTICIA", "")
        d["paginas"][1]["grupos_leitura"] = [[
            {"texto": "CARGO:", "caixa": [0, 0, 50, 10]},
            {"texto": "AUXILIAR", "caixa": [52, 0, 100, 10]},
            {"texto": "CPF DO TRABALHADOR:", "caixa": [0, 20, 80, 30]},
        ], [{"texto": cpf_sintetico(1), "caixa": [84, 20, 150, 30]}]]
        c = analisar_documento(d)["campos"]["declaracao_vinculo"]
        self.assertEqual(c["CARGO"]["valor"], "AUXILIAR")
        self.assertEqual(c["CARGO"]["pagina_origem"], 2)
        self.assertEqual(c["CPF"]["status"], "EVIDENCIA_INSUFICIENTE")


class TestePersistenciaVinculo(unittest.TestCase):
    def test_rollback_atualiza_documento_e_campos_juntos(self):
        with closing(conexao.conectar_banco(":memory:")) as b:
            conexao.criar_tabelas(b)
            conexao.criar_analise(b)
            b.execute("""
                CREATE TEMP TRIGGER negar_salario BEFORE INSERT ON campos_extraidos
                WHEN NEW.nome_campo = 'REMUNERACAO_BRUTA'
                BEGIN SELECT RAISE(ABORT, 'fixture'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                salvar_resultado_analise(b, 1, analisar(declaracao()))
            for tabela in ("documentos", "campos_extraidos", "itens_documentais"):
                self.assertEqual(b.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)

    def test_csv_relatorio_conferencia_e_reatribuicao(self):
        for modo in ("INDIVIDUAL", "FAMILIAR"):
            with self.subTest(modo=modo), closing(conexao.conectar_banco(":memory:")) as b:
                conexao.criar_tabelas(b)
                codigo = conexao.criar_analise(b, modo=modo)
                d = documento(declaracao().replace("CARGO: AUXILIAR FICTICIO", "CARGO: =SOMA(1;2)"))
                d["paginas"][0]["diagnostico_ocr"] = {"pendencias": ["ORCAMENTO_ESGOTADO"], "metricas": {}}
                decl = salvar_resultado_analise(b, 1, analisar_documento(d))["documento_id"]
                outro = adicionar_documento(b, 1, "outra.pdf", "fixtures/outra.pdf", "CIN")
                for campo, valor, s in (
                    ("NOME", "TITULAR FICTICIO", "ENCONTRADO"),
                    ("CPF", cpf_sintetico(1), STATUS_CPF_TITULAR),
                    ("DATA_NASCIMENTO", "10/03/2021", "ENCONTRADO"),
                ):
                    salvar_campo_extraido(b, outro, campo, valor, status=s)
                if modo == "FAMILIAR":
                    pessoa = atribuicoes.criar_pessoa(b, 1)
                    diferente = atribuicoes.criar_pessoa(b, 1)
                    for ident in (decl, outro):
                        atribuicoes.atribuir_documento(b, ident, pessoa)
                resultados = conferir_analise(b, 1)
                self.assertEqual({r["campo"] for r in resultados if r["resultado"] == "COMPATIVEL"}, {"NOME", "CPF"})
                self.assertFalse(any(r["resultado"] == "DIVERGENTE" for r in resultados))
                with b:
                    b.execute("UPDATE analises SET status='CONCLUIDA'")
                rel, _ = recuperar_relatorio(b, codigo)
                self.assertEqual(rel["leituras_pendentes"], [decl])
                self.assertEqual(rel["itens_documentais"], [])
                with TemporaryDirectory() as pasta:
                    destino = Path(pasta, "fixture.csv")
                    exportar_analise_csv(b, destino, codigo=codigo)
                    with destino.open(encoding="utf-8-sig", newline="") as arquivo:
                        linhas = list(csv.reader(arquivo, delimiter=";"))
                    self.assertIn(["fixture.pdf", "CARGO", "'=SOMA(1;2)", "1", "ENCONTRADO", "ROTULO_EXPLICITO"], linhas)
                    self.assertIn(["fixture.pdf", "REMUNERACAO_BRUTA", "1234.56", "1",
                                   "ENCONTRADO", "REMUNERACAO_DECLARADA"], linhas)
                if modo == "FAMILIAR":
                    atribuicoes.atribuir_documento(b, decl, diferente)
                    self.assertEqual(conferir_analise(b, 1), [])
                self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(b.execute("PRAGMA foreign_key_check").fetchall(), [])
