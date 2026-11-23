import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ceuma.leitura.ocr import tesseract
from ceuma.leitura.ocr.tesseract import Tesseract, localizar_tesseract


class TesteLocalizarTesseract(unittest.TestCase):
    def test_variavel_de_ambiente_tem_prioridade(self):
        with patch.dict(os.environ, {"CEUMA_TESSERACT": "/opt/ocr/tesseract"}):
            self.assertEqual(localizar_tesseract(), Path("/opt/ocr/tesseract"))

    def test_usa_o_path_quando_nao_ha_instalacao_padrao(self):
        with tempfile.TemporaryDirectory() as pasta:
            exe = Path(pasta, "tesseract")
            with patch.dict(os.environ, {"CEUMA_TESSERACT": ""}), \
                    patch.object(Path, "is_file", return_value=False), \
                    patch.object(tesseract.shutil, "which", return_value=str(exe)):
                self.assertEqual(localizar_tesseract(), exe)

    def test_sem_tesseract_devolve_caminho_padrao_inexistente(self):
        with patch.dict(os.environ, {"CEUMA_TESSERACT": ""}), \
                patch.object(Path, "is_file", return_value=False), \
                patch.object(tesseract.shutil, "which", return_value=None):
            caminho = localizar_tesseract()
        self.assertIn(caminho.name, {"tesseract", "tesseract.exe"})

    def test_executavel_explicito_ignora_busca(self):
        with patch.object(tesseract, "localizar_tesseract") as busca:
            motor = Tesseract("/tmp/meu-tesseract")
        busca.assert_not_called()
        self.assertEqual(motor.executavel, Path("/tmp/meu-tesseract").absolute())


if __name__ == "__main__":
    unittest.main()
