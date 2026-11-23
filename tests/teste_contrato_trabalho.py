import csv
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from ceuma.documentos.analisador import analisar_documento
from ceuma.conferencia.automatica import conferir_analise
from ceuma.relatorios.exportador_csv import exportar_analise_csv
from ceuma.extracao.campos import STATUS_CPF_TITULAR
from ceuma.banco.persistencia import salvar_resultado_analise
from ceuma.relatorios.relatorio import recuperar_relatorio
from ceuma.banco import atribuicoes
from ceuma.banco import conexao
from ceuma.banco.operacoes import adicionar_documento, salvar_campo_extraido
from ceuma.documentos.identificador import identificar_documento
from teste_analise_familiar import cpf_sintetico
from teste_ctps_digital import dados_pessoais
from teste_ctps_fisica import CAPA, QUALIFICACAO, documento
from teste_declaracao_vinculo import declaracao


def contrato():
    return (
        "CONTRATO INDIVIDUAL DE TRABALHO\nEMPREGADO: TITULAR FICTICIO\n"
        f"CPF DO EMPREGADO: {cpf_sintetico(1)}\nEMPREGADOR: EMPRESA FICTICIA\n"
        "CNPJ DO EMPREGADOR: 00.000.000/0001-00\nCARGO: AUXILIAR FICTICIO\n"
        "DATA DE ADMISSAO: 01/02/2020\nDATA DE INICIO DA VIGENCIA: 02/02/2020\n"
        "DATA PREVISTA DE TERMINO: 31/12/2021\nDATA DE ELABORACAO: 20/01/2020\n"
        "DATA DA ASSINATURA: 21/01/2020\nJORNADA SEMANAL: 44 HORAS\n"
        "REMUNERACAO BRUTA MENSAL: R$ 1.234,56\nLOCAL DE TRABALHO: UNIDADE FICTICIA"
    )


def analisar(*paginas):
    return analisar_documento(documento(*paginas))


def campos(*paginas):
    return analisar(*paginas)["campos"]["contrato_trabalho"]


class TesteContratoTrabalho(unittest.TestCase):
    def test_distincao_documental_e_mencoes(self):
        casos = {
            contrato(): "CONTRATO_TRABALHO_AVULSO",
            "PROPOSTA DE EMPREGO": "PROPOSTA_EMPREGO",
            "CONTRATO DE PRESTACAO DE SERVICOS": "CONTRATO_PRESTACAO_SERVICOS",
            "TERMO DE COMPROMISSO DE ESTAGIO": "CONTRATO_ESTAGIO",
            "TERMO ADITIVO AO CONTRATO DE TRABALHO": "TERMO_ADITIVO_TRABALHO",
            "TERMO DE RESCISAO DO CONTRATO DE TRABALHO": "TERMO_RESCISAO_TRABALHO",
            "ANEXAR CONTRATO DE TRABALHO": "DOCUMENTO_DESCONHECIDO",
            "CONTRATO INDIVIDUAL DE TRABALHO": "DOCUMENTO_DESCONHECIDO",
            declaracao(): "DECLARACAO_VINCULO",
            CAPA + "\n" + QUALIFICACAO: "CTPS_FISICA",
            dados_pessoais(): "CTPS_DIGITAL",
        }
        for texto, tipo in casos.items():
            with self.subTest(tipo=tipo):
                self.assertEqual(identificar_documento(texto)["tipo"], tipo)
        r = analisar(contrato() + "\nPROPOSTA DE EMPREGO")
        self.assertEqual(r["identificacao"]["status"], "AMBIGUO")
        self.assertEqual(r["campos"]["cpfs"], [])

    def test_rotulos_e_datas_com_finalidades_distintas(self):
        c = campos(contrato())
        esperado = {
            "NOME": "TITULAR FICTICIO", "CPF": cpf_sintetico(1), "EMPREGADOR": "EMPRESA FICTICIA",
            "CNPJ_EMPREGADOR": "00000000000100", "CARGO": "AUXILIAR FICTICIO",
            "ADMISSAO": "01/02/2020", "DATA_INICIO_VIGENCIA": "02/02/2020",
            "DATA_TERMINO_PREVISTO": "31/12/2021", "DATA_ELABORACAO": "20/01/2020",
            "DATA_ASSINATURA": "21/01/2020", "JORNADA_SEMANAL": "44 HORAS",
            "REMUNERACAO_BRUTA": "1234.56", "REMUNERACAO_BRUTA_ORIGINAL": "R$ 1.234,56",
            "REMUNERACAO_BRUTA_PERIODICIDADE": "MENSAL", "LOCAL_TRABALHO": "UNIDADE FICTICIA",
        }
        for campo, valor in esperado.items():
            self.assertEqual(c[campo]["valor"], valor, campo)
        for campo in ("DESLIGAMENTO", "TIPO_CONTRATO", "PRAZO_CONTRATUAL"):
            self.assertEqual(c[campo]["status"], "NAO_ENCONTRADO")

    def test_modalidade_expressa_e_prazo_sem_calculo(self):
        for titulo, tipo in (
            ("CONTRATO DE EXPERIENCIA", "EXPERIENCIA"),
            ("CONTRATO DE TRABALHO POR PRAZO DETERMINADO", "PRAZO DETERMINADO"),
            ("CONTRATO DE TRABALHO POR PRAZO INDETERMINADO", "PRAZO INDETERMINADO"),
        ):
            with self.subTest(tipo=tipo):
                c = campos(contrato().replace("CONTRATO INDIVIDUAL DE TRABALHO", titulo) +
                           "\nPRAZO CONTRATUAL: 30 DIAS")
                self.assertEqual(c["TIPO_CONTRATO"]["valor"], tipo)
                self.assertEqual(c["PRAZO_CONTRATUAL"]["valor"], "30 DIAS")

    def test_clausulas_narrativas_em_paginas_distintas(self):
        c = campos(
            "CONTRATO DE EXPERIENCIA\n"
            f"EMPREGADO TITULAR FICTICIO, CPF {cpf_sintetico(1)}.\n"
            f"EMPREGADOR FIGURA FICTICIA, CPF {cpf_sintetico(2)}.\n"
            "CLAUSULA PRIMEIRA - DA FUNCAO\nO EMPREGADO EXERCERA A FUNCAO DE AUXILIAR.",
            "CLAUSULA 2 - DA VIGENCIA\nO CONTRATO VIGORARA DE 01/02/2020 A 01/03/2020.\n"
            "CLAUSULA 3 - DA REMUNERACAO\nO EMPREGADO RECEBERA SALARIO BRUTO DE R$ 25,30 POR HORA.\n"
            "CLAUSULA 4 - DA JORNADA\nA JORNADA SERA DE 8 HORAS DIARIAS.\n"
            "CLAUSULA 5 - MULTA\nREMUNERACAO BRUTA: R$ 500,00\n"
            "REFERENCIA LEGAL ARTIGO 7 DE 01/01/1990.",
        )
        self.assertEqual(c["CPF"]["valor"], cpf_sintetico(1))
        self.assertEqual(c["CPF_EMPREGADOR"]["valor"], cpf_sintetico(2))
        self.assertEqual(c["CARGO"]["valor"], "AUXILIAR")
        self.assertEqual(c["DATA_INICIO_VIGENCIA"]["valor"], "01/02/2020")
        self.assertEqual(c["DATA_TERMINO_PREVISTO"]["pagina_origem"], 2)
        self.assertEqual(c["REMUNERACAO_BRUTA"]["valor"], "25.30")
        self.assertEqual(c["REMUNERACAO_BRUTA_PERIODICIDADE"]["valor"], "HORARIA")
        self.assertEqual(c["JORNADA_DIARIA"]["valor"], "8 HORAS DIARIAS")
        self.assertEqual(c["ADMISSAO"]["status"], "NAO_ENCONTRADO")

    def test_terceiros_e_assinaturas_nao_substituem_partes(self):
        c = campos(contrato() + "\nREPRESENTANTE DA EMPRESA\nCPF: " + cpf_sintetico(2) +
                   "\nTESTEMUNHA\nCPF DO EMPREGADO: " + cpf_sintetico(3) +
                   f"\nASSINATURAS\nEMPREGADO PESSOA SEM VINCULO, CPF {cpf_sintetico(3)}.\nDATA DE ASSINATURA: 21/01/2020")
        self.assertEqual(c["CPF"]["valor"], cpf_sintetico(1))
        self.assertEqual(c["NOME"]["valor"], "TITULAR FICTICIO")
        self.assertEqual(c["DATA_ASSINATURA"]["valor"], "21/01/2020")

    def test_salario_liquido_e_valores_alheios(self):
        c = campos(contrato() + "\nSALARIO LIQUIDO: R$ 1.000,01 POR MES\n"
                   "BENEFICIO: R$ 10,00\nDESCONTO: R$ 20,00\nMULTA: R$ 30,00\nINDENIZACAO: R$ 40,00")
        self.assertEqual(c["REMUNERACAO_BRUTA"]["valor"], "1234.56")
        self.assertEqual(c["REMUNERACAO_LIQUIDA"]["valor"], "1000.01")
        self.assertEqual(c["REMUNERACAO_LIQUIDA_PERIODICIDADE"]["valor"], "MENSAL")
        self.assertEqual(c["REMUNERACAO_DECLARADA"]["status"], "NAO_ENCONTRADO")

    def test_multiplas_pessoas_e_multiplos_contratos(self):
        for texto in (contrato() + "\nEMPREGADO: OUTRA PESSOA FICTICIA",
                      contrato() + "\n" + contrato()):
            r = analisar(texto)
            c = r["campos"]["contrato_trabalho"]
            self.assertIn("CONTRATO_TRABALHO_REQUER_REVISAO", r["pendencias_leitura"])
            for k in ("NOME", "CPF", "EMPREGADOR", "REMUNERACAO_BRUTA"):
                self.assertEqual(c[k]["status"], "AMBIGUO")
                self.assertIsNone(c[k]["valor"])

    def test_clausulas_conflitantes_preservam_ambiguidade(self):
        c = campos(contrato(), "CLAUSULA 5 - DA REMUNERACAO\n"
                   "SALARIO BRUTO MENSAL: R$ 9.900,00\nDATA DE ADMISSAO: 01/02/2023")
        self.assertEqual(c["REMUNERACAO_BRUTA"]["status"], "AMBIGUO")
        self.assertEqual(c["ADMISSAO"]["status"], "AMBIGUO")

    def test_geometria_nao_transpoe_grupos(self):
        d = documento(contrato().replace("CARGO: AUXILIAR FICTICIO\n", ""), "")
        d["paginas"][1]["grupos_leitura"] = [[
            {"texto": "CARGO:", "caixa": [0, 0, 50, 8]},
            {"texto": "MOTORISTA FICTICIO", "caixa": [55, 0, 110, 8]},
            {"texto": "PRAZO CONTRATUAL:", "caixa": [0, 20, 85, 28]},
        ], [{"texto": "30 DIAS", "caixa": [90, 20, 130, 28]}]]
        c = analisar_documento(d)["campos"]["contrato_trabalho"]
        self.assertEqual(c["CARGO"]["valor"], "MOTORISTA FICTICIO")
        self.assertEqual(c["CARGO"]["pagina_origem"], 2)
        self.assertEqual(c["PRAZO_CONTRATUAL"]["status"], "EVIDENCIA_INSUFICIENTE")

    def test_rollback_integral(self):
        with closing(conexao.conectar_banco(":memory:")) as b:
            conexao.criar_tabelas(b)
            conexao.criar_analise(b)
            b.execute("""CREATE TEMP TRIGGER negar_salario BEFORE INSERT ON campos_extraidos
                         WHEN NEW.nome_campo = 'REMUNERACAO_BRUTA'
                         BEGIN SELECT RAISE(ABORT, 'fixture'); END""")
            with self.assertRaises(sqlite3.IntegrityError):
                salvar_resultado_analise(b, 1, analisar(contrato()))
            for tabela in ("documentos", "campos_extraidos", "itens_documentais"):
                self.assertEqual(b.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)

    def test_integracao_relatorio_csv_familia_reatribuicao_e_parcial(self):
        for modo in ("INDIVIDUAL", "FAMILIAR"):
            with self.subTest(modo=modo), closing(conexao.conectar_banco(":memory:")) as b:
                conexao.criar_tabelas(b)
                codigo = conexao.criar_analise(b, modo=modo)
                d = documento(contrato().replace("AUXILIAR FICTICIO", "=SOMA(1;2)"))
                d["paginas"][0]["diagnostico_ocr"] = {"pendencias": ["ORCAMENTO_ESGOTADO"], "metricas": {}}
                ident = salvar_resultado_analise(b, 1, analisar_documento(d))["documento_id"]
                outro = adicionar_documento(b, 1, "outro.pdf", "fixtures/outro.pdf", "CIN")
                for c, v, s in (("NOME", "TITULAR FICTICIO", "ENCONTRADO"),
                                ("CPF", cpf_sintetico(1), STATUS_CPF_TITULAR),
                                ("DATA_NASCIMENTO", "01/02/2020", "ENCONTRADO")):
                    salvar_campo_extraido(b, outro, c, v, status=s)
                if modo == "FAMILIAR":
                    p = atribuicoes.criar_pessoa(b, 1)
                    outra = atribuicoes.criar_pessoa(b, 1)
                    for doc in (ident, outro):
                        atribuicoes.atribuir_documento(b, doc, p)
                resultados = conferir_analise(b, 1)
                self.assertEqual({r["campo"] for r in resultados if r["resultado"] == "COMPATIVEL"}, {"NOME", "CPF"})
                self.assertFalse(any(r["resultado"] == "DIVERGENTE" for r in resultados))
                with b:
                    b.execute("UPDATE analises SET status='CONCLUIDA'")
                rel, _ = recuperar_relatorio(b, codigo)
                self.assertEqual(rel["leituras_pendentes"], [ident])
                self.assertEqual(rel["itens_documentais"], [])
                with TemporaryDirectory() as pasta:
                    destino = Path(pasta, "fixture.csv")
                    exportar_analise_csv(b, destino, codigo=codigo)
                    with destino.open(encoding="utf-8-sig", newline="") as arquivo:
                        linhas = list(csv.reader(arquivo, delimiter=";"))
                    self.assertIn(["fixture.pdf", "CARGO", "'=SOMA(1;2)", "1", "ENCONTRADO", "ROTULO_EXPLICITO"], linhas)
                    self.assertIn(["fixture.pdf", "REMUNERACAO_BRUTA", "1234.56", "1",
                                   "ENCONTRADO", "REMUNERACAO_CONTRATADA"], linhas)
                if modo == "FAMILIAR":
                    atribuicoes.atribuir_documento(b, ident, outra)
                    self.assertEqual(conferir_analise(b, 1), [])
                self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(b.execute("PRAGMA foreign_key_check").fetchall(), [])
