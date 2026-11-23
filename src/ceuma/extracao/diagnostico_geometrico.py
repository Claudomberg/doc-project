"""Diagnóstico local das associações espaciais de uma única página OCR."""

from math import isfinite

from ceuma.extracao.associador_espacial import _caixa
from ceuma.extracao.campos import _datas_espaciais_pagina, _rg_espacial_pagina


def diagnosticar_geometria(linhas_ocr):
    """Retorna eventos por campo com IDs locais de linha iniciados em zero.

    Recebe as linhas em memória e avalia o caminho espacial da extração.
    ASSOCIADO indica que o par passou pelo caminho espacial da página.
    """
    relatorio = {"ENTRADA": [], "DATA_EXPEDICAO": [], "RG": []}
    linhas = []
    for indice, linha in enumerate(linhas_ocr):
        try:
            caixa = _caixa(linha)
        except (IndexError, KeyError, OverflowError, TypeError, ValueError):
            caixa = None
        if caixa is None or not all(map(isfinite, caixa)):
            relatorio["ENTRADA"].append({"decisao": "CAIXA_INVALIDA", "id_linha": indice})
            caixa = None
        linhas.append({"caixa": caixa, "texto": linha.get("texto", ""),
                       "grupo_espacial": linha.get("grupo_espacial")})

    def observar_expedicao(decisao, **metadados):
        relatorio["DATA_EXPEDICAO"].append({"decisao": decisao, **metadados})

    def observar_rg(decisao, **metadados):
        relatorio["RG"].append({"decisao": decisao, **metadados})

    _datas_espaciais_pagina({"linhas_ocr": linhas}, observar_expedicao=observar_expedicao)
    _rg_espacial_pagina(linhas, observar=observar_rg)
    return relatorio
