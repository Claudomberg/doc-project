import hashlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

from analise.analisador_documento import analisar_documento
from analise.diagnostico_geometrico import diagnosticar_geometria
from analise.extrator_campos import (
    extrair_cpfs_documento, extrair_datas_classificadas_documento, extrair_rg_documento,
)
from leitor import leitor_ocr, leitor_pdf, pagina_pdf


CORES = {
    (220, 20, 70): "12.345.678-9",
    (20, 130, 80): "17/04/2035",
    (40, 70, 220): "DOCUMENTO FICTICIO",
    (220, 120, 20): "RG: 12.345.678-9",
    (110, 40, 150): "CARTEIRA DE IDENTIDADE",
}


def imagem_marcada(tamanho, marcas=(), *, fundo="white"):
    imagem = Image.new("RGB", tamanho, fundo)
    desenho = ImageDraw.Draw(imagem)
    for coordenadas, cor in marcas:
        desenho.rectangle(coordenadas, fill=cor)
    with io.BytesIO() as arquivo:
        imagem.save(arquivo, format="PNG")
        return arquivo.getvalue()


class OCRSimulado:
    """Encontra marcas de cor no raster recebido, sem pressupor o recorte."""

    def __init__(self):
        self.chamadas = []

    def reconhecer(self, imagem):
        self.chamadas.append(imagem.shape[:2])
        linhas = []
        for cor, texto in CORES.items():
            y, x = np.nonzero(np.all(imagem == cor, axis=2))
            if x.size:
                x0, y0, x1, y1 = map(float, (x.min(), y.min(), x.max() + 1, y.max() + 1))
                linhas.append({
                    "texto": texto, "confianca": 0.95,
                    "caixa": [x0, y0, x1, y1],
                    "poligono": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                })
        return {"texto": "\n".join(linha["texto"] for linha in linhas), "linhas": linhas}


def criar_cenario(documento, nome):
    pagina = documento.new_page(width=300, height=220)
    if nome in {"digital", "decorativa"}:
        pagina.insert_text((20, 35), "NOME: TITULAR FICTICIO")
        pagina.insert_text((20, 55), "CPF DO TITULAR: 00000000191")
    if nome == "decorativa":
        pagina.insert_image(
            pymupdf.Rect(190, 80, 280, 140),
            stream=imagem_marcada((90, 60), fundo=(17, 50, 110)),
        )
    if nome in {"digitalizada", "camada"}:
        pagina.insert_image(
            pagina.rect, stream=imagem_marcada((300, 220), [((21, 25, 145, 37), (40, 70, 220))]),
        )
        if nome == "camada":
            pagina.insert_text((20, 37), "DOCUMENTO FICTICIO", render_mode=3)
    if nome == "camada_fragmentada":
        pagina.insert_image(
            pagina.rect, stream=imagem_marcada((300, 220), [((41, 25, 115, 37), (220, 20, 70))]),
        )
        pagina.insert_text((20, 37), "RG: 12.345.678-9", render_mode=3)
    if nome == "camada_parcial":
        pagina.insert_image(
            pagina.rect,
            stream=imagem_marcada((300, 220), [((21, 25, 145, 37), (40, 70, 220)),
                                               ((21, 70, 120, 82), (220, 120, 20))]),
        )
        pagina.insert_text((20, 37), "DOCUMENTO FICTICIO", render_mode=3)
    if nome in {"rodape", "falha"}:
        pagina.insert_image(
            pymupdf.Rect(10, 10, 290, 175),
            stream=imagem_marcada((280, 165), [((20, 22, 115, 34), (110, 40, 150)),
                                                     ((20, 55, 95, 68), (220, 120, 20))]),
        )
        pagina.insert_text((25, 200), "RODAPE DIGITAL FICTICIO")
    if nome in {"complementar", "cropbox"}:
        pagina.insert_text((25, 65), "RG", fontsize=12)
        pagina.insert_text((25, 102), "DATA DE EXPEDICAO", fontsize=12)
        pagina.insert_image(
            pymupdf.Rect(50, 49, 112, 69),
            stream=imagem_marcada((62, 20), [((1, 5, 57, 16), (220, 20, 70))]),
        )
        pagina.insert_image(
            pymupdf.Rect(153, 87, 213, 107),
            stream=imagem_marcada((60, 20), [((0, 4, 55, 15), (20, 130, 80))]),
        )
        if nome == "cropbox":
            pagina.set_cropbox(pymupdf.Rect(10, 15, 290, 210))
    if nome == "digital_fragmentada":
        pagina.insert_text((25, 45), "NOME:")
        pagina.insert_text((90, 45), "TITULAR FICTICIO")
    if nome == "repetidos":
        pagina.insert_text((20, 37), "DOCUMENTO FICTICIO")
        pagina.insert_image(
            pymupdf.Rect(20, 100, 160, 120),
            stream=imagem_marcada((140, 20), [((1, 2, 123, 16), (40, 70, 220))]),
        )
    if nome == "rotacionada":
        pagina.insert_image(
            pymupdf.Rect(25, 45, 100, 120),
            stream=imagem_marcada((75, 75), [((15, 35, 60, 45), (220, 120, 20))]),
            rotate=90,
        )
        pagina.insert_text((25, 145), "DIGITAL FICTICIO", rotate=90)
        pagina.set_rotation(90)
    return pagina


class TesteLeituraHibrida(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(self.enterContext(TemporaryDirectory()))
        self.ocr = OCRSimulado()
        self.enterContext(patch.dict(os.environ, {"CEUMA_OCR_MODO": "paddle"}))
        self.fabrica = self.enterContext(patch.object(leitor_pdf, "LeitorOCR", return_value=self.ocr))

    def criar_pdf(self, *cenarios):
        arquivo = self.pasta / "sintetico.pdf"
        with pymupdf.open() as documento:
            for nome in cenarios:
                criar_cenario(documento, nome)
            documento.save(arquivo)
        return arquivo

    def ler(self, *cenarios):
        return leitor_pdf.ler_pdf(self.criar_pdf(*cenarios))

    def test_pagina_digital_sem_ocr_preserva_geometria_e_cpf(self):
        documento = self.ler("digital")
        pagina, = documento["paginas"]
        self.assertEqual(pagina["metodo"], "DIGITAL")
        self.assertEqual(pagina["linhas_ocr"], [])
        self.assertTrue(all(linha["caixa"] and linha["confianca"] is None for linha in pagina["linhas"]))
        self.assertEqual(extrair_cpfs_documento(documento)[0]["papel"], "TITULAR")
        self.fabrica.assert_not_called()

    def test_pagina_digitalizada_usa_pagina_inteira(self):
        pagina, = self.ler("digitalizada")["paginas"]
        linha, = pagina["linhas"]
        self.assertEqual((pagina["metodo"], pagina["composicao"]), ("OCR", "DIGITALIZADA"))
        self.assertEqual(self.ocr.chamadas, [(440, 600)])
        self.assertEqual((linha["origem"], linha["confianca"], linha["pagina_origem"]), ("OCR", 0.95, 1))

    def test_texto_digital_preserva_a_extracao_ordenada_do_leitor_anterior(self):
        arquivo = self.criar_pdf("digital_fragmentada")
        with pymupdf.open(arquivo) as pdf:
            esperado = pdf[0].get_text("text", sort=True)
        pagina, = leitor_pdf.ler_pdf(arquivo)["paginas"]
        self.assertEqual(pagina["texto"], esperado)
        self.assertTrue(pagina["linhas"])
        self.fabrica.assert_not_called()

    def test_rodape_digital_nao_impede_ocr_da_imagem_principal(self):
        documento = self.ler("rodape")
        pagina, = documento["paginas"]
        self.assertEqual(pagina["metodo"], "HIBRIDO")
        self.assertLess(self.ocr.chamadas[0][0], 440)
        self.assertEqual(len(self.ocr.chamadas), 1)
        self.assertTrue(pagina["texto"].startswith("CARTEIRA"))
        self.assertTrue(pagina["texto"].endswith("RODAPE DIGITAL FICTICIO"))
        self.assertEqual(extrair_rg_documento(documento)["status"], "ENCONTRADO")
        self.assertEqual(analisar_documento(documento)["identificacao"]["tipo"], "DOCUMENTO_IDENTIDADE")

    def test_recortes_pequenos_reconstituem_rg_e_expedicao_no_sistema_comum(self):
        documento = self.ler("complementar")
        pagina, = documento["paginas"]
        self.assertEqual(pagina["metodo"], "HIBRIDO")
        self.assertEqual(len(self.ocr.chamadas), 2)
        rg = next(linha for linha in pagina["linhas"] if linha["origem"] == "OCR")
        np.testing.assert_allclose(rg["caixa"][:2], [102, 108], atol=2)
        np.testing.assert_allclose(rg["poligono"][0], rg["caixa"][:2])
        self.assertEqual(extrair_rg_documento(documento)["status"], "ENCONTRADO")
        self.assertEqual(extrair_datas_classificadas_documento(documento)[0]["campo"], "DATA_EXPEDICAO")
        diagnostico = diagnosticar_geometria(pagina["linhas_ocr"])
        self.assertTrue(any(item["decisao"] == "ASSOCIADO" for item in diagnostico["DATA_EXPEDICAO"]))

    def test_cropbox_define_origem_comum_da_pagina_visivel(self):
        documento = self.ler("cropbox")
        pagina, = documento["paginas"]
        rg = next(linha for linha in pagina["linhas"] if linha["origem"] == "OCR")
        np.testing.assert_allclose(rg["caixa"][:2], [82, 78], atol=2)
        self.assertEqual(extrair_rg_documento(documento)["status"], "ENCONTRADO")

    def test_imagem_decorativa_uniforme_nao_inicializa_ocr(self):
        pagina, = self.ler("decorativa")["paginas"]
        self.assertEqual(pagina["metodo"], "DIGITAL")
        self.fabrica.assert_not_called()

    def test_camada_digital_sobre_imagem_deduplica_texto_na_mesma_posicao(self):
        pagina, = self.ler("camada")["paginas"]
        linha, = pagina["linhas"]
        self.assertEqual(pagina["composicao"], "DIGITALIZADA_COM_CAMADA")
        self.assertEqual(linha["origem"], "DIGITAL")
        self.assertIsNone(linha["confianca"])
        self.assertEqual(len(self.ocr.chamadas), 1)

    def test_textos_iguais_em_posicoes_distintas_sao_preservados(self):
        pagina, = self.ler("repetidos")["paginas"]
        self.assertEqual(pagina["texto"].count("DOCUMENTO FICTICIO"), 2)
        self.assertEqual([linha["origem"] for linha in pagina["linhas"]], ["DIGITAL", "OCR"])

    def test_camada_com_linha_completa_deduplica_fragmento_do_ocr(self):
        pagina, = self.ler("camada_fragmentada")["paginas"]
        linha, = pagina["linhas"]
        self.assertEqual(linha["texto"], "RG: 12.345.678-9")
        self.assertEqual(linha["origem"], "DIGITAL")

    def test_camada_parcial_preserva_o_campo_ausente_na_camada_digital(self):
        documento = self.ler("camada_parcial")
        pagina, = documento["paginas"]
        self.assertEqual(pagina["composicao"], "DIGITALIZADA_COM_CAMADA")
        self.assertEqual(pagina["texto"].count("DOCUMENTO FICTICIO"), 1)
        self.assertEqual(extrair_rg_documento(documento)["status"], "ENCONTRADO")

    def test_pagina_rotacionada_normaliza_raster_e_digital(self):
        pagina, = self.ler("rotacionada")["paginas"]
        self.assertEqual(pagina["metodo"], "HIBRIDO")
        linha = next(linha for linha in pagina["linhas"] if linha["origem"] == "OCR")
        # A imagem tem a mesma orientação na tela após as duas rotações.
        np.testing.assert_allclose(linha["caixa"][:2], [230, 120], atol=2)
        for item in pagina["linhas"]:
            x0, y0, x1, y1 = item["caixa"]
            self.assertTrue(0 <= x0 < x1 <= 440 and 0 <= y0 < y1 <= 600)

    def test_tentativa_com_raster_desrotacionado_reprojeta_a_caixa(self):
        reconhecer = self.ocr.reconhecer

        def simular(imagem):
            resultado = reconhecer(imagem)
            return resultado if len(self.ocr.chamadas) > 1 else {"linhas": []}

        self.ocr.reconhecer = simular
        pagina, = self.ler("rotacionada")["paginas"]
        linha = next(linha for linha in pagina["linhas"] if linha["origem"] == "OCR")
        np.testing.assert_allclose(linha["caixa"][:2], [230, 120], atol=2)
        self.assertEqual(len(self.ocr.chamadas), 2)

    def test_escala_de_renderizacao_reduzida_mantem_geometria_canonica(self):
        with patch.object(pagina_pdf, "_MAX_PIXELS", 70_000):
            pagina, = self.ler("digitalizada")["paginas"]
        altura, largura = self.ocr.chamadas[0]
        self.assertLess(altura * largura, 71_000)
        np.testing.assert_allclose(pagina["linhas"][0]["caixa"][:2], [42, 50], atol=4)

    def test_multiplas_paginas_reusam_instancia_e_preservam_origem(self):
        documento = self.ler("digital", "digitalizada", "rodape", "vazia")
        paginas = documento["paginas"]
        self.assertEqual([p["metodo"] for p in paginas], ["DIGITAL", "OCR", "HIBRIDO", "SEM_TEXTO"])
        self.assertEqual([p["numero"] for p in paginas], [1, 2, 3, 4])
        for pagina in paginas:
            self.assertTrue(all(linha["pagina_origem"] == pagina["numero"] for linha in pagina["linhas"]))
        self.fabrica.assert_called_once_with()
        self.assertEqual(len(self.ocr.chamadas), 2)

    def test_pagina_vazia_nao_chama_ocr(self):
        pagina, = self.ler("vazia")["paginas"]
        self.assertEqual((pagina["metodo"], pagina["texto"], pagina["linhas"]), ("SEM_TEXTO", "", []))
        self.fabrica.assert_not_called()

    def test_falha_de_ocr_nao_devolve_leitura_parcial(self):
        arquivo = self.criar_pdf("digital", "falha")
        antes = arquivo.read_bytes()
        with patch.object(self.ocr, "reconhecer", side_effect=RuntimeError("falha ficticia")):
            with self.assertRaisesRegex(RuntimeError, "falha ficticia"):
                leitor_pdf.ler_pdf(arquivo)
        self.assertEqual(arquivo.read_bytes(), antes)

    def test_documento_original_preservado_e_ocr_opcional(self):
        arquivo = self.criar_pdf("rodape", "digitalizada")
        antes = hashlib.sha256(arquivo.read_bytes()).digest()
        leitor_pdf.ler_pdf(arquivo)
        self.fabrica.reset_mock()
        leitura = leitor_pdf.ler_pdf(arquivo, usar_ocr=False)
        self.fabrica.assert_not_called()
        self.assertEqual([p["metodo"] for p in leitura["paginas"]], ["DIGITAL", "SEM_TEXTO"])
        self.assertEqual(hashlib.sha256(arquivo.read_bytes()).digest(), antes)

    def test_recorte_sem_resultado_util_recorre_a_pagina_inteira(self):
        reconhecer = self.ocr.reconhecer

        def simular(imagem):
            resultado = reconhecer(imagem)
            return resultado if imagem.shape[:2] == (440, 600) else {"linhas": []}

        self.ocr.reconhecer = simular
        pagina, = self.ler("rodape")["paginas"]
        self.assertEqual(pagina["metodo"], "HIBRIDO")
        self.assertEqual(len(self.ocr.chamadas), 2)
        self.assertEqual(self.ocr.chamadas[-1], (440, 600))

    def test_cache_ausente_produz_erro_local_controlado(self):
        with patch.dict(os.environ, {"PADDLE_PDX_CACHE_HOME": str(self.pasta / "cache-ausente")}):
            with self.assertRaisesRegex(FileNotFoundError, "modelo OCR local"):
                leitor_ocr.LeitorOCR().inicializar()


if __name__ == "__main__":
    unittest.main()
