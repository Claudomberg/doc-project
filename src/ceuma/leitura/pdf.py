
from pathlib import Path

import pymupdf

from ceuma.leitura.ocr.adaptativo import LeitorAdaptativo, criar_leitor_ocr, modo_ocr
from ceuma.leitura.ocr.paddle import LeitorOCR
from ceuma.leitura.pagina import ler_pagina


def ler_pdf(caminho_arquivo, usar_ocr=True, *, leitor_ocr=None):
    caminho = Path(caminho_arquivo)

    if not caminho.is_file():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {caminho}"
        )

    if caminho.suffix.lower() != ".pdf":
        raise ValueError("O arquivo precisa ser PDF.")

    paginas = []

    def obter_leitor():
        nonlocal leitor_ocr
        if leitor_ocr is None:
            leitor_ocr = LeitorOCR()
        return leitor_ocr

    with pymupdf.open(str(caminho)) as documento:
        if documento.needs_pass:
            raise ValueError("PDF protegido por senha.")

        for numero, pagina in enumerate(documento, start=1):
            if (usar_ocr and leitor_ocr is None
                    and modo_ocr() != "paddle"):
                leitor_ocr = criar_leitor_ocr()
            adaptativo = usar_ocr and isinstance(leitor_ocr, LeitorAdaptativo)
            if adaptativo:
                leitor_ocr.iniciar_pagina()
            if adaptativo:
                leitura = ler_pagina(pagina, obter_leitor, preferir_digital=True)
            else:
                leitura = ler_pagina(pagina, obter_leitor if usar_ocr else None)
            if adaptativo:
                leitura["diagnostico_ocr"] = {
                    "pendencias": sorted(leitor_ocr.pendencias),
                    "metricas": dict(leitor_ocr.metricas),
                }
            for linha in leitura["linhas"]:
                linha["pagina_origem"] = numero
            paginas.append({"numero": numero, **leitura})

    return {
        "nome_arquivo": caminho.name,
        "caminho_origem": str(caminho.resolve()),
        "quantidade_paginas": len(paginas),
        "paginas": paginas
    }
