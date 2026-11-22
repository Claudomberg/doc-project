import csv
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf

from analise.analisador_documento import analisar_documento
from analise.conferidor_banco import conferir_analise
from analise.exportador import exportar_relatorio_csv
from analise.persistencia import salvar_resultado_analise
from analise.relatorio import recuperar_relatorio
from banco import conexao as banco
from leitor.leitor_pdf import ler_pdf
from teste_orientacao_pdf import OCRDeOrientacaoSimulado, _arte


_CPF = "00000000191"
_OUTRO_CPF = "00000000272"
_RG_COMPLETO = """CARTEIRA DE IDENTIDADE
NOME COMPLETO: TITULAR FICTICIO DA SILVA
CPF DO TITULAR: 000.000.001-91
RG: 12.345.678-9
DATA DE NASCIMENTO: 14/05/2001
DATA DE EXPEDICAO: 17/04/2035
VALIDADE: 17/04/2045
ORGAO EXPEDIDOR / UF: SSP/MA
NOME DA MAE: GENITORA FICTICIA DA SILVA
NOME DO PAI: GENITOR FICTICIO DA SILVA
NATURALIDADE: SAO LUIS - MA
NACIONALIDADE: BRASILEIRA"""
_CIN_COMPLETA = """CARTEIRA DE IDENTIDADE NACIONAL
NOME: TITULAR FICTICIO DA SILVA
REGISTRO GERAL - CPF: 000.000.001-91
DATA DE NASCIMENTO: 14/05/2001
DATA DE EXPEDICAO: 17/04/2035
DATA DE VALIDADE: 17/04/2045
ORGAO EMISSOR: SSP/MA
FILIACAO
GENITORA FICTICIA DA SILVA
GENITOR FICTICIO DA SILVA
NATURALIDADE: SAO LUIS - MA
NACIONALIDADE: BRASILEIRA"""


def analisar(*paginas, nome="sintetico.pdf"):
    preparadas = []
    for numero, pagina in enumerate(paginas, 1):
        if isinstance(pagina, str):
            dados = {"texto": pagina, "metodo": "DIGITAL"}
        else:
            dados = {"texto": "\n".join(l["texto"] for l in pagina), "linhas_ocr": pagina, "metodo": "OCR"}
        preparadas.append({"numero": numero, **dados})
    return analisar_documento({
        "nome_arquivo": nome, "caminho_origem": nome,
        "quantidade_paginas": len(preparadas), "paginas": preparadas,
    })


def linha(texto, x, y, largura=110, grupo=None):
    return {"texto": texto, "caixa": [x, y, x + largura, y + 13], "grupo_espacial": grupo}


class TesteExtracaoIdentidade(unittest.TestCase):
    def campos(self, *paginas):
        return analisar(*paginas)["campos"]["identidade"]

    def test_cin_rotulos_bilingues_frente_verso(self):
        frente = """CARTEIRA DE IDENTIDADE NACIONAL / NATIONAL IDENTITY CARD
NOME / NAME: PESSOA FICTICIA ALFA
REGISTRO GERAL - CPF / PERSONAL NUMBER
000.000.001-91
DATA DE NASCIMENTO / DATE OF BIRTH: 14/05/2001
NATURALIDADE / PLACE OF BIRTH: SAO LUIS - MA
NACIONALIDADE / NATIONALITY: BRASILEIRA"""
        verso = """DATA DE EMISSAO / DATE OF ISSUE: 17/04/2035
VALIDADE / EXPIRY DATE: 17/04/2045
FILIACAO / PARENTAGE: PESSOA FICTICIA BETA
ORGAO EXPEDIDOR / CARD ISSUER: SSP/MA"""
        resultado = analisar(frente, verso)
        self.assertEqual(resultado["identificacao"]["modelo_identidade"], "CIN")
        campos = resultado["campos"]["identidade"]
        esperados = {
            "NOME": ("PESSOA FICTICIA ALFA", 1), "CPF": (_CPF, 1),
            "DATA_NASCIMENTO": ("14/05/2001", 1), "NATURALIDADE": ("SAO LUIS - MA", 1),
            "NACIONALIDADE": ("BRASILEIRA", 1), "DATA_EXPEDICAO": ("17/04/2035", 2),
            "DATA_VALIDADE": ("17/04/2045", 2), "FILIACAO": ("PESSOA FICTICIA BETA", 2),
            "ORGAO_EMISSOR": ("SSP", 2), "UF_EMISSAO": ("MA", 2),
        }
        self.assertEqual({k: (v["valor"], v["pagina_origem"]) for k, v in campos.items()
                          if v["status"] == "ENCONTRADO"}, esperados)
        self.assertEqual(campos["RG"]["status"], "NAO_APLICAVEL")

    def test_cin_cpf_proximo_de_rg_controle_e_estados(self):
        titulo = "CARTEIRA DE IDENTIDADE NACIONAL\nNOME / NAME: PESSOA FICTICIA ALFA\n"
        for dado, status in (("", "NAO_ENCONTRADO"), ("REGISTRO GERAL - CPF / PERSONAL NUMBER: 00000000000\n", "INVALIDO")):
            campos = analisar(titulo + dado + "RG: 12.345.678-9\nNUMERO DE CONTROLE: " + _CPF)["campos"]["identidade"]
            self.assertEqual(campos["CPF"]["status"], status)
            self.assertIsNone(campos["RG"]["valor"])
        self.assertEqual(analisar(titulo + "CPF / PERSONAL NUMBER: " + _CPF)["campos"]["identidade"]["CPF"]["valor"], _CPF)

    def test_modelos_internos_sem_promover_desconhecido(self):
        rg = analisar(_RG_COMPLETO)["identificacao"]
        self.assertEqual((rg["tipo"], rg["modelo_identidade"]), ("DOCUMENTO_IDENTIDADE", "RG_TRADICIONAL"))
        incerto = analisar("CARTEIRA DE IDENTIDADE NACIONAL")["identificacao"]
        self.assertEqual((incerto["tipo"], incerto["modelo_identidade"]),
                         ("DOCUMENTO_DESCONHECIDO", "IDENTIDADE_MODELO_INDETERMINADO"))
        desconhecido = analisar("CPF / PERSONAL NUMBER: " + _CPF)["identificacao"]
        self.assertEqual(desconhecido["tipo"], "DOCUMENTO_DESCONHECIDO")
        self.assertNotIn("modelo_identidade", desconhecido)

    def test_rg_tradicional_com_campos_completos(self):
        campos = self.campos(_RG_COMPLETO)
        esperados = {
            "NOME": "TITULAR FICTICIO DA SILVA", "CPF": _CPF, "RG": "12.345.678-9",
            "DATA_NASCIMENTO": "14/05/2001", "DATA_EXPEDICAO": "17/04/2035",
            "DATA_VALIDADE": "17/04/2045", "ORGAO_EMISSOR": "SSP", "UF_EMISSAO": "MA",
            "NOME_MAE": "GENITORA FICTICIA DA SILVA", "NOME_PAI": "GENITOR FICTICIO DA SILVA",
            "NATURALIDADE": "SAO LUIS - MA", "NACIONALIDADE": "BRASILEIRA",
        }
        self.assertEqual({k: v["valor"] for k, v in campos.items() if v["status"] == "ENCONTRADO"}, esperados)

    def test_rg_antigo_sem_cpf_ou_validade(self):
        campos = self.campos("CEDULA DE IDENTIDADE\nRG: 12345678\nNOME: TITULAR FICTICIO")
        self.assertEqual(campos["RG"]["status"], "ENCONTRADO")
        for campo in ("CPF", "DATA_VALIDADE", "NACIONALIDADE"):
            self.assertEqual(campos[campo]["status"], "NAO_ENCONTRADO")

    def test_rg_com_formatos_estaduais_e_digito_alfanumerico(self):
        for numero in ("12.345.678-X", "012345678", "MG-12.345.678", "12 345 678-X"):
            with self.subTest(formato=numero):
                campos = self.campos(f"CARTEIRA DE IDENTIDADE\nREGISTRO GERAL: {numero}")
                self.assertEqual(campos["RG"]["valor"], numero)

    def test_rg_nao_aceita_cpf_valido_ou_numero_truncado(self):
        for numero in (_CPF, "1234567890123456", f"MG-{_CPF}"):
            campos = self.campos(f"CARTEIRA DE IDENTIDADE\nRG: {numero}")
            self.assertEqual(campos["RG"]["status"], "INVALIDO")
            self.assertIsNone(campos["RG"]["valor"])

    def test_sigla_uf_e_naturalidade_tem_contextos_distintos(self):
        campos = self.campos(
            "CARTEIRA DE IDENTIDADE\nRG: 12345678\nORGAO EXPEDIDOR / UF: IIRGD/SP\nNATURALIDADE: SAO LUIS / MA"
        )
        self.assertEqual(campos["ORGAO_EMISSOR"]["valor"], "IIRGD")
        self.assertEqual(campos["UF_EMISSAO"]["valor"], "SP")
        self.assertEqual(campos["NATURALIDADE"]["valor"], "SAO LUIS / MA")

    def test_uf_solto_nao_e_atribuido_ao_orgao(self):
        campos = self.campos("CARTEIRA DE IDENTIDADE\nRG: 12345678\nNATURALIDADE: RECIFE\nUF: PE")
        self.assertEqual(campos["UF_EMISSAO"]["status"], "NAO_ENCONTRADO")

    def test_orgao_e_uf_em_caixas_distintas_no_mesmo_cabecalho(self):
        campos = self.campos([
            linha("CARTEIRA DE IDENTIDADE", 0, 0, 330), linha("RG: 12345678", 0, 30, 120),
            linha("ORGAO EMISSOR / UF", 0, 65, 155),
            linha("SSP", 0, 91, 32), linha("MA", 49, 91, 27),
        ])
        self.assertEqual(campos["ORGAO_EMISSOR"]["valor"], "SSP")
        self.assertEqual(campos["UF_EMISSAO"]["valor"], "MA")

    def test_filiacao_sem_papeis_preserva_nomes_sem_inferir_parentesco(self):
        campos = self.campos(
            "CARTEIRA DE IDENTIDADE\nRG: 12345678\nFILIACAO\nPRIMEIRA PESSOA FICTICIA\n"
            "SEGUNDA PESSOA FICTICIA\nNATURALIDADE: RECIFE"
        )
        self.assertEqual(campos["FILIACAO"]["valor"], "PRIMEIRA PESSOA FICTICIA; SEGUNDA PESSOA FICTICIA")
        for campo in ("NOME", "NOME_MAE", "NOME_PAI"):
            self.assertEqual(campos[campo]["status"], "NAO_ENCONTRADO")

    def test_frente_e_verso_preservam_pagina_do_valor(self):
        campos = self.campos(
            "CARTEIRA DE IDENTIDADE\nNOME: TITULAR FICTICIO\nRG: 12345678",
            "EXPEDICAO: 17/04/2035\nNOME DA MAE: PESSOA FICTICIA\nORGAO EMISSOR: SSP / MA",
        )
        self.assertEqual(campos["NOME"]["pagina_origem"], 1)
        for campo in ("DATA_EXPEDICAO", "NOME_MAE", "ORGAO_EMISSOR", "UF_EMISSAO"):
            self.assertEqual(campos[campo]["pagina_origem"], 2)

    def test_rotulo_nome_dentro_do_bloco_de_filiacao_nao_significa_titular(self):
        campos = self.campos(
            "CARTEIRA DE IDENTIDADE\nRG: 12345678\nFILIACAO\nNOME: PRIMEIRA PESSOA FICTICIA\n"
            "NOME: SEGUNDA PESSOA FICTICIA\nNOME DO TITULAR: TITULAR FICTICIO"
        )
        self.assertEqual(campos["FILIACAO"]["valor"], "PRIMEIRA PESSOA FICTICIA; SEGUNDA PESSOA FICTICIA")
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO")

    def test_cin_combina_rg_e_cpf_sem_inventar_rg_legado(self):
        campos = self.campos(_CIN_COMPLETA)
        self.assertEqual(campos["CPF"]["valor"], _CPF)
        self.assertEqual(campos["CPF"]["status"], "ENCONTRADO")
        self.assertEqual(campos["RG"]["status"], "NAO_APLICAVEL")
        self.assertIsNone(campos["RG"]["valor"])
        self.assertEqual(campos["FILIACAO"]["status"], "ENCONTRADO")
        self.assertEqual(campos["DATA_VALIDADE"]["status"], "ENCONTRADO")

    def test_cin_com_cabecalho_generico_e_registro_com_cpf_entre_parenteses(self):
        resultado = analisar(f"CARTEIRA DE IDENTIDADE\nREGISTRO GERAL (CPF): {_CPF}")
        self.assertEqual(resultado["identificacao"]["tipo"], "CIN")
        self.assertEqual(resultado["campos"]["identidade"]["CPF"]["valor"], _CPF)

    def test_validade_indeterminada_explicita_e_nao_aplicavel(self):
        campos = self.campos("CARTEIRA DE IDENTIDADE\nRG: 12345678\nVALIDADE: INDETERMINADA")
        self.assertEqual(campos["DATA_VALIDADE"]["status"], "NAO_APLICAVEL")

    def test_cin_confirma_cpf_rotulado_com_contexto_do_titular(self):
        resultado = analisar(f"CARTEIRA DE IDENTIDADE NACIONAL\nNOME: TITULAR FICTICIO\nCPF: {_CPF}")
        cpf, = resultado["campos"]["cpfs"]
        self.assertTrue(cpf["valido"])
        self.assertEqual(cpf["papel"], "TITULAR")

    def test_cin_sem_campos_opcionais_e_sem_cpf_confirmado(self):
        campos = self.campos("CARTEIRA DE IDENTIDADE NACIONAL\nNOME: TITULAR FICTICIO\nCPF")
        self.assertEqual(campos["CPF"]["status"], "EVIDENCIA_INSUFICIENTE")
        self.assertEqual(campos["RG"]["status"], "NAO_APLICAVEL")
        self.assertEqual(campos["DATA_VALIDADE"]["status"], "NAO_ENCONTRADO")
        self.assertEqual(campos["FILIACAO"]["status"], "NAO_ENCONTRADO")

    def test_controle_e_cpf_de_responsavel_nao_vencem_identificador_da_cin(self):
        campos = self.campos(
            f"CARTEIRA DE IDENTIDADE NACIONAL\nNOME: TITULAR FICTICIO\nCONTROLE: {_OUTRO_CPF}\n"
            f"REGISTRO GERAL (CPF): {_CPF}\nCPF DA MAE: {_OUTRO_CPF}"
        )
        self.assertEqual(campos["CPF"]["valor"], _CPF)
        self.assertEqual(campos["CPF"]["status"], "ENCONTRADO")

    def test_cpf_em_bloco_de_filiacao_nao_prova_titularidade(self):
        campos = self.campos(
            f"CARTEIRA DE IDENTIDADE NACIONAL\nNOME: TITULAR FICTICIO\nFILIACAO: PESSOA FICTICIA\nCPF: {_CPF}"
        )
        self.assertEqual(campos["CPF"]["status"], "EVIDENCIA_INSUFICIENTE")

    def test_duas_evidencias_de_titularidade_conflitantes_exigem_revisao(self):
        campos = self.campos(
            f"CARTEIRA DE IDENTIDADE NACIONAL\nREGISTRO GERAL - CPF: {_CPF}\nCPF DO TITULAR: {_OUTRO_CPF}"
        )
        self.assertEqual(campos["CPF"]["status"], "AMBIGUO")
        self.assertIsNone(campos["CPF"]["valor"])

    def test_datas_proximas_na_mesma_linha_com_rotulos_separados(self):
        campos = self.campos(
            "CARTEIRA DE IDENTIDADE\nRG: 12345678\n"
            "NASCIMENTO: 14/05/2001 EXPEDICAO: 01/04/2035 VALIDADE: 02/04/2035"
        )
        self.assertEqual([campos[k]["valor"] for k in ("DATA_NASCIMENTO", "DATA_EXPEDICAO", "DATA_VALIDADE")],
                         ["14/05/2001", "01/04/2035", "02/04/2035"])

    def test_espacos_de_layout_dentro_de_um_rotulo_nao_vazam_para_o_valor(self):
        campos = self.campos(
            f"CARTEIRA DE IDENTIDADE NACIONAL\nNOME   COMPLETO: TITULAR FICTICIO\n"
            f"REGISTRO   GERAL - CPF: {_CPF}"
        )
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO")
        self.assertEqual(campos["CPF"]["valor"], _CPF)

    def test_campos_conflitantes_e_invalido_tem_estados_distintos(self):
        campos = self.campos(
            "CARTEIRA DE IDENTIDADE\nRG: 12345678\nRG: 87654321\n"
            "NOME: PRIMEIRA PESSOA\nNOME: SEGUNDA PESSOA\n"
            "EXPEDICAO: 17/04/2035\nEXPEDICAO: 18/04/2035\nNASCIMENTO: 31/02/2001"
        )
        for campo in ("NOME", "RG", "DATA_EXPEDICAO"):
            self.assertEqual(campos[campo]["status"], "AMBIGUO")
            self.assertIsNone(campos[campo]["valor"])
        self.assertEqual(campos["DATA_NASCIMENTO"]["status"], "INVALIDO")

    def test_rotulos_fragmentados_e_arranjos_horizontal_e_vertical(self):
        campos = self.campos([
            linha("CARTEIRA DE IDENTIDADE", 10, 5, 330),
            linha("RG: 12345678", 10, 35),
            linha("NOME", 10, 65, 38), linha("COMPLETO", 51, 65, 60),
            linha("TITULAR FICTICIO", 124, 65, 125),
            linha("ORGAO", 10, 110, 56), linha("EMISSOR", 69, 110, 65),
            linha("SSP/MA", 15, 135, 61),
        ])
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO")
        self.assertEqual(campos["ORGAO_EMISSOR"]["valor"], "SSP")
        self.assertEqual(campos["UF_EMISSAO"]["valor"], "MA")

    def test_regioes_independentes_nao_cruzam_rotulo_e_valor(self):
        campos = self.campos([
            linha("CARTEIRA DE IDENTIDADE", 0, 0, 250, 1), linha("RG: 12345678", 0, 25, 100, 1),
            linha("NATURALIDADE", 0, 70, 98, 1), linha("SAO LUIS", 108, 70, 100, 2),
        ])
        self.assertIsNone(campos["NATURALIDADE"]["valor"])

    def test_dois_nomes_de_filiacao_com_geometria(self):
        campos = self.campos([
            linha("CARTEIRA DE IDENTIDADE NACIONAL", 0, 0, 400),
            linha(f"REGISTRO GERAL - CPF: {_CPF}", 0, 25, 300),
            linha("FILIACAO", 0, 55, 80),
            linha("PRIMEIRA PESSOA FICTICIA", 0, 82, 195),
            linha("SEGUNDA PESSOA FICTICIA", 0, 110, 195),
        ])
        self.assertEqual(campos["FILIACAO"]["valor"], "PRIMEIRA PESSOA FICTICIA; SEGUNDA PESSOA FICTICIA")

    def test_pdf_fisicamente_invertido_misto_preserva_rodape_e_original(self):
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "sintetico.pdf"
            with pymupdf.open() as pdf:
                p = pdf.new_page(width=240, height=205)
                p.insert_image(pymupdf.Rect(0, 0, 240, 160), stream=_arte(), rotate=180)
                p.insert_text((10, 190), "RODAPE FICTICIO")
                pdf.save(arquivo)
            antes = arquivo.read_bytes()
            lido = ler_pdf(arquivo, leitor_ocr=OCRDeOrientacaoSimulado())
            self.assertEqual(arquivo.read_bytes(), antes)
        self.assertEqual(lido["paginas"][0]["orientacoes"][0]["angulo_aplicado"], 180)
        self.assertTrue(lido["paginas"][0]["texto"].endswith("RODAPE FICTICIO"))
        campos = analisar_documento(lido)["campos"]["identidade"]
        for campo in ("CPF", "RG", "DATA_EXPEDICAO"):
            self.assertEqual(campos[campo]["status"], "ENCONTRADO")


class TestePersistenciaIdentidade(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(closing(banco.conectar_banco(":memory:")))
        banco.criar_tabelas(self.con)
        self.codigo = banco.criar_analise(self.con)
        self.ident = self.con.execute("SELECT id FROM analises").fetchone()[0]

    def salvar(self, texto, nome="sintetico.pdf"):
        return salvar_resultado_analise(self.con, self.ident, analisar(texto, nome=nome))["documento_id"]

    def test_persistencia_recuperacao_e_csv_sem_trechos_de_identidade(self):
        self.salvar(_CIN_COMPLETA)
        with self.con:
            self.con.execute("UPDATE analises SET status='CONCLUIDA' WHERE id=?", (self.ident,))
        relatorio, _ = recuperar_relatorio(self.con, self.codigo)
        registros = {item["campo"]: item for item in relatorio["campos_extraidos"]}
        self.assertNotIn("RG", registros)
        self.assertEqual(registros["CPF"]["status"], "TITULAR_CONFIRMADO")
        self.assertTrue(all(item["trecho_origem"] is None for item in registros.values()))
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "relatorio.csv"
            exportar_relatorio_csv(relatorio, arquivo)
            with arquivo.open(encoding="utf-8-sig", newline="") as f:
                self.assertTrue(any("ORGAO_EMISSOR" in linha for linha in csv.reader(f, delimiter=";")))

    def test_persistencia_de_ambiguidade_sem_valores_nem_identificadores_de_controle(self):
        self.salvar(
            f"CARTEIRA DE IDENTIDADE NACIONAL\nREGISTRO GERAL - CPF: {_CPF}\n"
            f"CPF DO TITULAR: {_OUTRO_CPF}\nCONTROLE: 12345678901"
        )
        linhas = [(row[0], row[1], row[2]) for row in self.con.execute(
            "SELECT nome_campo, valor, status FROM campos_extraidos"
        )]
        self.assertEqual(linhas, [("CPF", None, "AMBIGUO")])

    def test_rg_e_cin_mesmo_titular_conferem_sem_comparar_identificadores_diferentes(self):
        self.salvar(_RG_COMPLETO, "rg.pdf")
        self.salvar(_CIN_COMPLETA, "cin.pdf")
        resultado = conferir_analise(self.con, self.ident)
        self.assertEqual({(r["campo"], r["resultado"]) for r in resultado}, {
            ("NOME", "COMPATIVEL"), ("CPF", "COMPATIVEL"), ("DATA_NASCIMENTO", "COMPATIVEL"),
        })

    def test_rg_sem_cpf_contra_cin_nao_gera_divergencia(self):
        self.salvar("CARTEIRA DE IDENTIDADE\nRG: 12345678\nNOME: TITULAR FICTICIO DA SILVA", "rg.pdf")
        self.salvar(_CIN_COMPLETA, "cin.pdf")
        resultado = conferir_analise(self.con, self.ident)
        self.assertIn(("CPF", "NAO_CONFERIDO"), {(r["campo"], r["resultado"]) for r in resultado})
        self.assertTrue(all(r["resultado"] != "DIVERGENTE" for r in resultado))


if __name__ == "__main__":
    unittest.main()
