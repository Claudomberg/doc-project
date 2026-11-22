"""Mantém o classificador de orientação em uma única thread proprietária."""
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from leitor.leitor_ocr import LeitorOCR


class OrientacaoPreparada:
    def __init__(self, fabrica=LeitorOCR):
        self._fabrica = fabrica
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ceuma-orientacao")
        self._preparacao = self._executor.submit(self._preparar)

    def _preparar(self):
        self._leitor = self._fabrica()
        previa = np.full((128, 128, 3), 255, dtype=np.uint8)
        return self._leitor.classificar_orientacao(previa) is not None

    @property
    def pronta(self):
        return self._preparacao.done()

    def aguardar(self):
        return self._preparacao.result()

    def classificar(self, imagem):
        return self._executor.submit(self._leitor.classificar_orientacao, imagem).result()

    def fechar(self):
        # Uma inferência nativa em execução termina na thread dona.
        self._executor.shutdown(wait=False, cancel_futures=True)
