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
from teste_ctps_fisica import documento


def certidao():
    return (
        "CERTIDÃO DE CASAMENTO\nREGISTRO CIVIL\nMATRÍCULA: 000001 01 55\n"
        "DATA DO CASAMENTO: 01/02/2020\nDATA DO REGISTRO: 02/02/2020\n"
        "DATA DE EMISSÃO: 03/02/2025\nLOCAL DO CASAMENTO: Município Fictício\n"
        "CARTÓRIO: Serventia Fictícia\nREGIME DE BENS: Comunhão parcial\n"
        f"CÔNJUGE 1\nNOME: Titular Fictícia\nCPF: {cpf_sintetico(1)}\n"
        "DATA DE NASCIMENTO: 01/01/2000\n"
        f"CÔNJUGE 2\nNOME: Pessoa Fictícia\nCPF: {cpf_sintetico(2)}\nDATA DE NASCIMENTO: 02/01/2000"
    )


def analisar(*paginas):
    return analisar_documento(documento(*paginas))


def campos(*paginas):
    return analisar(*paginas)["campos"]["casamento"]


class TesteCertidaoCasamento(unittest.TestCase):
    def test_distincao_dos_titulos_e_mencoes(self):
        for texto, tipo in (
            ("CERTIDÃO DE CASAMENTO RELIGIOSO", "CASAMENTO_RELIGIOSO"),
            ("DECLARAÇÃO DE UNIÃO ESTÁVEL", "DECLARACAO_UNIAO_ESTAVEL"),
            ("ESCRITURA PÚBLICA DE UNIÃO ESTÁVEL", "ESCRITURA_UNIAO_ESTAVEL"),
            ("CERTIDÃO DE DIVÓRCIO", "DOCUMENTO_DIVORCIO"),
            ("CERTIDÃO DE ÓBITO", "CERTIDAO_OBITO"),
            ("CASAMENTO MENCIONADO POR PESSOA FICTICIA", "DOCUMENTO_DESCONHECIDO"),
            ("CERTIDÃO DE CASAMENTO", "DOCUMENTO_DESCONHECIDO"),
        ):
            self.assertEqual(analisar(texto)["identificacao"]["tipo"], tipo)
        self.assertEqual(identificar_documento(certidao())["tipo"], "CERTIDAO_CASAMENTO")
        averbada = analisar(certidao().replace("CERTIDÃO DE CASAMENTO", "CERTIDÃO DE CASAMENTO COM AVERBAÇÃO"))
        self.assertEqual(averbada["identificacao"]["tipo"], "CERTIDAO_CASAMENTO")
        self.assertIn("AVERBACAO_REQUER_CONFERENCIA", averbada["pendencias_leitura"])
        self.assertEqual(analisar(certidao() + "\nCERTIDÃO DE ÓBITO")["identificacao"]["status"], "AMBIGUO")

    def test_nascimento_com_anotacao_nao_vira_casamento(self):
        r = analisar("CERTIDÃO DE NASCIMENTO\nREGISTRO CIVIL\nMATRÍCULA: 000001\n"
                     "NOME DO REGISTRADO: PESSOA FICTICIA\n"
                     "ANOTAÇÃO DE CASAMENTO\nCERTIDÃO DE CASAMENTO\nDATA DO CASAMENTO: 01/01/2025")
        self.assertEqual(r["identificacao"]["tipo"], "CERTIDAO_NASCIMENTO")

    def test_registro_estruturado_e_dados_separados(self):
        c = campos(certidao())
        esperado = {
            "NOME_CONJUGE_1": "Titular Fictícia", "NOME_CONJUGE_2": "Pessoa Fictícia",
            "CPF_CONJUGE_1": cpf_sintetico(1), "CPF_CONJUGE_2": cpf_sintetico(2),
            "DATA_NASCIMENTO_CONJUGE_1": "01/01/2000", "DATA_NASCIMENTO_CONJUGE_2": "02/01/2000",
            "DATA_CASAMENTO": "01/02/2020", "DATA_REGISTRO_CASAMENTO": "02/02/2020",
            "DATA_EMISSAO_CERTIDAO": "03/02/2025", "MATRICULA": "000001 01 55",
            "REGIME_BENS": "Comunhão parcial", "CARTORIO": "Serventia Fictícia",
            "LOCAL_CASAMENTO": "Município Fictício",
        }
        for campo, valor in esperado.items():
            self.assertEqual(c[campo]["valor"], valor, campo)
        self.assertEqual(c["AVERBACAO"]["status"], "NAO_ENCONTRADO")
        self.assertEqual(c["FILIACAO_CONJUGE_1"]["status"], "NAO_ENCONTRADO")

    def test_narrativa_preserva_grafia_e_data_extensa(self):
        c = campos("CERTIDÃO DE CASAMENTO\nREGISTRO CIVIL\nCertifico que contraíram matrimônio "
                   "João Fictício e Ana D’Exemplo, em primeiro de fevereiro de dois mil e vinte.")
        self.assertEqual(c["NOME_CONJUGE_1"]["valor"], "João Fictício")
        self.assertEqual(c["NOME_CONJUGE_2"]["valor"], "Ana D’Exemplo")
        self.assertEqual(c["DATA_CASAMENTO"]["valor"], "01/02/2020")
        self.assertEqual(c["CPF_CONJUGE_1"]["status"], "NAO_ENCONTRADO")

    def test_filiacao_e_cpfs_de_terceiros(self):
        texto = certidao().replace("CÔNJUGE 2", f"FILIAÇÃO: Genitores Fictícios\nCPF: {cpf_sintetico(3)}\nCÔNJUGE 2")
        c = campos(texto + f"\nFILIAÇÃO: Outros Genitores\nTESTEMUNHA\nCPF: {cpf_sintetico(4)}")
        self.assertEqual(c["FILIACAO_CONJUGE_1"]["valor"], "Genitores Fictícios")
        self.assertEqual(c["FILIACAO_CONJUGE_2"]["valor"], "Outros Genitores")
        self.assertEqual(c["CPF_CONJUGE_1"]["valor"], cpf_sintetico(1))
        self.assertEqual(c["CPF_CONJUGE_2"]["valor"], cpf_sintetico(2))

    def test_averbacoes_separadas_preservam_registro_original(self):
        for categoria, texto in (
            ("DIVORCIO", "Divórcio"), ("OBITO", "Óbito"), ("RETIFICACAO", "Retificação"),
            ("SEPARACAO", "Separação"), ("ALTERACAO_NOME", "Alteração de nome"),
        ):
            r = analisar(certidao(), f"AVERBAÇÃO DE {texto}\nDATA DA AVERBAÇÃO: 10/03/2024\n"
                        "NOVO NOME DO CÔNJUGE 1: Nome Fictício Alterado\n"
                        "DATA DO CASAMENTO: 01/01/2030")
            c = r["campos"]["casamento"]
            self.assertEqual(c["AVERBACAO_" + categoria]["valor"], "MENCIONADA")
            self.assertEqual(c["DATA_AVERBACAO_" + categoria]["pagina_origem"], 2)
            self.assertEqual(c["DATA_AVERBACAO_" + categoria]["valor"], "10/03/2024")
            self.assertEqual(c["NOME_AVERBADO_CONJUGE_1"]["valor"], "Nome Fictício Alterado")
            self.assertEqual(c["NOME_CONJUGE_1"]["valor"], "Titular Fictícia")
            self.assertEqual(c["DATA_CASAMENTO"]["valor"], "01/02/2020")
            self.assertIn("AVERBACAO_REQUER_CONFERENCIA", r["pendencias_leitura"])

    def test_data_retificada_fica_separada_e_ausencia_explicita_nao_vira_presenca(self):
        original = certidao().replace("CÔNJUGE 2", "NOME APÓS O CASAMENTO: Titular Fictícia Nova\nCÔNJUGE 2")
        c = campos(original, "AVERBAÇÃO DE RETIFICAÇÃO\nDATA DO CASAMENTO RETIFICADA: 03/02/2020")
        self.assertEqual(c["NOME_APOS_CASAMENTO_CONJUGE_1"]["valor"], "Titular Fictícia Nova")
        self.assertEqual(c["NOME_CONJUGE_1"]["valor"], "Titular Fictícia")
        self.assertEqual(c["DATA_CASAMENTO"]["valor"], "01/02/2020")
        self.assertEqual(c["DATA_CASAMENTO_AVERBADA"]["valor"], "03/02/2020")
        r = analisar(certidao() + "\nAVERBAÇÕES: NÃO CONSTA")
        self.assertEqual(r["campos"]["casamento"]["AVERBACAO"]["valor"], "AUSENCIA_DECLARADA")
        self.assertNotIn("AVERBACAO_REQUER_CONFERENCIA", r.get("pendencias_leitura", []))

    def test_cpfs_repetidos_e_datas_conflitantes(self):
        r = analisar(certidao().replace(cpf_sintetico(2), cpf_sintetico(1)),
                     "DATA DO CASAMENTO: 02/05/2022")
        c = r["campos"]["casamento"]
        self.assertEqual(c["DATA_CASAMENTO"]["status"], "AMBIGUO")
        self.assertEqual(c["CPF_CONJUGE_1"]["status"], "AMBIGUO")
        self.assertEqual(c["CPF_CONJUGE_2"]["status"], "AMBIGUO")
        self.assertEqual(r["campos"]["cpfs"], [])

    def test_geometria_respeita_grupos_e_pagina(self):
        d = documento("CERTIDÃO DE CASAMENTO\nREGISTRO CIVIL\nMATRÍCULA: 001", "")
        d["paginas"][1]["grupos_leitura"] = [[
            {"texto": "NOME DO CÔNJUGE 1:", "caixa": [0, 0, 100, 10]},
            {"texto": "Pessoa Fictícia", "caixa": [103, 0, 200, 10]},
            {"texto": "CPF DO CÔNJUGE 1:", "caixa": [0, 20, 100, 30]},
        ], [{"texto": cpf_sintetico(1), "caixa": [103, 20, 200, 30]}]]
        c = analisar_documento(d)["campos"]["casamento"]
        self.assertEqual(c["NOME_CONJUGE_1"]["valor"], "Pessoa Fictícia")
        self.assertEqual(c["NOME_CONJUGE_1"]["pagina_origem"], 2)
        self.assertEqual(c["CPF_CONJUGE_1"]["status"], "EVIDENCIA_INSUFICIENTE")

    def test_rollback_atomico(self):
        with closing(conexao.conectar_banco(":memory:")) as b:
            conexao.criar_tabelas(b)
            conexao.criar_analise(b)
            b.execute("""CREATE TEMP TRIGGER negar_campo_demo BEFORE INSERT ON campos_extraidos
                         WHEN NEW.nome_campo='NOME_CONJUGE_2'
                         BEGIN SELECT RAISE(ABORT, 'fixture'); END""")
            with self.assertRaises(sqlite3.IntegrityError):
                salvar_resultado_analise(b, 1, analisar(certidao()))
            for tabela in ("documentos", "campos_extraidos", "itens_documentais"):
                self.assertEqual(b.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)

    def test_recuperacao_csv_conferencia_e_reatribuicao(self):
        for modo in ("INDIVIDUAL", "FAMILIAR"):
            with self.subTest(modo=modo), closing(conexao.conectar_banco(":memory:")) as b:
                conexao.criar_tabelas(b)
                codigo = conexao.criar_analise(b, modo=modo)
                d = documento(certidao().replace("Município Fictício", "=SOMA(1;2)"))
                d["paginas"][0]["diagnostico_ocr"] = {"pendencias": ["ORCAMENTO_ESGOTADO"], "metricas": {}}
                ident = salvar_resultado_analise(b, 1, analisar_documento(d))["documento_id"]
                self.assertEqual(b.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0], 0)
                outro = adicionar_documento(b, 1, "outro.pdf", "fixtures/outro.pdf", "CIN")
                # Imita campos genéricos de um registro histórico de casamento.
                for doc, cpf in ((ident, cpf_sintetico(2)), (outro, cpf_sintetico(1))):
                    salvar_campo_extraido(b, doc, "CPF", cpf, status=STATUS_CPF_TITULAR)
                if modo == "FAMILIAR":
                    p = atribuicoes.criar_pessoa(b, 1)
                    outra = atribuicoes.criar_pessoa(b, 1)
                    for doc in (ident, outro):
                        atribuicoes.atribuir_documento(b, doc, p)
                self.assertEqual(conferir_analise(b, 1), [])
                with b:
                    b.execute("UPDATE analises SET status='CONCLUIDA'")
                rel, _ = recuperar_relatorio(b, codigo)
                self.assertEqual(rel["leituras_pendentes"], [ident])
                self.assertEqual(rel["total_pares_elegiveis"], 0)
                self.assertEqual(rel["itens_documentais"], [])
                with TemporaryDirectory() as pasta:
                    destino = Path(pasta, "fixture.csv")
                    exportar_analise_csv(b, destino, codigo=codigo)
                    with destino.open(encoding="utf-8-sig", newline="") as arquivo:
                        linhas = list(csv.reader(arquivo, delimiter=";"))
                    self.assertIn(["fixture.pdf", "LOCAL_CASAMENTO", "'=SOMA(1;2)", "1",
                                   "ENCONTRADO", "ROTULO_EXPLICITO"], linhas)
                    self.assertIn(["fixture.pdf", "NOME_CONJUGE_2", "Pessoa Fictícia", "1",
                                   "ENCONTRADO", "ROTULO_EXPLICITO"], linhas)
                if modo == "FAMILIAR":
                    atribuicoes.atribuir_documento(b, ident, outra)
                    self.assertEqual(conferir_analise(b, 1), [])
                self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(b.execute("PRAGMA foreign_key_check").fetchall(), [])
