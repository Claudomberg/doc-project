import csv
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from analise.analisador_documento import analisar_documento
from analise.conferidor_banco import conferir_analise
from analise.extrator_campos import STATUS_CPF_TITULAR
from analise.exportador import exportar_analise_csv
from analise.persistencia import salvar_resultado_analise
from analise.relatorio import recuperar_relatorio
from banco import atribuicoes, conexao, itens_documentais
from banco.operacoes import adicionar_documento, salvar_campo_extraido
from documentos.identificador import identificar_documento
from teste_analise_familiar import cpf_sintetico

CAPA = "CARTEIRA DE TRABALHO E PREVIDENCIA SOCIAL"
QUALIFICACAO = "QUALIFICACAO CIVIL\nNOME: PESSOA FICTICIA\nNUMERO DA CTPS: 000123\nSERIE: 0004\nUF: MA"
FORMULARIO = "CONTRATO DE TRABALHO\nEMPREGADOR: ____\nCARGO: ____\nADMISSAO: __/__/____"
CONTRATO = "CONTRATO DE TRABALHO\nEMPREGADOR: FICTICIO A\nADMISSAO: 01/02/2020"


def documento(*textos):
    return {"nome_arquivo": "fixture.pdf", "caminho_origem": "fixtures/fixture.pdf",
            "quantidade_paginas": len(textos), "paginas": [
                {"numero": n, "texto": texto, "metodo": "OCR"}
                for n, texto in enumerate(textos, 1)
            ]}


class TesteExtracaoCTPS(unittest.TestCase):
    def test_identificacao_contextual(self):
        for texto, tipo in (
            (f"{CAPA}\n{QUALIFICACAO}", "CTPS_FISICA"),
            ("CTPS DIGITAL\nDADOS PESSOAIS\nNOME: FICTICIO", "CTPS_DIGITAL"),
            ("COMPROVANTE DE SOLICITACAO DA CTPS\nPROTOCOLO: 0001", "SOLICITACAO_CTPS"),
            ("DECLARACAO DE VINCULO\nEMPREGADOR: FICTICIO\nEMPREGADO: FICTICIO", "DECLARACAO_VINCULO"),
            ("CONTRATO INDIVIDUAL DE TRABALHO\nEMPREGADOR: A\nEMPREGADO: B\nCLAUSULA 1",
             "CONTRATO_TRABALHO_AVULSO"),
            ("APRESENTAR CARTEIRA DE TRABALHO PARA REVISAO", "DOCUMENTO_DESCONHECIDO"),
        ):
            with self.subTest(tipo=tipo):
                self.assertEqual(identificar_documento(texto)["tipo"], tipo)

    def test_conflito_digital_fisica(self):
        texto = f"{CAPA}\n{QUALIFICACAO}\nCTPS DIGITAL\nDADOS PESSOAIS"
        self.assertEqual(identificar_documento(texto)["status"], "AMBIGUO")

    def test_qualificacao_cpf_nascimento_e_zeros(self):
        cpf = cpf_sintetico(1)
        r = analisar_documento(documento(CAPA, f"{QUALIFICACAO}\nCPF: {cpf}\nNASCIMENTO: 01/01/2000"))
        campos = r["campos"]["ctps_fisica"]
        self.assertEqual({k: v["valor"] for k, v in campos.items()}, {
            "NOME": "PESSOA FICTICIA", "CPF": cpf, "NUMERO_CTPS": "000123", "SERIE": "0004",
            "UF": "MA", "DATA_NASCIMENTO": "01/01/2000",
        })

    def test_cpf_de_terceiros_e_do_contrato_excluidos(self):
        cpf = cpf_sintetico(2)
        r = analisar_documento(documento(CAPA,
            f"{QUALIFICACAO}\nCPF DO REPRESENTANTE: {cpf}\nCPF: {cpf}",
            f"{CONTRATO}\nCPF: {cpf}"))
        self.assertEqual(r["campos"]["ctps_fisica"]["CPF"]["status"], "NAO_ENCONTRADO")

    def test_contratos_distintos_na_mesma_pagina_e_em_outras(self):
        outro = CONTRATO.replace("FICTICIO A", "FICTICIO B").replace("2020", "2021")
        terceiro = CONTRATO.replace("2020", "2022")
        r = analisar_documento(documento(CAPA, QUALIFICACAO, f"{CONTRATO}\n{outro}", terceiro))
        cs = r["ctps_fisica"]["contratos"]
        self.assertEqual(len(cs), 3)
        self.assertEqual([c["campos"]["EMPREGADOR"]["valor"] for c in cs],
                         ["FICTICIO A", "FICTICIO B", "FICTICIO A"])
        self.assertEqual([c["campos"]["ADMISSAO"]["valor"] for c in cs],
                         ["01/02/2020", "01/02/2021", "01/02/2022"])
        self.assertTrue(all(c["campos"]["SITUACAO"]["valor"] is None for c in cs))

    def test_contratos_colunas_e_valores_a_direita(self):
        d = documento(CAPA, QUALIFICACAO, "")
        dados = [
            ("CONTRATO DE TRABALHO", [0, 10, 90, 20]),
            ("CONTRATO DE TRABALHO", [180, 10, 270, 20]),
            ("EMPREGADOR:", [0, 25, 45, 35]), ("FICTICIO A", [48, 25, 90, 35]),
            ("EMPREGADOR:", [180, 25, 225, 35]), ("FICTICIO B", [229, 25, 270, 35]),
            ("ADMISSAO: 01/02/2020", [0, 40, 90, 50]),
            ("ADMISSAO: 01/02/2021", [180, 40, 270, 50]),
        ]
        p = d["paginas"][2]
        p["texto"] = "\n".join(t for t, _ in dados)
        p["linhas"] = [{"texto": t, "caixa": c} for t, c in dados]
        contratos = analisar_documento(d)["ctps_fisica"]["contratos"]
        self.assertEqual([c["campos"]["EMPREGADOR"]["valor"] for c in contratos],
                         ["FICTICIO A", "FICTICIO B"])

    def test_continuacao_explicita_e_inconclusiva(self):
        base = CONTRATO.replace("CONTRATO DE TRABALHO", "CONTRATO DE TRABALHO 07")
        final = "CONTINUACAO DO CONTRATO DE TRABALHO 07\nDESLIGAMENTO: 01/02/2022"
        junto = analisar_documento(documento(CAPA, QUALIFICACAO, base, final))["ctps_fisica"]["contratos"]
        self.assertEqual(len(junto), 1)
        self.assertEqual(junto[0]["paginas"], [3, 4])
        d = documento(CAPA, QUALIFICACAO, base, "", final)
        separados = analisar_documento(d)["ctps_fisica"]["contratos"]
        self.assertEqual(len(separados), 2)
        self.assertTrue(separados[1]["continuacao_inconclusiva"])

    def test_cronologia_cnpj_e_estado_de_evidencia(self):
        r = analisar_documento(documento(CAPA, QUALIFICACAO,
            f"{CONTRATO}\nDESLIGAMENTO: 01/01/2019\nCNPJ: ABC0000\nCARGO: MANUSCRITO ILEGIVEL"))
        campos = r["ctps_fisica"]["contratos"][0]["campos"]
        self.assertEqual(campos["ADMISSAO"]["status"], "INVALIDO")
        self.assertEqual(campos["DESLIGAMENTO"]["status"], "INVALIDO")
        self.assertEqual(campos["CNPJ"]["trecho_origem"], "FORMATO_NAO_SUPORTADO")
        self.assertEqual(campos["CARGO"]["status"], "EVIDENCIA_INSUFICIENTE")

    def test_formulario_ausencia_ilegivel_e_ordem(self):
        for pagina, estado in ((FORMULARIO, "APARENTEMENTE_SEM_PREENCHIMENTO"),
                                ("", "AUSENTE_OU_INCONCLUSIVA"),
                                (FORMULARIO + "\nOBSERVACAO ILEGIVEL", "AUSENTE_OU_INCONCLUSIVA"),
                                (FORMULARIO + "\nEMPREGADOR: FICTICIO", "PREENCHIDA")):
            with self.subTest(estado=estado):
                r = analisar_documento(documento(CAPA, QUALIFICACAO, CONTRATO, pagina))["ctps_fisica"]
                self.assertEqual(r["pagina_seguinte"], estado)
                self.assertEqual(r["sequencia"], "INCONCLUSIVA")
        d = documento(f"{CAPA}\nPAGINA 1 DE 4", f"{QUALIFICACAO}\nPAGINA 2 DE 4",
                      f"{CONTRATO}\nPAGINA 3 DE 4", f"{FORMULARIO}\nPAGINA 4 DE 4")
        self.assertEqual(analisar_documento(d)["ctps_fisica"]["sequencia"],
                         "VERIFICADA_NAS_PAGINAS_APRESENTADAS")
        d["paginas"][2], d["paginas"][3] = d["paginas"][3], d["paginas"][2]
        self.assertEqual(analisar_documento(d)["ctps_fisica"]["sequencia"], "INCONCLUSIVA")


class TestePersistenciaCTPS(unittest.TestCase):
    def setUp(self):
        self.b = self.enterContext(closing(conexao.conectar_banco(":memory:")))
        conexao.criar_tabelas(self.b)
        self.codigo = conexao.criar_analise(self.b, modo="FAMILIAR")
        self.cpf = cpf_sintetico(1)
        qualificacao = f"{QUALIFICACAO}\nCPF: {self.cpf}\nNASCIMENTO: 01/01/2000"
        self.r = analisar_documento(documento(CAPA, qualificacao, CONTRATO,
                                             CONTRATO.replace("2020", "2021")))

    def test_rollback_integral_em_falha_do_segundo_contrato(self):
        real = itens_documentais.criar_item_documental
        def salvar(banco, doc, tipo, ordem, **kwargs):
            if ordem == 2:
                raise sqlite3.IntegrityError("FIXTURE")
            return real(banco, doc, tipo, ordem, **kwargs)
        with patch("analise.persistencia.criar_item_documental", side_effect=salvar):
            with self.assertRaises(sqlite3.IntegrityError):
                salvar_resultado_analise(self.b, 1, self.r)
        for tabela in ("documentos", "campos_extraidos", "itens_documentais", "campos_item"):
            self.assertEqual(self.b.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0], 0)

    def test_recuperacao_csv_reatribuicao_e_conferencia(self):
        doc = salvar_resultado_analise(self.b, 1, self.r)["documento_id"]
        titular = atribuicoes.criar_pessoa(self.b, 1)
        outra = atribuicoes.criar_pessoa(self.b, 1)
        atribuicoes.atribuir_documento(self.b, doc, titular)
        for tipo, pessoa in (
            ("DOCUMENTO_IDENTIDADE", titular), ("CIN", titular),
            ("CNH", titular), ("CERTIDAO_NASCIMENTO", titular), ("CNH", outra),
        ):
            ident = adicionar_documento(self.b, 1, "fixture.pdf", "fixtures/fixture.pdf", tipo)
            salvar_campo_extraido(self.b, ident, "NOME", "PESSOA FICTICIA")
            salvar_campo_extraido(self.b, ident, "CPF", self.cpf, status=STATUS_CPF_TITULAR)
            salvar_campo_extraido(self.b, ident, "DATA_NASCIMENTO", "01/01/2000")
            atribuicoes.atribuir_documento(self.b, ident, pessoa)
        resultados = [r for r in conferir_analise(self.b, 1) if doc in (
            r["documento_1_id"], r["documento_2_id"],
        )]
        self.assertEqual(len(resultados), 12)
        self.assertEqual({x["campo"] for x in resultados}, {"NOME", "CPF", "DATA_NASCIMENTO"})
        self.assertTrue(all((x["pessoa_id"], x["resultado"]) == (titular, "COMPATIVEL")
                            for x in resultados))
        item = itens_documentais.listar_itens_por_documento(self.b, doc)[0]["item_id"]
        itens_documentais.alterar_atribuicao_item(self.b, item, titular)
        atribuicoes.atribuir_documento(self.b, doc, outra)
        with self.b:
            self.b.execute("UPDATE analises SET status = 'CONCLUIDA' WHERE id = 1")
        dados, _ = recuperar_relatorio(self.b, self.codigo)
        self.assertEqual(len(dados["itens_documentais"]), 2)
        self.assertTrue(all(i["pessoa_id"] is None for i in dados["itens_documentais"]))
        self.assertTrue(any(c["campo"] == "NUMERO_CTPS" and c["valor"] == "000123"
                            for c in dados["campos_extraidos"]))
        with TemporaryDirectory() as tmp:
            destino = Path(tmp, "fixture.csv")
            exportar_analise_csv(self.b, destino, codigo=self.codigo)
            with destino.open(encoding="utf-8-sig", newline="") as arquivo:
                linhas = list(csv.reader(arquivo, delimiter=";"))
            self.assertIn([str(item), "ADMISSAO", "01/02/2020", "ENCONTRADO", "3", ""], linhas)
        self.assertEqual(self.b.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
