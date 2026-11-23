import unittest
from threading import Event, get_ident
from unittest.mock import Mock, patch

import numpy as np

from ceuma.leitura.ocr.adaptativo import LeitorAdaptativo
from ceuma.leitura.ocr.orientacao import OrientacaoPreparada


class TesteOrientacaoPreparada(unittest.TestCase):
    def setUp(self):
        self.imagem = np.zeros((120, 200, 3), dtype=np.uint8)

    def test_preparacao_e_solicitacoes_usam_a_mesma_thread(self):
        identificadores = []
        paddle = Mock()
        paddle.classificar_orientacao.side_effect = lambda _: (
            identificadores.append(get_ident()) or [(90, .97)]
        )
        fabrica = Mock(return_value=paddle)
        orientador = OrientacaoPreparada(fabrica)
        self.addCleanup(orientador.fechar)
        self.assertTrue(orientador.aguardar())
        for _ in range(2):
            self.assertEqual(orientador.classificar(self.imagem), [(90, .97)])
        fabrica.assert_called_once()
        self.assertEqual(len(identificadores), 3)
        self.assertEqual(len(set(identificadores)), 1)
        self.assertNotEqual(identificadores[0], get_ident())
        paddle.reconhecer.assert_not_called()

    def test_falha_compartilhada_preserva_revisao(self):
        paddle = Mock()
        paddle.classificar_orientacao.side_effect = RuntimeError("fixture")
        orientador = OrientacaoPreparada(lambda: paddle)
        self.addCleanup(orientador.fechar)
        leve = Mock()
        leve.reconhecer.return_value = []
        for _ in range(2):
            leitor = LeitorAdaptativo(orientador=orientador, tesseract=leve)
            self.assertIsNone(leitor.classificar_orientacao(self.imagem))
            self.assertIn("ORIENTACAO_INDISPONIVEL", leitor.pendencias)
        paddle.classificar_orientacao.assert_called_once()
        paddle.reconhecer.assert_not_called()

    def test_espera_pela_preparacao_nao_consume_orcamento(self):
        relogio = [100.0]
        orientador = Mock(pronta=False)
        def aguardar():
            if not orientador.pronta:
                relogio[0] += 14
                orientador.pronta = True
            return True
        orientador.aguardar.side_effect = aguardar
        orientador.classificar.return_value = [(90, .98)]
        leve = Mock()
        leve.reconhecer.return_value = []
        with patch("ceuma.leitura.ocr.adaptativo.monotonic", side_effect=lambda: relogio[0]):
            leitor = LeitorAdaptativo(orientador=orientador, tesseract=leve, segundos_pagina=5)
            self.assertEqual(leitor.classificar_orientacao(self.imagem), [(90, .98)])
            self.assertEqual(leitor.metricas["segundos_preparacao_orientacao"], 14)
            self.assertEqual(leitor._restante(), 5)
            relogio[0] += 5
            self.assertFalse(leitor.pode_rasterizar())
            self.assertIn("ORCAMENTO_ESGOTADO", leitor.pendencias)

    def test_fechamento_durante_preparacao_rejeita_nova_inferencia(self):
        iniciou, liberar = Event(), Event()
        paddle = Mock()
        def carregar(_):
            iniciou.set()
            if not liberar.wait(3):
                raise RuntimeError("fixture")
            return []
        paddle.classificar_orientacao.side_effect = carregar
        orientador = OrientacaoPreparada(lambda: paddle)
        try:
            self.assertTrue(iniciou.wait(2))
            orientador.fechar()
            self.assertFalse(orientador.pronta)
        finally:
            liberar.set()
        self.assertTrue(orientador.aguardar())
        with self.assertRaises(RuntimeError):
            orientador.classificar(self.imagem)
        paddle.classificar_orientacao.assert_called_once()

    def test_pdf_digital_nao_aguarda_orientador_em_preparacao(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import pymupdf
        from ceuma.leitura.pdf import ler_pdf

        orientador = Mock(pronta=False)
        motor = LeitorAdaptativo(orientador=orientador)
        with TemporaryDirectory() as pasta:
            caminho = Path(pasta, "fixture.pdf")
            with pymupdf.open() as pdf:
                pagina = pdf.new_page()
                pagina.insert_text((50, 50), "DOCUMENTO DIGITAL FICTICIO PARA TESTE")
                pdf.save(caminho)
            dados = ler_pdf(caminho, leitor_ocr=motor)
        self.assertEqual(dados["paginas"][0]["metodo"], "DIGITAL")
        orientador.aguardar.assert_not_called()
        orientador.classificar.assert_not_called()

    def test_cin_de_ocr_sem_cpf_confirmado_fica_em_revisao(self):
        from ceuma.documentos.analisador import analisar_documento
        from teste_ctps_fisica import documento
        dados = documento("CARTEIRA DE IDENTIDADE NACIONAL\nNOME: TITULAR FICTICIO")
        dados["paginas"][0]["diagnostico_ocr"] = {"pendencias": [], "metricas": {"tesseract": 1}}
        resultado = analisar_documento(dados)
        self.assertIn("CPF_CIN_NAO_CONFIRMADO", resultado["pendencias_leitura"])
