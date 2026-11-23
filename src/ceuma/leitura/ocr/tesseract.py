"""Adaptador do executável Tesseract: chamada em subprocesso e leitura do TSV."""
import csv
import io
import os
import re
import shutil
import subprocess
from pathlib import Path

import pymupdf


class FalhaOCR(RuntimeError):
    """Falha do adaptador sem mensagens brutas ou conteúdo documental."""


def _tsv(saida):
    try:
        leitor = csv.DictReader(io.StringIO(saida.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE)
        colunas = {"level", "block_num", "par_num", "line_num", "left",
                   "top", "width", "height", "conf", "text"}
        if not colunas.issubset(leitor.fieldnames or []):
            raise ValueError
        grupos = {}
        for linha in leitor:
            if None in linha or any(linha[k] is None for k in colunas):
                raise ValueError
            if linha["level"] != "5" or not linha["text"].strip():
                continue
            x, y, largura, altura = (int(linha[k]) for k in ("left", "top", "width", "height"))
            confianca = float(linha["conf"])
            if min(x, y) < 0 or min(largura, altura) <= 0 or not 0 <= confianca <= 100:
                raise ValueError
            chave = tuple(linha[k] for k in ("block_num", "par_num", "line_num"))
            grupos.setdefault(chave, []).append(
                (linha["text"].strip(), x, y, x + largura, y + altura, confianca / 100)
            )
    except (ValueError, UnicodeError, csv.Error):
        raise FalhaOCR("TSV_INVALIDO") from None
    return [
        {"texto": " ".join(p[0] for p in palavras), "motor": "TESSERACT",
         "caixa": [min(p[1] for p in palavras), min(p[2] for p in palavras),
                   max(p[3] for p in palavras), max(p[4] for p in palavras)],
         "confianca": min(p[5] for p in palavras)}
        for palavras in grupos.values()
    ]


class Tesseract:
    def __init__(self, executavel=None):
        padrao = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"
        caminho = executavel or (padrao if padrao.is_file() else shutil.which("tesseract.exe")) or padrao
        self.executavel = Path(caminho).absolute()

    def _executar(self, imagem, psm, idioma, timeout, *opcoes):
        if not self.executavel.is_file():
            raise FalhaOCR("TESSERACT_INDISPONIVEL")
        altura, largura = imagem.shape[:2]
        png = pymupdf.Pixmap(pymupdf.csRGB, largura, altura, imagem.tobytes(), False).tobytes("png")
        ambiente = {**os.environ, "OMP_THREAD_LIMIT": "2"}
        try:
            retorno = subprocess.run(
                [str(self.executavel), "stdin", "stdout", "-l", idioma, "--psm", str(psm), *opcoes],
                input=png, capture_output=True, timeout=timeout, check=False, env=ambiente,
                shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise FalhaOCR("TIMEOUT_TESSERACT") from None
        except OSError:
            raise FalhaOCR("FALHA_TESSERACT") from None
        if retorno.returncode:
            if b"por.traineddata" in retorno.stderr or b"Failed loading language 'por'" in retorno.stderr:
                raise FalhaOCR("IDIOMA_PORTUGUES_INDISPONIVEL")
            raise FalhaOCR("FALHA_TESSERACT")
        return retorno.stdout

    def reconhecer(self, imagem, psm, timeout):
        return _tsv(self._executar(imagem, psm, "por", timeout, "tsv"))

    def orientar(self, imagem, timeout):
        saida = self._executar(imagem, 0, "osd", timeout).decode("ascii", errors="replace")
        giro = re.search(r"(?m)^Rotate: (0|90|180|270)\s*$", saida)
        escore = re.search(r"Orientation confidence: ([0-9.]+)", saida)
        if not giro or not escore or float(escore[1]) < 12:
            return []
        return [((-int(giro[1])) % 360, 0.8)]
