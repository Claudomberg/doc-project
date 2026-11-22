import os
import hashlib
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pymupdf

from analise.diagnostico_geometrico import diagnosticar_geometria
from analise.extrator_campos import (
    extrair_cpfs_documento, extrair_datas_classificadas_documento,
    extrair_nome_documento, extrair_rg_documento,
)
from banco import conexao as banco
from leitor.leitor_pdf import ler_pdf
from processamento import processador
from teste_leitura_hibrida import imagem_marcada


_MARCADOR = (11, 52, 111)
_SINAIS = [
    ("CARTEIRA DE IDENTIDADE", (220, 20, 70), (24, 24, 170, 33)),
    ("RG", (20, 130, 80), (24, 58, 38, 65)),
    ("12.345.678-9", (40, 70, 220), (24, 72, 100, 81)),
    ("DATA DE EXPEDICAO", (220, 120, 20), (125, 58, 218, 65)),
    ("17/04/2035", (110, 40, 150), (125, 72, 192, 81)),
    ("CPF DO TITULAR", (25, 94, 100), (24, 110, 120, 118)),
    ("00000000191", (200, 42, 175), (24, 125, 98, 135)),
]


class OCRDeOrientacaoSimulado:
    def __init__(self, predicoes=None):
        self.predicoes = predicoes
        self.classificacoes = 0
        self.leituras = 0
        self.correcoes = []

    def _correcao(self, imagem):
        y, x = np.nonzero(np.all(imagem == _MARCADOR, axis=2))
        if not x.size:
            return 0
        canto = (x.mean() > imagem.shape[1] / 2, y.mean() > imagem.shape[0] / 2)
        return {(False, False): 0, (True, False): 90, (True, True): 180, (False, True): 270}[canto]

    def classificar_orientacao(self, imagem):
        self.classificacoes += 1
        angulo = self._correcao(imagem)
        self.correcoes.append(angulo)
        return self.predicoes if self.predicoes is not None else [(angulo, 0.98), ((angulo + 90) % 360, 0.02)]

    def reconhecer(self, imagem):
        self.leituras += 1
        linhas = []
        if not self._correcao(imagem):
            for texto, cor, _ in _SINAIS:
                y, x = np.nonzero(np.all(imagem == cor, axis=2))
                if not x.size:
                    continue
                x0, y0, x1, y1 = map(float, (x.min(), y.min(), x.max() + 1, y.max() + 1))
                linhas.append({
                    "texto": texto, "confianca": 0.97,
                    "caixa": [x0, y0, x1, y1],
                    "poligono": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                })
        return {"texto": "\n".join(linha["texto"] for linha in linhas), "linhas": linhas}


def _arte(indices=None):
    sinais = _SINAIS if indices is None else [_SINAIS[i] for i in indices]
    return imagem_marcada((240, 160), [((8, 8, 13, 13), _MARCADOR), *(
        (posicao, cor) for _, cor, posicao in sinais
    )])


class TesteOrientacaoPDF(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(self.enterContext(TemporaryDirectory()))
        self.leitor = OCRDeOrientacaoSimulado()

    def criar_pdf(self, *paginas, nome="sintetico.pdf", rodape=False):
        arquivo = self.pasta / nome
        with pymupdf.open() as pdf:
            for fisica, metadado in paginas:
                w, h = (160, 240) if fisica % 180 else (240, 160)
                p = pdf.new_page(width=w, height=h + (45 if rodape else 0))
                p.insert_image(pymupdf.Rect(0, 0, w, h), stream=_arte(), rotate=fisica)
                if rodape:
                    p.insert_text((12, h + 26), "RODAPE DIGITAL FICTICIO")
                p.set_rotation(metadado)
            pdf.save(arquivo)
        return arquivo

    def ler(self, arquivo):
        return ler_pdf(arquivo, leitor_ocr=self.leitor)

    def conferir_campos(self, resultado):
        self.assertEqual(extrair_rg_documento(resultado)["status"], "ENCONTRADO")
        cpf, = extrair_cpfs_documento(resultado)
        self.assertTrue(cpf["valido"])
        self.assertEqual(cpf["papel"], "TITULAR")
        self.assertTrue(all(d["campo"] == "DATA_EXPEDICAO" for d in
                            extrair_datas_classificadas_documento(resultado)))
        self.assertTrue(extrair_datas_classificadas_documento(resultado))

    def test_quatro_rotacoes_fisicas_com_um_ocr_por_pagina(self):
        resultado = self.ler(self.criar_pdf(*((fisica, 0) for fisica in (0, 90, 180, 270))))
        self.assertEqual(
            [p["orientacoes"][0]["angulo_aplicado"] for p in resultado["paginas"]],
            [0, 270, 180, 90],
        )
        self.assertEqual((self.leitor.classificacoes, self.leitor.leituras), (4, 4))
        for pagina in resultado["paginas"]:
            with self.subTest(angulo=pagina["orientacoes"][0]["angulo_aplicado"]):
                self.conferir_campos({"paginas": [pagina]})
                self.assertEqual(pagina["orientacoes"][0]["status"], "CONFIRMADA_MODELO")

    def test_metadado_ja_aplicado_na_renderizacao_nao_e_repetido(self):
        pagina, = self.ler(self.criar_pdf((0, 90)))["paginas"]
        orientacao, = pagina["orientacoes"]
        self.assertEqual((orientacao["rotacao_pdf"], orientacao["angulo_aplicado"]), (90, 90))
        self.conferir_campos({"paginas": [pagina]})
        self.assertEqual(self.leitor.leituras, 1)

    def test_metadado_e_rotacao_fisica_que_se_anulam(self):
        pagina, = self.ler(self.criar_pdf((90, 90)))["paginas"]
        orientacao, = pagina["orientacoes"]
        self.assertEqual((orientacao["rotacao_pdf"], orientacao["angulo_aplicado"]), (90, 0))
        self.conferir_campos({"paginas": [pagina]})
        self.assertEqual(self.leitor.leituras, 1)

    def test_tres_paginas_com_orientacoes_distintas_e_origem_preservada(self):
        resultado = self.ler(self.criar_pdf((90, 0), (0, 0), (180, 0)))
        self.assertEqual(self.leitor.correcoes, [270, 0, 180])
        self.assertEqual(self.leitor.leituras, 3)
        for n, pagina in enumerate(resultado["paginas"], 1):
            self.assertTrue(all(l["pagina_origem"] == n for l in pagina["linhas"]))

    def test_imagem_invertida_preserva_rodape_digital_e_campos(self):
        resultado = self.ler(self.criar_pdf((180, 0), rodape=True))
        pagina, = resultado["paginas"]
        self.assertTrue(pagina["texto"].endswith("RODAPE DIGITAL FICTICIO"))
        rodape = pagina["linhas"][-1]
        self.assertEqual(rodape["origem"], "DIGITAL")
        self.assertEqual(rodape["caixa"], rodape["caixa_pagina"])
        self.assertIsNone(rodape.get("grupo_espacial"))
        self.assertEqual(len(pagina["grupos_leitura"]), 2)
        self.conferir_campos(resultado)

    def test_transformacao_inversa_leva_a_caixa_ao_pdf_original(self):
        resultado = self.ler(self.criar_pdf((90, 0), (180, 90)))
        for pagina in resultado["paginas"]:
            orientacao, = pagina["orientacoes"]
            matriz = pymupdf.Matrix(*orientacao["leitura_para_pdf"])
            for linha in pagina["linhas"]:
                convertido = pymupdf.Rect(linha["caixa"]) * matriz
                np.testing.assert_allclose(list(convertido), linha["caixa_pdf"], atol=0.01)
                self.assertTrue(all(0 <= coord for coord in linha["caixa_pdf"]))

    def test_orientacao_inconclusiva_sem_alternativa_mantem_um_ocr(self):
        self.leitor.predicoes = []
        pagina, = self.ler(self.criar_pdf((180, 0)))["paginas"]
        info, = pagina["orientacoes"]
        self.assertEqual(info["status"], "INCONCLUSIVA")
        self.assertEqual(info["angulo_aplicado"], 0)
        self.assertIsNone(info["confianca"])
        self.assertEqual(self.leitor.leituras, 1)

    def test_segunda_tentativa_so_quando_alternativa_plausivel_e_primeira_fraca(self):
        self.leitor.predicoes = [(0, 0.78), (180, 0.20)]
        pagina, = self.ler(self.criar_pdf((180, 0)))["paginas"]
        self.assertEqual(pagina["orientacoes"][0]["status"], "INCONCLUSIVA")
        self.assertEqual(pagina["orientacoes"][0]["angulo_aplicado"], 180)
        self.conferir_campos({"paginas": [pagina]})
        self.assertEqual(self.leitor.leituras, 2)

    def test_hipotese_alternativa_nao_repete_ocr_com_texto_util(self):
        self.leitor.predicoes = [(0, 0.78), (180, 0.20)]
        self.ler(self.criar_pdf((0, 0)))
        self.assertEqual(self.leitor.leituras, 1)

    def test_digital_e_vazia_nao_inicializam_modelos(self):
        arquivo = self.pasta / "sem_ocr.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((30, 55), "DIGITAL FICTICIO")
            pdf.new_page()
            pdf.save(arquivo)
        with patch.dict(os.environ, {"CEUMA_OCR_MODO": "paddle"}), patch("leitor.leitor_pdf.LeitorOCR") as fabrica:
            resultado = ler_pdf(arquivo)
        fabrica.assert_not_called()
        self.assertEqual([p["orientacoes"] for p in resultado["paginas"]], [[], []])

    def test_modelo_ausente_e_distinto_de_orientacao_inconclusiva(self):
        with patch.object(self.leitor, "classificar_orientacao", return_value=None):
            pagina, = self.ler(self.criar_pdf((0, 0)))["paginas"]
        info, = pagina["orientacoes"]
        self.assertEqual(info["status"], "INDISPONIVEL")
        self.assertIsNone(info["confianca"])
        self.assertEqual(self.leitor.leituras, 1)

    def test_imagens_com_orientacoes_distintas_nao_cruzam_rotulo_e_valor(self):
        arquivo = self.pasta / "grupos.pdf"
        with pymupdf.open() as pdf:
            p = pdf.new_page(width=480, height=270)
            p.insert_image(pymupdf.Rect(10, 10, 170, 250), stream=_arte([1, 3, 5]), rotate=90)
            p.insert_image(pymupdf.Rect(280, 10, 440, 250), stream=_arte([2, 4, 6]), rotate=270)
            pdf.save(arquivo)
        resultado = self.ler(arquivo)
        pagina, = resultado["paginas"]
        self.assertEqual(len(pagina["grupos_leitura"]), 2)
        self.assertEqual(extrair_rg_documento(resultado)["status"], "NAO_ENCONTRADO")
        self.assertEqual(extrair_cpfs_documento(resultado)[0]["papel"], "AMBIGUO")
        self.assertEqual(extrair_datas_classificadas_documento(resultado)[0]["campo"], "DATA_NAO_CLASSIFICADA")
        diagnostico = diagnosticar_geometria(pagina["linhas_ocr"])
        self.assertTrue(all(
            e["decisao"] != "ASSOCIADO" for campo in ("RG", "DATA_EXPEDICAO") for e in diagnostico[campo]
        ))

    def test_deduplicacao_de_camada_digital_em_scan_rotacionado(self):
        arquivo = self.pasta / "camada.pdf"
        with pymupdf.open() as pdf:
            p = pdf.new_page(width=240, height=160)
            p.insert_image(p.rect, stream=_arte([1]), rotate=180)
            p.insert_text((216, 95), "RG", fontsize=11, rotate=180, render_mode=3)
            pdf.save(arquivo)
        pagina, = self.ler(arquivo)["paginas"]
        linha, = pagina["linhas"]
        self.assertEqual(linha["texto"], "RG")
        self.assertEqual(linha["origem"], "DIGITAL")
        self.assertIsNotNone(linha["grupo_espacial"])

    def test_extracao_textual_de_nome_preserva_fronteira_entre_grupos(self):
        pagina = {
            "numero": 1, "metodo": "HIBRIDO", "texto": "NOME\nRODAPE FICTICIO",
            "grupos_leitura": [[{"texto": "NOME"}], [{"texto": "RODAPE FICTICIO"}]],
        }
        self.assertEqual(extrair_nome_documento({"paginas": [pagina]})["status"], "NAO_ENCONTRADO")

    def test_lote_reutiliza_sessao_entre_pdfs_distintos(self):
        arquivos = [self.criar_pdf((a, 0), nome=f"sintetico_{i}.pdf") for i, a in enumerate((0, 90, 180))]
        hashes = [hashlib.sha256(a.read_bytes()).digest() for a in arquivos]
        with closing(banco.conectar_banco(":memory:")) as conexao:
            banco.criar_tabelas(conexao)
            banco.criar_analise(conexao)
            ident = conexao.execute("SELECT id FROM analises").fetchone()[0]
            with patch.dict(os.environ, {"CEUMA_OCR_MODO": "paddle"}), patch.object(processador, "LeitorOCR", return_value=self.leitor) as fabrica:
                resultado = processador.processar_lote(conexao, ident, self.pasta)
        fabrica.assert_called_once_with()
        self.assertEqual(self.leitor.classificacoes, 3)
        self.assertEqual(self.leitor.leituras, 3)
        self.assertEqual(len(resultado["documentos"]), 3)
        self.assertEqual([hashlib.sha256(a.read_bytes()).digest() for a in arquivos], hashes)

    def test_leitura_independente_preserva_bytes_do_pdf(self):
        arquivo = self.criar_pdf((270, 0))
        antes = arquivo.read_bytes()
        with patch.dict(os.environ, {"CEUMA_OCR_MODO": "paddle"}), patch("leitor.leitor_pdf.LeitorOCR", return_value=self.leitor) as fabrica:
            self.conferir_campos(ler_pdf(arquivo))
        fabrica.assert_called_once_with()
        self.assertEqual(arquivo.read_bytes(), antes)


if __name__ == "__main__":
    unittest.main()
