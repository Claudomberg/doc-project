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


# Bordas, brasões e fundos de segurança viram palavras de confiança baixa; o texto impresso
# fica acima de 50 mesmo em tinta colorida.
_CONFIANCA_MINIMA_PALAVRA = 40
# Em alturas de palavra; campos datilografados chegam a 1,3 e colunas ficam acima de 6.
_VAO_ENTRE_COLUNAS = 3


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
    # O vão entre colunas é medido antes do filtro: o buraco deixado por uma palavra
    # descartada no meio de um campo não pode parecer separação de colunas.
    trechos = (
        [p for p in trecho if p[5] * 100 >= _CONFIANCA_MINIMA_PALAVRA]
        for linha in grupos.values() for trecho in _separar_colunas(linha)
    )
    return [
        {"texto": " ".join(p[0] for p in palavras), "motor": "TESSERACT",
         "caixa": [min(p[1] for p in palavras), min(p[2] for p in palavras),
                   max(p[3] for p in palavras), max(p[4] for p in palavras)],
         "confianca": min(p[5] for p in palavras)}
        for palavras in trechos if palavras
    ]


def _separar_colunas(palavras):
    # O Tesseract junta numa linha campos de colunas diferentes na mesma altura
    # ("NATURALIDADE   DATA DE NASCIMENTO"), o que impede associar rótulo e valor pela posição.
    trechos = [[palavras[0]]]
    for anterior, atual in zip(palavras, palavras[1:]):
        altura = max(anterior[4] - anterior[2], atual[4] - atual[2])
        if atual[1] - anterior[3] > _VAO_ENTRE_COLUNAS * altura:
            trechos.append([])
        trechos[-1].append(atual)
    return trechos


def localizar_tesseract():
    if configurado := os.environ.get("CEUMA_TESSERACT"):
        return Path(configurado)
    if os.name == "nt":
        padroes = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"]
    else:
        # Apps abertos pelo Finder não herdam o PATH do Homebrew.
        padroes = [Path("/opt/homebrew/bin/tesseract"), Path("/usr/local/bin/tesseract"),
                   Path("/usr/bin/tesseract")]
    instalado = next((caminho for caminho in padroes if caminho.is_file()), None)
    no_path = shutil.which("tesseract")
    return instalado or (Path(no_path) if no_path else padroes[0])


class Tesseract:
    def __init__(self, executavel=None):
        self.executavel = Path(executavel or localizar_tesseract()).absolute()

    def _executar(self, imagem, psm, idioma, timeout, *opcoes):
        if not self.executavel.is_file():
            raise FalhaOCR("TESSERACT_INDISPONIVEL")
        altura, largura = imagem.shape[:2]
        # Em cores o Tesseract binariza mal fundos de segurança. A conversão do MuPDF clareia
        # verde e vermelho saturados bem mais que a média ponderada, destacando o texto preto.
        rgb = pymupdf.Pixmap(pymupdf.csRGB, largura, altura, imagem.tobytes(), False)
        png = pymupdf.Pixmap(pymupdf.csGRAY, rgb).tobytes("png")
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
