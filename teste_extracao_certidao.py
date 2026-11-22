import csv
import hashlib
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf

from analise.analisador_documento import analisar_documento
from analise.conferidor_banco import conferir_analise
from analise.datas_extenso import interpretar_data
from analise.exportador import exportar_relatorio_csv
from analise.persistencia import salvar_resultado_analise
from analise.relatorio import recuperar_relatorio
from banco import conexao as banco
from leitor.leitor_pdf import ler_pdf
from teste_extracao_cnh import _ANTIGA
from teste_extracao_identidade import _CIN_COMPLETA, _RG_COMPLETO, analisar, linha
from teste_orientacao_pdf import OCRDeOrientacaoSimulado, _arte


_CPF = "00000000191"
_CPF_TERCEIRO = "00000000272"
_CABECALHO = "CERTIDAO DE NASCIMENTO\nREGISTRO CIVIL DAS PESSOAS NATURAIS"
_MODERNA = f"""{_CABECALHO}
NOME DO REGISTRADO: TITULAR FICTICIO DA SILVA
CPF DO REGISTRADO: {_CPF}
DATA DE NASCIMENTO: 14/05/2001
DATA DE REGISTRO: 16/05/2001
DATA DE EMISSAO: 17/04/2023
MATRICULA: 000001 01 55 2001 1 00001 001 0000001 00
NOME DA MAE: PARENTE FICTICIA DA SILVA
NOME DO PAI: PARENTE FICTICIO DA SILVA
NATURALIDADE: RECIFE - PE
CARTORIO: PRIMEIRO OFICIO DE REGISTRO CIVIL
LIVRO: A-0001
FOLHA: 002
TERMO: 00003
MUNICIPIO DO REGISTRO / UF: SAO LUIS - MA"""
_NARRATIVA = """REGISTRO CIVIL DAS PESSOAS NATURAIS
CERTIFICO QUE AOS QUINZE DIAS DO MES DE MARCO DO ANO DE DOIS MIL E SEIS,
NASCEU TITULAR FICTICIO DA SILVA, NATURAL DE RECIFE, FILHA DE PARENTE FICTICIA E DE PARENTE FICTICIO.
REGISTRADA EM DEZESSETE DE MARCO DE DOIS MIL E SEIS.
CERTIDAO EMITIDA EM PRIMEIRO DE JANEIRO DE DOIS MIL E VINTE.
LAVRADO NO CARTORIO FICTICIO DE REGISTRO CIVIL, LIVRO A-0001, FOLHA 002, TERMO 00003.
REGISTRO EFETUADO NO MUNICIPIO DE SAO LUIS - MA."""


class TesteDatasEmPortugues(unittest.TestCase):
    def test_exemplos_numericos_e_por_extenso(self):
        for texto, esperado in (
            ("15/03/2006", "15/03/2006"),
            ("15 de março de 2006", "15/03/2006"),
            ("quinze de março de dois mil e seis", "15/03/2006"),
            ("aos quinze dias do mês de março do ano de dois mil e seis", "15/03/2006"),
            ("aos quinze dias de março de dois mil e seis", "15/03/2006"),
            ("primeiro de janeiro de mil novecentos e noventa e oito", "01/01/1998"),
            ("vinte e nove de fevereiro de dois mil", "29/02/2000"),
            ("treze de outubro de mil oitocentos e noventa e nove", "13/10/1899"),
        ):
            with self.subTest(data=texto):
                self.assertEqual(interpretar_data(texto), (esperado, True))

    def test_datas_invalidas_por_calendario(self):
        self.assertEqual(interpretar_data("31/02/2006"), ("31/02/2006", False))
        self.assertEqual(interpretar_data("trinta e um de fevereiro de dois mil e seis"), ("31/02/2006", False))
        self.assertEqual(interpretar_data("vinte e nove de fevereiro de mil e novecentos"), ("29/02/1900", False))

    def test_numeros_incompletos_e_erros_de_ocr_nao_sao_corrigidos(self):
        for texto in ("l5/03/2006", "15/03/200", "quinze de março de dois mil e",
                      "quinze de março", "quinze de marco de dois mil cento", "vinte e vinte de marco de 2006"):
            with self.subTest(data=texto):
                self.assertEqual(interpretar_data(texto), (None, False))


class TesteExtracaoCertidao(unittest.TestCase):
    def campos(self, *paginas):
        resultado = analisar(*paginas)
        self.assertEqual(resultado["identificacao"]["tipo"], "CERTIDAO_NASCIMENTO")
        return resultado["campos"]["certidao_nascimento"]

    def test_estruturada_moderna(self):
        campos = self.campos(_MODERNA)
        esperados = {
            "NOME": "TITULAR FICTICIO DA SILVA", "CPF": _CPF, "DATA_NASCIMENTO": "14/05/2001",
            "DATA_REGISTRO": "16/05/2001", "DATA_EMISSAO": "17/04/2023",
            "NOME_MAE": "PARENTE FICTICIA DA SILVA", "NOME_PAI": "PARENTE FICTICIO DA SILVA",
            "NATURALIDADE": "RECIFE - PE", "CARTORIO": "PRIMEIRO OFICIO DE REGISTRO CIVIL",
            "MUNICIPIO_REGISTRO": "SAO LUIS", "UF_REGISTRO": "MA",
            "LIVRO": "A-0001", "FOLHA": "002", "TERMO": "00003",
        }
        for campo, valor in esperados.items():
            with self.subTest(campo=campo):
                self.assertEqual(campos[campo]["valor"], valor)
                self.assertEqual(campos[campo]["status"], "ENCONTRADO")

    def test_certidao_narrativa_antiga_com_nascimento_registro_e_emissao(self):
        campos = self.campos(_NARRATIVA)
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO DA SILVA")
        self.assertEqual([campos[c]["valor"] for c in ("DATA_NASCIMENTO", "DATA_REGISTRO", "DATA_EMISSAO")],
                         ["15/03/2006", "17/03/2006", "01/01/2020"])
        self.assertEqual(campos["CARTORIO"]["valor"], "CARTORIO FICTICIO DE REGISTRO CIVIL")
        self.assertEqual(campos["MUNICIPIO_REGISTRO"]["valor"], "SAO LUIS")
        self.assertEqual(campos["UF_REGISTRO"]["valor"], "MA")

    def test_certidao_sem_cpf_e_sem_matricula_moderna(self):
        campos = self.campos(_NARRATIVA)
        for campo in ("CPF", "MATRICULA"):
            self.assertEqual(campos[campo]["status"], "NAO_ENCONTRADO")
        self.assertEqual(campos["TERMO"]["status"], "ENCONTRADO")

    def test_matricula_e_codigos_antigos_preservam_zeros(self):
        campos = self.campos(_MODERNA)
        self.assertTrue(campos["MATRICULA"]["valor"].startswith("000001"))
        self.assertIsInstance(campos["MATRICULA"]["valor"], str)
        self.assertEqual(campos["FOLHA"]["valor"], "002")
        self.assertEqual(campos["TERMO"]["valor"], "00003")

    def test_matricula_nao_e_cpf_nem_selo(self):
        campos = self.campos(
            f"{_CABECALHO}\nMATRICULA: {_CPF}\nSELO DIGITAL: 00000333333333333333333333333322"
        )
        self.assertEqual(campos["MATRICULA"]["status"], "EVIDENCIA_INSUFICIENTE")
        self.assertIsNone(campos["MATRICULA"]["valor"])
        self.assertIsNone(campos["CPF"]["valor"])

    def test_livro_folha_e_termo_em_texto_corrido(self):
        campos = self.campos(_NARRATIVA)
        self.assertEqual([campos[c]["valor"] for c in ("LIVRO", "FOLHA", "TERMO")],
                         ["A-0001", "002", "00003"])

    def test_titularidade_do_cpf_e_bloco_de_terceiros(self):
        campos = self.campos(
            f"{_CABECALHO}\nDECLARANTE\nNOME: DECLARANTE FICTICIO\nCPF: {_CPF_TERCEIRO}\n"
            "NASCIMENTO: 01/02/1970\nNATURALIDADE: RECIFE\n"
            f"NOME DO REGISTRADO: TITULAR FICTICIO\nCPF DO REGISTRADO: {_CPF}"
        )
        self.assertEqual(campos["CPF"]["valor"], _CPF)
        self.assertEqual(campos["CPF"]["status"], "ENCONTRADO")
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO")
        self.assertIsNone(campos["DATA_NASCIMENTO"]["valor"])
        self.assertIsNone(campos["NATURALIDADE"]["valor"])

    def test_cpf_isolado_valido_nao_confirma_titularidade(self):
        campos = self.campos(f"{_CABECALHO}\nCPF: {_CPF}")
        self.assertEqual(campos["CPF"]["status"], "EVIDENCIA_INSUFICIENTE")

    def test_cpf_rotulado_dentro_dos_dados_do_registrado_tem_vinculo(self):
        campos = self.campos(
            f"{_CABECALHO}\nDADOS DO REGISTRADO\nNOME: TITULAR FICTICIO\nCPF: {_CPF}\n"
            f"DECLARANTE\nNOME: PARENTE FICTICIO\nCPF: {_CPF_TERCEIRO}"
        )
        self.assertEqual(campos["CPF"]["valor"], _CPF)
        self.assertEqual(campos["CPF"]["status"], "ENCONTRADO")

    def test_terceiros_sem_titular_identificado_nao_preenchem_nome(self):
        campos = self.campos(
            f"{_CABECALHO}\nAVOS MATERNOS: PARENTE FICTICIO\n"
            "TESTEMUNHA\nNOME: PESSOA FICTICIA\nOFICIAL: SERVIDOR FICTICIO"
        )
        self.assertEqual(campos["NOME"]["status"], "NAO_ENCONTRADO")

    def test_narrativa_distingue_registrado_do_primeiro_nome_e_do_cpf_do_pai(self):
        campos = self.campos(
            f"{_CABECALHO}\nCompareceu declarante ficticio inscrito no CPF {_CPF_TERCEIRO}. "
            f"Em 15/03/2006 nasceu a crianca Titular Ficticio, inscrita no CPF sob o n. {_CPF}. "
            "O pai, nascido em 12/09/1970, declarou o nascimento."
        )
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO")
        self.assertEqual(campos["CPF"]["valor"], _CPF)
        self.assertEqual(campos["DATA_NASCIMENTO"]["valor"], "15/03/2006")

    def test_nomes_de_filiacao_preservados_sem_inferir_papeis(self):
        campos = self.campos(_NARRATIVA)
        self.assertEqual(campos["FILIACAO"]["valor"], "PARENTE FICTICIA; PARENTE FICTICIO")
        self.assertIsNone(campos["NOME_MAE"]["valor"])
        self.assertIsNone(campos["NOME_PAI"]["valor"])

    def test_nascimento_registro_e_emissao_em_colunas_proximas(self):
        campos = self.campos([
            linha("CERTIDAO DE NASCIMENTO", 5, 5, 335),
            linha("REGISTRO CIVIL", 5, 29, 160),
            linha("DATA DE NASCIMENTO", 5, 62, 133),
            linha("DATA DE REGISTRO", 155, 62, 130),
            linha("DATA DE EMISSAO", 306, 62, 126),
            linha("15/03/2006", 9, 87, 90),
            linha("17/03/2006", 162, 87, 90),
            linha("01/01/2020", 313, 87, 90),
        ])
        self.assertEqual([campos[c]["valor"] for c in ("DATA_NASCIMENTO", "DATA_REGISTRO", "DATA_EMISSAO")],
                         ["15/03/2006", "17/03/2006", "01/01/2020"])

    def test_municipio_de_nascimento_nao_fornece_uf_do_registro(self):
        campos = self.campos(f"{_CABECALHO}\nNATURALIDADE: RECIFE - PE\nMUNICIPIO DO REGISTRO: SAO LUIS")
        self.assertEqual(campos["NATURALIDADE"]["valor"], "RECIFE - PE")
        self.assertEqual(campos["MUNICIPIO_REGISTRO"]["valor"], "SAO LUIS")
        self.assertIsNone(campos["UF_REGISTRO"]["valor"])

    def test_averbacao_separa_nome_e_nascimento_conflitantes(self):
        campos = self.campos(
            _MODERNA + "\nAVERBACAO: PASSA A SE CHAMAR SEGUNDO TITULAR FICTICIO.\n"
                       "NASCIMENTO: 31/05/2001\nEMITIDA EM 12/06/2030."
        )
        self.assertEqual(campos["AVERBACAO"]["valor"], "PRESENTE")
        self.assertEqual(campos["REVISAO_AVERBACAO"]["status"], "AMBIGUO")
        self.assertEqual(campos["NOME"]["status"], "AMBIGUO")
        self.assertEqual(campos["DATA_NASCIMENTO"]["status"], "AMBIGUO")
        self.assertIsNone(campos["DATA_NASCIMENTO"]["valor"])

    def test_data_avulsa_da_averbacao_nao_vira_data_de_emissao(self):
        campos = self.campos(
            f"{_CABECALHO}\nEMISSAO: 01/01/2020\nAVERBACAO: ALTERACAO POSTERIOR EM 19/09/2022."
        )
        self.assertEqual(campos["REVISAO_AVERBACAO"]["status"], "EVIDENCIA_INSUFICIENTE")
        self.assertEqual(campos["DATA_EMISSAO"]["valor"], "01/01/2020")

    def test_averbacoes_nada_consta_nao_criam_evento_e_emissao_e_lida(self):
        campos = self.campos(f"{_CABECALHO}\nAVERBACOES: NADA CONSTA.\nEMISSAO: 01/01/2020")
        self.assertEqual(campos["AVERBACAO"]["status"], "NAO_ENCONTRADO")
        self.assertEqual(campos["DATA_EMISSAO"]["valor"], "01/01/2020")

    def test_documento_incompleto_preserva_estados_distintos(self):
        campos = self.campos(f"{_CABECALHO}\nNOME DO REGISTRADO\nDATA DE NASCIMENTO: 15/03/200")
        self.assertEqual(campos["NOME"]["status"], "EVIDENCIA_INSUFICIENTE")
        self.assertEqual(campos["DATA_NASCIMENTO"]["status"], "EVIDENCIA_INSUFICIENTE")
        self.assertEqual(campos["MATRICULA"]["status"], "NAO_ENCONTRADO")

    def test_datas_invalidas_e_inversao_cronologica_sao_tipos_de_revisao_distintos(self):
        campos = self.campos(
            f"{_CABECALHO}\nNASCIMENTO: TRINTA E UM DE FEVEREIRO DE DOIS MIL E SEIS\n"
            "REGISTRO EM: 01/01/2021\nEMISSAO: 01/01/2020"
        )
        self.assertEqual(campos["DATA_NASCIMENTO"]["status"], "INVALIDO")
        self.assertEqual(campos["CRONOLOGIA_CERTIDAO"]["status"], "INCONSISTENCIA_CRONOLOGICA")
        self.assertEqual(campos["DATA_REGISTRO"]["valor"], "01/01/2021")
        self.assertEqual(campos["DATA_EMISSAO"]["valor"], "01/01/2020")

    def test_segunda_via_tem_sua_emissao_sem_alterar_registro_antigo(self):
        campos = self.campos(
            "CERTIDAO DE NASCIMENTO - SEGUNDA VIA\nREGISTRO CIVIL\n"
            "LAVRADA EM PRIMEIRO DE JANEIRO DE MIL NOVECENTOS E NOVENTA E OITO.\n"
            "EXPEDIDA EM 15 DE MARCO DE 2006."
        )
        self.assertEqual(campos["DATA_REGISTRO"]["valor"], "01/01/1998")
        self.assertEqual(campos["DATA_EMISSAO"]["valor"], "15/03/2006")
        self.assertEqual(campos["DATA_NASCIMENTO"]["status"], "NAO_ENCONTRADO")

    def test_rotulos_fragmentados_e_sem_posicao_absoluta(self):
        campos = self.campos([
            linha("CERTIDAO DE NASCIMENTO", 210, 95, 290), linha("REGISTRO CIVIL", 210, 117, 150),
            linha("DATA DE", 210, 154, 60), linha("REGISTRO", 274, 154, 75),
            linha("17/03/2006", 221, 182, 120),
        ])
        self.assertEqual(campos["DATA_REGISTRO"]["valor"], "17/03/2006")

    def test_pdf_de_tres_paginas_preserva_origem_dos_campos(self):
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "certidao-sintetica.pdf"
            with pymupdf.open() as pdf:
                for linhas in (
                    ("CERTIDAO DE NASCIMENTO", "REGISTRO CIVIL", "NOME DO REGISTRADO: TITULAR FICTICIO"),
                    ("DATA DE REGISTRO: 17/03/2006",),
                    ("CARTORIO: PRIMEIRO OFICIO DE REGISTRO CIVIL",),
                ):
                    pagina = pdf.new_page()
                    for i, texto in enumerate(linhas):
                        pagina.insert_text((28, 46 + i * 23), texto)
                pdf.save(arquivo)
            antes = arquivo.read_bytes()
            documento = ler_pdf(arquivo, usar_ocr=False)
            self.assertEqual(arquivo.read_bytes(), antes)
        campos = analisar_documento(documento)["campos"]["certidao_nascimento"]
        self.assertEqual([campos[c]["pagina_origem"] for c in ("NOME", "DATA_REGISTRO", "CARTORIO")], [1, 2, 3])

    def test_pdf_misto_rotacionado_preserva_rodape_campos_e_original(self):
        class Simulado(OCRDeOrientacaoSimulado):
            def reconhecer(self, imagem):
                self.leituras += 1
                if self._correcao(imagem):
                    return {"linhas": []}
                return {"linhas": [linha(t, 31, 28 + 22 * i, 355)
                                   for i, t in enumerate(_MODERNA.splitlines()[:8])]}

        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "certidao-sintetica.pdf"
            with pymupdf.open() as pdf:
                p = pdf.new_page(width=240, height=205)
                p.insert_image(pymupdf.Rect(0, 0, 240, 160), stream=_arte(), rotate=180)
                p.insert_text((10, 190), "RODAPE DIGITAL FICTICIO")
                pdf.save(arquivo)
            antes = hashlib.sha256(arquivo.read_bytes()).digest()
            documento = ler_pdf(arquivo, leitor_ocr=Simulado())
            self.assertEqual(hashlib.sha256(arquivo.read_bytes()).digest(), antes)
        pagina, = documento["paginas"]
        self.assertEqual(pagina["orientacoes"][0]["angulo_aplicado"], 180)
        self.assertTrue(pagina["texto"].endswith("RODAPE DIGITAL FICTICIO"))
        campos = analisar_documento(documento)["campos"]["certidao_nascimento"]
        for campo in ("CPF", "MATRICULA", "DATA_NASCIMENTO", "DATA_REGISTRO"):
            self.assertEqual(campos[campo]["status"], "ENCONTRADO")


class TestePersistenciaCertidao(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(closing(banco.conectar_banco(":memory:")))
        banco.criar_tabelas(self.con)
        self.codigo = banco.criar_analise(self.con)
        self.ident = self.con.execute("SELECT id FROM analises").fetchone()[0]

    def salvar(self, texto, arquivo):
        return salvar_resultado_analise(
            self.con, self.ident, analisar(texto, nome=arquivo),
        )["documento_id"]

    def recuperar(self):
        with self.con:
            self.con.execute("UPDATE analises SET status='CONCLUIDA' WHERE id=?", (self.ident,))
        return recuperar_relatorio(self.con, self.codigo)[0]

    def test_estados_recuperados_e_no_csv_sem_texto_de_averbacao(self):
        texto = (f"{_CABECALHO}\nNOME DO REGISTRADO: TITULAR FICTICIO\n"
                 "DATA DE NASCIMENTO: 31/02/2006\nCARTORIO\n"
                 "AVERBACAO: PASSOU A SE CHAMAR SEGUNDO TITULAR FICTICIO.")
        ident = self.salvar(texto, "cert-sintetica.pdf")
        relatorio = self.recuperar()
        estados = {c["campo"]: c["status"] for c in relatorio["estados_campos"] if c["documento_id"] == ident}
        esperados = {"CPF": "NAO_ENCONTRADO", "NOME": "AMBIGUO", "DATA_NASCIMENTO": "INVALIDO",
                     "CARTORIO": "EVIDENCIA_INSUFICIENTE", "AVERBACAO": "ENCONTRADO", "REVISAO_AVERBACAO": "AMBIGUO"}
        self.assertEqual({k: estados[k] for k in esperados}, esperados)
        for campo in relatorio["campos_extraidos"]:
            self.assertIsNone(campo["trecho_origem"])
            self.assertNotIn("SEGUNDO TITULAR", campo["valor"] or "")
        with TemporaryDirectory() as pasta:
            saida = Path(pasta) / "certidao.csv"
            exportar_relatorio_csv(relatorio, saida)
            with saida.open(encoding="utf-8-sig", newline="") as arq:
                linhas = list(csv.reader(arq, delimiter=";"))
        self.assertIn(["ESTADOS DOS CAMPOS"], linhas)
        for campo, estado in esperados.items():
            self.assertIn([str(ident), campo, estado], linhas)

    def test_persistencia_preserva_zeros_e_matricula_fica_fora_da_conferencia(self):
        self.salvar(_MODERNA, "certidao.pdf")
        for texto, nome in ((_RG_COMPLETO, "rg.pdf"), (_CIN_COMPLETA, "cin.pdf"), (_ANTIGA, "cnh.pdf")):
            self.salvar(texto, nome)
        resultados = conferir_analise(self.con, self.ident)
        self.assertEqual(len(resultados), 18)
        self.assertEqual({r["campo"] for r in resultados}, {"NOME", "CPF", "DATA_NASCIMENTO"})
        self.assertTrue(all(r["resultado"] == "COMPATIVEL" for r in resultados))
        campos = self.recuperar()["campos_extraidos"]
        self.assertTrue(next(r for r in campos if r["campo"] == "MATRICULA")["valor"].startswith("000001"))

    def test_certidao_sem_cpf_nao_gera_divergencia(self):
        self.salvar(_MODERNA.replace(f"CPF DO REGISTRADO: {_CPF}\n", ""), "certidao.pdf")
        self.salvar(_CIN_COMPLETA, "cin.pdf")
        resultados = {(r["campo"], r["resultado"]) for r in conferir_analise(self.con, self.ident)}
        self.assertIn(("CPF", "NAO_CONFERIDO"), resultados)
        self.assertFalse(any(estado == "DIVERGENTE" for _, estado in resultados))


if __name__ == "__main__":
    unittest.main()
