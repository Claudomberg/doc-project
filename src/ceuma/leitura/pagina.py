"""Composição e leitura espacial de uma página PDF."""

import math
import re
import unicodedata

import numpy as np
import pymupdf


# Mantém as distâncias do associador na escala histórica de 2 pixels por ponto.
_ESCALA_COORDENADAS = 2
# O OCR recebe ~300 DPI; _converter_linhas devolve as caixas para a escala de coordenadas.
_ESCALA_OCR = 300 / 72
_MAX_PIXELS = 5_000_000
_MAX_LADO = 3600
_MARGEM_RECORTE = 4


def _caixa_canonica(retangulo):
    return [valor * _ESCALA_COORDENADAS for valor in retangulo]


def _linhas_digitais(pagina, texto_pagina):
    blocos = texto_pagina.extractDICT()["blocks"]
    linhas = []
    for bloco in blocos:
        for linha in bloco.get("lines", ()):
            texto = "".join(span["text"] for span in linha["spans"]).strip()
            if texto:
                original = pymupdf.Rect(linha["bbox"])
                caixa = original * pagina.rotation_matrix
                linhas.append({
                    "texto": texto, "caixa": _caixa_canonica(caixa),
                    "caixa_pagina": _caixa_canonica(caixa), "caixa_pdf": list(original),
                    "confianca": None, "poligono": None, "origem": "DIGITAL",
                })
    return linhas


def _qualidade_textual(texto):
    visiveis = [c for c in texto if not c.isspace()]
    defeitos = sum(
        c == "\ufffd" or unicodedata.category(c) in {"Cc", "Co", "Cs"}
        for c in visiveis
    )
    return any(c.isalnum() for c in visiveis) and defeitos <= len(visiveis) * 0.15


def _ocr_util(linha):
    confianca = linha["confianca"]
    return (confianca is None or confianca >= 0.50) and _qualidade_textual(linha["texto"])


def _regioes_de_imagem(pagina):
    regioes = []
    for info in pagina.get_image_info():
        caixa = (pymupdf.Rect(info["bbox"]) * pagina.rotation_matrix) & pagina.rect
        if caixa.is_empty or (info["width"] == info["height"] == 1):
            continue
        if not any(outra.contains(caixa) for outra in regioes):
            regioes = [outra for outra in regioes if not caixa.contains(outra)]
            regioes.append(caixa)
    return regioes


def _imagem_variada(imagem, area, pix, escala):
    # Examina a área original da imagem, sem a margem que inclui o papel.
    x0, y0, x1, y1 = (area * escala).irect
    dados = imagem[max(0, y0 - pix.y): y1 - pix.y, max(0, x0 - pix.x): x1 - pix.x]
    return dados.size > 0 and any(
        canal.min() != canal.max() for canal in (dados[:, :, i] for i in range(3))
    )


def _matriz_rotacionada_para_pagina(pix, escala, angulo):
    s = 1 / _ESCALA_COORDENADAS
    x, y = pix.x / escala, pix.y / escala
    largura, altura = pix.width / escala, pix.height / escala
    return pymupdf.Matrix(*{
        0: (s, 0, 0, s, x, y),
        90: (0, s, -s, 0, x + largura, y),
        180: (-s, 0, 0, -s, x + largura, y + altura),
        270: (0, -s, s, 0, x, y + altura),
    }[angulo])


def _converter_linhas(resultado, pagina, pix, escala, angulo, grupo):
    para_pagina = _matriz_rotacionada_para_pagina(pix, escala, angulo)

    def retangulo(pontos):
        xs, ys = zip(*pontos)
        return [min(xs), min(ys), max(xs), max(ys)]

    linhas = []
    for linha in resultado["linhas"]:
        texto = linha["texto"].strip()
        if not texto:
            continue
        poligono = linha.get("poligono")
        caixa = linha.get("caixa")
        pontos = poligono
        if not pontos and caixa:
            x0, y0, x1, y1 = caixa
            pontos = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        caixa_convertida = poligono_convertido = caixa_pagina = caixa_pdf = poligono_pdf = None
        if pontos and all(math.isfinite(v) for ponto in pontos for v in ponto):
            no_raster = [_caixa_canonica((x / escala, y / escala)) for x, y in pontos]
            na_pagina = [pymupdf.Point(ponto) * para_pagina for ponto in no_raster]
            caixa_pagina = _caixa_canonica(retangulo(na_pagina))
            if caixa_pagina[0] < caixa_pagina[2] and caixa_pagina[1] < caixa_pagina[3]:
                no_pdf = [list(ponto * pagina.derotation_matrix) for ponto in na_pagina]
                na_leitura = (
                    no_raster
                    if grupo is not None and angulo else [_caixa_canonica(p) for p in na_pagina]
                )
                caixa_convertida = retangulo(na_leitura)
                caixa_pdf = retangulo(no_pdf)
                poligono_convertido = na_leitura if poligono else None
                poligono_pdf = no_pdf if poligono else None
        linhas.append({
            "texto": texto, "caixa": caixa_convertida, "poligono": poligono_convertido,
            "caixa_pagina": caixa_pagina, "caixa_pdf": caixa_pdf, "poligono_pdf": poligono_pdf,
            "grupo_espacial": grupo,
            "confianca": linha.get("confianca"), "origem": "OCR",
            "motor": linha.get("motor", "PADDLE"),
        })
    return linhas


def _ler_imagem(pagina, area, obter_leitor, orientacoes, *, recorte):
    clip = area
    if recorte:
        margem = (-_MARGEM_RECORTE, -_MARGEM_RECORTE, _MARGEM_RECORTE, _MARGEM_RECORTE)
        clip = (area + margem) & pagina.rect
    escala = min(
        _ESCALA_OCR, math.sqrt(_MAX_PIXELS / clip.get_area()),
        _MAX_LADO / max(clip.width, clip.height),
    )
    pix = pagina.get_pixmap(
        matrix=pymupdf.Matrix(escala, escala), clip=clip, colorspace=pymupdf.csRGB,
        alpha=False,
    )
    imagem = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    if not _imagem_variada(imagem, area, pix, escala):
        return []
    leitor = obter_leitor()
    classificar = getattr(leitor, "classificar_orientacao", None)
    predicoes = classificar(imagem) if classificar is not None else None
    if predicoes is not None:
        predicoes = sorted(
            (
                (angulo, escore) for angulo, escore in predicoes
                if angulo in (0, 90, 180, 270) and math.isfinite(escore) and 0 <= escore <= 1
            ),
            key=lambda item: item[1], reverse=True,
        )
    primeiro, confianca = predicoes[0] if predicoes else (None, None)
    segundo, escore_alternativo = predicoes[1] if predicoes and len(predicoes) > 1 else (None, 0)
    conclusivo = (
        confianca is not None and confianca >= 0.70
        and confianca - escore_alternativo >= 0.15
    )
    aplicado = primeiro if conclusivo else 0
    grupo = len(orientacoes) + 1

    def reconhecer(angulo, isolar):
        raster = np.ascontiguousarray(np.rot90(imagem, angulo // 90)) if angulo else imagem
        return _converter_linhas(
            leitor.reconhecer(raster), pagina, pix, escala, angulo, grupo if isolar else None,
        )

    linhas = reconhecer(aplicado, aplicado != 0 or (predicoes is not None and not conclusivo))
    evidencia_alternativa = (
        segundo if conclusivo and escore_alternativo >= 0.15
        else primeiro if not conclusivo and confianca is not None and confianca >= 0.40
        else None
    )
    # O raster já inclui /Rotate. Em leitores sem classificador, o ângulo do
    # PDF só serve de evidência para uma tentativa após OCR sem texto útil.
    if predicoes is None and pagina.rotation:
        evidencia_alternativa = pagina.rotation
    if evidencia_alternativa not in (None, aplicado) and not any(map(_ocr_util, linhas)):
        tentativa = reconhecer(evidencia_alternativa, classificar is not None)
        if any(map(_ocr_util, tentativa)):
            linhas = tentativa
            aplicado = evidencia_alternativa
            conclusivo = False
    tem_texto_util = any(map(_ocr_util, linhas))
    grupo_final = next((linha["grupo_espacial"] for linha in linhas), None)
    s = 1 / _ESCALA_COORDENADAS
    para_pagina = (
        _matriz_rotacionada_para_pagina(pix, escala, aplicado)
        if grupo_final is not None and aplicado else pymupdf.Matrix(s, s)
    )

    status = (
        "CONFIRMADA_MODELO" if conclusivo and tem_texto_util else
        "INDISPONIVEL" if predicoes is None and not aplicado else "INCONCLUSIVA"
    )
    orientacoes.append({
        "regiao": list(area), "rotacao_pdf": pagina.rotation, "status": status,
        "angulo_estimado": primeiro, "angulo_aplicado": aplicado, "confianca": confianca,
        "grupo_espacial": grupo_final, "leitura_para_pdf": list(para_pagina * pagina.derotation_matrix),
    })
    return linhas


def _sobrepostas(caixa_a, caixa_b):
    if caixa_a is None or caixa_b is None:
        return False
    a, b = pymupdf.Rect(caixa_a), pymupdf.Rect(caixa_b)
    menor = min(a.get_area(), b.get_area())
    return menor > 0 and (a & b).get_area() >= 0.55 * menor


def _texto_sobre_imagem(digitais, imagem):
    # A camada do scan coincide com a imagem. Um rodapé fora dela continua misto.
    for linha in digitais:
        caixa = pymupdf.Rect(linha["caixa"]) / _ESCALA_COORDENADAS
        if caixa.get_area() > 0 and (caixa & imagem).get_area() >= 0.55 * caixa.get_area():
            return True
    return False


def _palavras(texto):
    normalizado = unicodedata.normalize("NFKD", texto).casefold()
    sem_acentos = "".join(c for c in normalizado if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", sem_acentos)


def _combinar(digitais, reconhecidas):
    combinadas = list(digitais)
    for linha in reconhecidas:
        palavras = _palavras(linha["texto"])
        atual = "".join(palavras)
        trecho = " " + " ".join(palavras) + " "
        substituir = []
        descartar = False
        for indice, anterior in enumerate(combinadas):
            if not _sobrepostas(linha["caixa_pagina"], anterior["caixa_pagina"]):
                continue
            outras_palavras = _palavras(anterior["texto"])
            if atual and atual == "".join(outras_palavras):
                if linha.get("grupo_espacial") is not None and anterior["origem"] == "DIGITAL":
                    combinadas[indice] = {
                        **anterior, "caixa": linha["caixa"],
                        "grupo_espacial": linha["grupo_espacial"],
                    }
                descartar = True
                break
            # O OCR às vezes separa em duas caixas uma linha digital completa.
            if linha.get("grupo_espacial") is None and anterior["origem"] == "DIGITAL" and len(atual) >= 3 and (
                trecho in f" {' '.join(outras_palavras)} "
            ):
                descartar = True
                break
            if not _qualidade_textual(anterior["texto"]) and _ocr_util(linha):
                substituir.append(indice)
        if not descartar:
            for indice in reversed(substituir):
                del combinadas[indice]
            combinadas.append(linha)
    return combinadas


def _ordenar(linhas):
    posicionadas = sorted(
        (linha for linha in linhas if linha["caixa"] is not None),
        key=lambda linha: (linha["caixa"][1], linha["caixa"][0]),
    )
    ordenadas = []
    faixa = []
    for linha in posicionadas:
        caixa = linha["caixa"]
        if faixa:
            referencia = faixa[0]["caixa"]
            sobreposicao = min(caixa[3], referencia[3]) - max(caixa[1], referencia[1])
            if sobreposicao < 0.5 * min(caixa[3] - caixa[1], referencia[3] - referencia[1]):
                ordenadas.extend(sorted(faixa, key=lambda item: item["caixa"][0]))
                faixa = []
        faixa.append(linha)
    ordenadas.extend(sorted(faixa, key=lambda item: item["caixa"][0]))
    ordenadas.extend(linha for linha in linhas if linha["caixa"] is None)
    return ordenadas


def _ordenar_grupos(linhas):
    grupos = {}
    for linha in linhas:
        grupos.setdefault(linha.get("grupo_espacial"), []).append(linha)
    return sorted(
        (_ordenar(grupo) for grupo in grupos.values()),
        key=lambda grupo: min((l["caixa_pagina"][1] for l in grupo if l["caixa_pagina"]), default=math.inf),
    )


def ler_pagina(pagina, obter_leitor=None, *, preferir_digital=False):
    """Usa a composição visual para OCR seletivo e entrega linhas na mesma escala."""
    texto_pagina = pagina.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT)
    digitais = _linhas_digitais(pagina, texto_pagina)
    imagens = _regioes_de_imagem(pagina)
    principal = max(imagens, key=lambda area: area.get_area(), default=None)
    cobertura = principal.get_area() / pagina.rect.get_area() if principal is not None else 0
    texto_bom = bool(digitais) and _qualidade_textual(" ".join(linha["texto"] for linha in digitais))
    tem_conteudo = bool(digitais or imagens or pagina.get_bboxlog())
    reconhecidas = []
    orientacoes = []
    digital_fora_da_imagem = any(
        not any(_texto_sobre_imagem([linha], area) for area in imagens) for linha in digitais
    ) if imagens else False
    camada_suficiente = False
    if preferir_digital and texto_bom and cobertura >= 0.85 and not digital_fora_da_imagem:
        topo = min(l["caixa"][1] for l in digitais)
        base = max(l["caixa"][3] for l in digitais)
        if len(digitais) >= 5 and base - topo >= pagina.rect.height * _ESCALA_COORDENADAS * 0.20:
            from ceuma.documentos.analisador import analisar_documento

            resultado = analisar_documento({
                "nome_arquivo": "", "quantidade_paginas": 1, "paginas": [{
                    "numero": 1, "metodo": "DIGITAL",
                    "texto": "\n".join(l["texto"] for l in digitais),
                }],
            })
            campos = resultado["campos"]
            campo_civil = any(c.get("papel") == "TITULAR" and c["valido"] for c in campos["cpfs"]) or any(
                d["campo"] == "DATA_NASCIMENTO" and d["valida"] for d in campos["datas"]
            )
            camada_suficiente = (
                resultado["identificacao"]["status"] == "IDENTIFICADO"
                and campos["nome"]["status"] == "ENCONTRADO" and campo_civil
                and resultado["identificacao"]["tipo"] != "CTPS_FISICA"
            )

    def ler_area(area, *, recorte):
        if preferir_digital and not obter_leitor().pode_rasterizar():
            return []
        return _ler_imagem(pagina, area, obter_leitor, orientacoes, recorte=recorte)

    if obter_leitor is not None and tem_conteudo and not camada_suficiente:
        if ((cobertura >= 0.85 and not digital_fora_da_imagem)
                or (not imagens and not texto_bom)):
            reconhecidas = ler_area(pagina.rect, recorte=False)
        elif imagens:
            for area in imagens:
                reconhecidas.extend(ler_area(area, recorte=True))
            uteis = [linha for linha in reconhecidas if _ocr_util(linha)]
            sem_geometria = any(linha["caixa"] is None for linha in uteis)
            evidencias_distintas = any(o["status"] != "INDISPONIVEL" for o in orientacoes)
            if (sem_geometria or (not uteis and (not texto_bom or cobertura >= 0.30))) and (
                not evidencias_distintas or (len(imagens) == 1 and not digital_fora_da_imagem)
            ):
                reconhecidas = _combinar(
                    reconhecidas,
                    ler_area(pagina.rect, recorte=False),
                )

    grupos = _ordenar_grupos(_combinar(digitais, reconhecidas))
    linhas = [linha for grupo in grupos for linha in grupo]
    isolados = any(linha.get("grupo_espacial") is not None for linha in linhas)
    origens = {linha["origem"] for linha in linhas}
    metodo = "HIBRIDO" if len(origens) == 2 else next(iter(origens), "SEM_TEXTO")
    if cobertura >= 0.85 and _texto_sobre_imagem(digitais, principal):
        composicao = "DIGITALIZADA_COM_CAMADA"
    elif imagens and digitais:
        composicao = "MISTA"
    elif cobertura >= 0.85:
        composicao = "DIGITALIZADA"
    else:
        composicao = "DIGITAL" if digitais else "SEM_TEXTO" if not linhas else "DIGITALIZADA"

    texto = (
        pagina.get_text("text", sort=True, textpage=texto_pagina)
        if origens == {"DIGITAL"} and not isolados else "\n".join(linha["texto"] for linha in linhas)
    )
    return {
        "texto": texto,
        "metodo": metodo, "composicao": composicao,
        "linhas": linhas,
        "orientacoes": orientacoes,
        "grupos_leitura": grupos if isolados else [],
        # Consumidores existentes esperam o caminho textual em páginas apenas digitais.
        "linhas_ocr": linhas if "OCR" in origens or isolados else [],
    }
