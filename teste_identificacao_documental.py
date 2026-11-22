import os
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf

from analise.analisador_documento import analisar_documento
from analise.conferidor_banco import conferir_analise
from analise.persistencia import salvar_resultado_analise
from banco.conexao import conectar_banco, criar_analise, criar_tabelas
from documentos.identificador import identificar_documento
from leitor import leitor_pdf
from teste_leitura_hibrida import OCRSimulado, criar_cenario


class TesteIdentificacaoDocumental(unittest.TestCase):

    def test_rotulo_registro_geral_quebrado_preserva_distincao_rg_cin(self):
        cabecalho = "POLICIA CIVIL\nNOME: PESSOA FICTICIA\n"
        self.assertEqual(identificar_documento(cabecalho + "REGISTRO\nGERAL\n")["tipo"],
                         "DOCUMENTO_IDENTIDADE")
        self.assertEqual(identificar_documento("REGISTRO\nGERAL\nNOME: PESSOA FICTICIA")["tipo"],
                         "DOCUMENTO_DESCONHECIDO")
        cin = "CARTEIRA DE IDENTIDADE NACIONAL\n" + cabecalho + "REGISTRO\nGERAL CPF"
        self.assertEqual(identificar_documento(cin)["tipo"], "CIN")

    def assert_tipo(self, texto, esperado, status="IDENTIFICADO"):
        resultado = identificar_documento(texto)
        self.assertEqual((resultado["tipo"], resultado["status"]), (esperado, status))

    def test_modelos_rg_com_titulo_ou_cabecalho_institucional_e_registro(self):
        for texto in (
            "CÉDULA DE IDENTIDADE\nRG: 12.345.678-9",
            "CARTEIRA DE IDENTIDADE CIVIL\nR.G. 123456789",
            "INSTITUTO DE IDENTIFICAÇÃO\nREGISTRO GERAL\nNOME: PESSOA FICTÍCIA",
        ):
            with self.subTest(texto=texto):
                self.assert_tipo(texto, "DOCUMENTO_IDENTIDADE")

    def test_cin_explicita_e_cin_com_rotulo_combinado(self):
        for texto in (
            "CARTEIRA DE IDENTIDADE NACIONAL\nNOME: PESSOA FICTÍCIA\nCPF DO TITULAR: 00000000191",
            "CARTEIRA DE IDENTIDADE - CIN\nCPF DO TITULAR: 00000000191",
            "CARTEIRA DE IDENTIDADE\nREGISTRO GERAL - CPF: 00000000191\nNOME: PESSOA FICTÍCIA",
            "CIN\nCPF / REGISTRO GERAL: 00000000191\nNOME: PESSOA FICTÍCIA",
        ):
            with self.subTest(texto=texto):
                self.assert_tipo(texto, "CIN")

    def test_cin_sem_titulo_com_bloco_bilingue_e_emissor(self):
        linhas = [
            "SECRETARIA DE SEGURANCA PUBLICA",
            "NOME / NAME",
            "REGISTRO GERAL - CPF / PERSONAL NUMBER",
            "DATA DE NASCIMENTO / DATE OF BIRTH",
            "VALIDADE / EXPIRY DATE",
        ]
        self.assert_tipo("\n".join(linhas), "CIN")
        for indice in range(len(linhas)):
            with self.subTest(omissao=indice):
                resultado = identificar_documento("\n".join(l for i, l in enumerate(linhas) if i != indice))
                self.assertNotEqual(resultado["tipo"], "CIN")

        texto_com_rg_distinto = "\n".join([*linhas, "REGISTRO GERAL: 12.345.678-9"])
        self.assertNotEqual(identificar_documento(texto_com_rg_distinto)["tipo"], "CIN")

    def test_cnh_antiga_e_permissao_para_dirigir(self):
        for texto in (
            "CARTEIRA NACIONAL DE HABILITAÇÃO\nCAT. HAB.: B\n1ª HABILITAÇÃO",
            "PERMISSÃO PARA DIRIGIR\nNº REGISTRO: 000000\nDETRAN",
        ):
            with self.subTest(texto=texto):
                self.assert_tipo(texto, "CNH")

    def test_cnh_atual_com_subtitulos_e_rg_como_documento_de_origem(self):
        texto = (
            "REPÚBLICA FEDERATIVA DO BRASIL\n"
            "CARTEIRA NACIONAL DE HABILITAÇÃO / DRIVER LICENSE / PERMISO DE CONDUCCIÓN\n"
            "9. CAT. HAB.: B\nDOC. IDENTIDADE / ÓRGÃO EMISSOR / UF\n"
            "RG: 12.345.678-9\nÓRGÃO EMISSOR: POLÍCIA CIVIL\nNOME: PESSOA FICTÍCIA"
        )
        self.assert_tipo(texto, "CNH")

    def test_certidao_em_formato_tabular_e_certidao_em_texto_corrido(self):
        for texto in (
            "REGISTRO CIVIL DAS PESSOAS NATURAIS\nCERTIDÃO DE NASCIMENTO\nMATRÍCULA: 000",
            "CERTIDÃO DE\nNASCIMENTO - INTEIRO TEOR\n"
            "Certifico que nasceu pessoa fictícia conforme o assento.",
            "REGISTRO CIVIL\nCertifico que nasceu pessoa fictícia. Livro 1, folha 2, termo 3.",
        ):
            with self.subTest(texto=texto):
                self.assert_tipo(texto, "CERTIDAO_NASCIMENTO")

    def test_erros_moderados_de_ocr_e_cabecalhos_em_varias_linhas(self):
        for texto, esperado in (
            ("CARTElRA DEIDENTIDADE\nREGISTRO GERAL: 123456789", "DOCUMENTO_IDENTIDADE"),
            ("CARTEIRA DE 1DENTIDADE NAC10NAL\nCPF DO TITULAR: 00000000191", "CIN"),
            ("CARTEIRA NACIONAL\nDE HABIL1TAÇÃ0\nCATEGORIA HAB B", "CNH"),
            ("CERTIDÃ0 DE NASCIMEN-\nTO\nMATRÍCULA: 000", "CERTIDAO_NASCIMENTO"),
        ):
            with self.subTest(esperado=esperado):
                self.assert_tipo(texto, esperado)

    def test_termos_compartilhados_nao_estabelecem_o_tipo(self):
        for texto in (
            "REPÚBLICA FEDERATIVA DO BRASIL\nNOME: PESSOA FICTÍCIA\nCPF: 00000000191",
            "Envie cópia da carteira nacional de habilitação para o cadastro.",
            "CARTEIRA NACIONAL DE HABILITAÇÃO / CNH OBRIGATÓRIA",
        ):
            with self.subTest(texto=texto):
                self.assert_tipo(texto, "DOCUMENTO_DESCONHECIDO", "NAO_IDENTIFICADO")

    def test_titulos_isolados_e_cabecalhos_genericos_sao_inconclusivos(self):
        for texto in (
            "CARTEIRA DE IDENTIDADE",
            "CARTEIRA DE IDENTIDADE NACIONAL",
            "CARTEIRA NACIONAL DE HABILITAÇÃO\nDETRAN",
            "CERTIDÃO DE NASCIMENTO",
            "INSTITUTO DE IDENTIFICAÇÃO\nSECRETARIA DE SEGURANÇA PÚBLICA",
        ):
            with self.subTest(texto=texto):
                self.assert_tipo(texto, "DOCUMENTO_DESCONHECIDO", "EVIDENCIA_INSUFICIENTE")

    def test_mais_evidencias_de_um_tipo_nao_anulam_outro_tipo_confirmado(self):
        texto = (
            "CARTEIRA NACIONAL DE HABILITAÇÃO\nCAT. HAB. B\nPRIMEIRA HABILITAÇÃO\n"
            "DETRAN\nNÚMERO DE REGISTRO: 000\nCERTIDÃO DE NASCIMENTO\nMATRÍCULA: 000"
        )
        self.assert_tipo(texto, "DOCUMENTO_DESCONHECIDO", "AMBIGUO")

    def test_rg_e_cin_com_cabecalhos_e_registros_distintos_geram_ambiguidade(self):
        texto = (
            "CARTEIRA DE IDENTIDADE\nREGISTRO GERAL: 12.345.678-9\n"
            "CARTEIRA DE IDENTIDADE NACIONAL\nREGISTRO GERAL - CPF: 00000000191"
        )
        self.assert_tipo(texto, "DOCUMENTO_DESCONHECIDO", "AMBIGUO")

    def test_casamento_e_vinculo_legados_continuam_reconhecidos(self):
        self.assert_tipo("CERTIDÃO DE CASAMENTO", "CERTIDAO_CASAMENTO")
        self.assert_tipo("CONTRATO DE TRABALHO", "VINCULO_TRABALHISTA")
        self.assert_tipo("CTPS", "DOCUMENTO_DESCONHECIDO", "EVIDENCIA_INSUFICIENTE")

    def test_certidao_de_casamento_com_dados_de_nascimento_nao_vira_certidao_de_nascimento(self):
        texto = (
            "CERTIDÃO DE CASAMENTO\nREGISTRO CIVIL\nMATRÍCULA: 000\n"
            "Uma pessoa fictícia, nascida em data fictícia, consta no assento."
        )
        self.assert_tipo(texto, "CERTIDAO_CASAMENTO")

    def test_documento_desconhecido_e_texto_vazio(self):
        for texto in ("", "RECIBO DE PROTOCOLO FICTÍCIO"):
            self.assert_tipo(texto, "DOCUMENTO_DESCONHECIDO", "NAO_IDENTIFICADO")

    def test_leitor_hibrido_encaminha_imagem_e_rodape_ao_identificador(self):
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "ficticio.pdf"
            with pymupdf.open() as pdf:
                criar_cenario(pdf, "rodape")
                pdf.save(arquivo)
            ocr = OCRSimulado()
            with patch.dict(os.environ, {"CEUMA_OCR_MODO": "paddle"}), patch.object(leitor_pdf, "LeitorOCR", return_value=ocr):
                documento = leitor_pdf.ler_pdf(arquivo)
        self.assertEqual(documento["paginas"][0]["metodo"], "HIBRIDO")
        self.assertEqual(len(ocr.chamadas), 1)
        resultado = analisar_documento(documento)
        self.assertEqual(resultado["identificacao"]["tipo"], "DOCUMENTO_IDENTIDADE")
        self.assertEqual(resultado["campos"]["rg"]["status"], "ENCONTRADO")

    def test_cin_persistida_participa_da_conferencia_por_cpf_do_titular(self):
        textos = (
            "CARTEIRA DE IDENTIDADE NACIONAL\nCPF DO TITULAR: 00000000191",
            "CARTEIRA DE IDENTIDADE\nRG: 12345678\nCPF DO TITULAR: 00000000191",
        )
        with closing(conectar_banco(":memory:")) as conexao:
            criar_tabelas(conexao)
            criar_analise(conexao)
            ident = conexao.execute("SELECT id FROM analises").fetchone()[0]
            for numero, texto in enumerate(textos):
                nome = f"sintetico_{numero}.pdf"
                resultado = analisar_documento({
                    "nome_arquivo": nome, "caminho_origem": nome, "quantidade_paginas": 1,
                    "paginas": [{"numero": 1, "metodo": "DIGITAL", "texto": texto}],
                })
                salvar_resultado_analise(conexao, ident, resultado)
            tipos = conexao.execute("SELECT tipo_identificado FROM documentos ORDER BY id").fetchall()
            self.assertEqual([linha[0] for linha in tipos], ["CIN", "DOCUMENTO_IDENTIDADE"])
            r = conferir_analise(conexao, ident)
        self.assertEqual([(c["campo"], c["resultado"]) for c in r], [("CPF", "COMPATIVEL")])


if __name__ == "__main__":
    unittest.main()
