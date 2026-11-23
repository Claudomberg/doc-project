import unittest
from unittest.mock import Mock, patch

import numpy as np
import pymupdf

from ceuma.documentos.identificador import identificar_documento
from ceuma.leitura.ocr.adaptativo import LeitorAdaptativo
from ceuma.leitura.ocr.tesseract import Tesseract, _tsv
from teste_extracao_identidade import analisar, linha

_HEADER = b"level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def _palavra(x, texto, conf=95, linha_num=1, largura=60, altura=20):
    return f"5\t1\t1\t{linha_num}\t{x}\t10\t{largura}\t{altura}\t{conf}\t{texto}\n".encode()


class TesteTesseractEmFotos(unittest.TestCase):
    def test_envia_imagem_em_tons_de_cinza(self):
        imagem = np.zeros((20, 30, 3), dtype=np.uint8)
        imagem[:, :, 0] = 200
        with patch("ceuma.leitura.ocr.tesseract.Path.is_file", return_value=True), \
                patch("ceuma.leitura.ocr.tesseract.subprocess.run",
                      return_value=Mock(returncode=0, stdout=_HEADER)) as executar:
            Tesseract("/tesseract").reconhecer(imagem, 3, 1)
        enviada = pymupdf.Pixmap(executar.call_args.kwargs["input"])
        self.assertEqual(enviada.n, 1)

    def test_descarta_palavras_de_confianca_baixa(self):
        linhas = _tsv(_HEADER + _palavra(0, "NOME", 96) + _palavra(70, "RSRS", 12)
                      + _palavra(140, "FICTICIO", 91))
        self.assertEqual([l["texto"] for l in linhas], ["NOME FICTICIO"])

    def test_separa_colunas_unidas_na_mesma_linha(self):
        linhas = _tsv(_HEADER + _palavra(0, "NATURALIDADE") + _palavra(70, "FICTICIA")
                      + _palavra(400, "NASCIMENTO") + _palavra(470, "01/01/2000"))
        self.assertEqual([l["texto"] for l in linhas], ["NATURALIDADE FICTICIA", "NASCIMENTO 01/01/2000"])
        self.assertEqual(linhas[1]["caixa"][0], 400)

    def test_mantem_espacamento_largo_de_campo_datilografado(self):
        linhas = _tsv(_HEADER + _palavra(0, "TITULAR") + _palavra(85, "FICTICIO"))
        self.assertEqual([l["texto"] for l in linhas], ["TITULAR FICTICIO"])


class TesteLeitorAdaptativoEmFotos(unittest.TestCase):
    def test_pagina_inteira_usa_analise_de_layout(self):
        self.assertEqual(LeitorAdaptativo._psm(np.zeros((3508, 2480, 3))), 3)
        self.assertEqual(LeitorAdaptativo._psm(np.zeros((1485, 3300, 3))), 3)
        self.assertEqual(LeitorAdaptativo._psm(np.zeros((90, 900, 3))), 7)

    def test_prazo_recomeca_a_cada_documento(self):
        relogio = [100.0]
        with patch("ceuma.leitura.ocr.adaptativo.monotonic", side_effect=lambda: relogio[0]):
            leitor = LeitorAdaptativo(tesseract=Mock(), segundos_pagina=10, segundos_documento=20)
            leitor._restante()
            relogio[0] += 25
            leitor.iniciar_pagina()
            self.assertFalse(leitor.pode_rasterizar())
            leitor.iniciar_documento()
            self.assertTrue(leitor.pode_rasterizar())


class TesteIdentificacaoDeRgFotografado(unittest.TestCase):
    _RG_FOTO = (
        "VALIDA EM TODO O TERRITORIO NACIONAL\nREGISTRO\nNOME TITULAR FICTICIO\n"
        "FILIACAO\nPESSOA FICTICIA\nCARTEIRA DE IDENTIDADE gr 0 0"
    )

    def test_titulo_com_ruido_e_validade_nacional_identificam_rg(self):
        resultado = identificar_documento(self._RG_FOTO)
        self.assertEqual((resultado["tipo"], resultado["status"]), ("DOCUMENTO_IDENTIDADE", "IDENTIFICADO"))
        self.assertIn("VALIDADE EM TERRITORIO NACIONAL", resultado["evidencias"])

    def test_ruido_longo_apos_titulo_nao_vale_como_titulo(self):
        texto = self._RG_FOTO.replace("gr 0 0", "SEGUNDA PARTE")
        self.assertEqual(identificar_documento(texto)["status"], "NAO_IDENTIFICADO")

    def test_validade_sem_campos_pessoais_nao_basta(self):
        texto = "VALIDA EM TODO O TERRITORIO NACIONAL\nCARTEIRA DE IDENTIDADE"
        self.assertNotEqual(identificar_documento(texto)["status"], "IDENTIFICADO")

    def test_colunas_separadas_permitem_extrair_data_de_nascimento(self):
        campos = analisar([
            linha("CARTEIRA DE IDENTIDADE", 10, 5, 200),
            linha("VALIDA EM TODO O TERRITORIO NACIONAL", 10, 25, 300),
            linha("NOME TITULAR FICTICIO", 10, 45, 250),
            linha("NATURALIDADE", 10, 65), linha("DATA DE NASCIMENTO", 300, 65, 150),
            linha("CIDADE FICTICIA", 10, 80), linha("01/01/2000", 300, 80, 90),
        ])["campos"]["identidade"]
        self.assertEqual(campos["DATA_NASCIMENTO"]["valor"], "01/01/2000")
        self.assertEqual(campos["NATURALIDADE"]["valor"], "CIDADE FICTICIA")


if __name__ == "__main__":
    unittest.main()
