"""Perfil de extração da CNH e da Permissão para Dirigir."""

import re
from datetime import datetime
from itertools import combinations

from ceuma.extracao.associador_espacial import _caixa, _sobreposicao_vertical
from ceuma.documentos.identidade import (
    PerfilDocumental, _ROTULOS, _SIGLA, _SIGLA_UF, _UF, _numero_rg, _registro, extrair_campos_perfil,
)


_ROTULOS_CNH = {
    **{campo: _ROTULOS[campo] for campo in (
        "NOME", "CPF", "DATA_NASCIMENTO", "DATA_VALIDADE", "NOME_MAE", "NOME_PAI",
        "FILIACAO", "NACIONALIDADE",
    )},
    "RG_ORIGEM": (
        "DOC. IDENTIDADE / ORGAO EMISSOR / UF", "DOC IDENTIDADE / ORGAO EMISSOR / UF",
        "DOCUMENTO DE IDENTIDADE / ORGAO EMISSOR / UF",
        "DOC IDENTIDADE", "DOC. IDENTIDADE", "DOCUMENTO DE IDENTIDADE",
        "IDENTIDADE DE ORIGEM", "RG DE ORIGEM", "REGISTRO GERAL", "R.G.", "RG",
    ),
    "ORGAO_EMISSOR_RG": ("ORGAO EMISSOR DO RG / UF", "ORGAO EMISSOR DO RG",
                        "ORGAO EXPEDIDOR DO RG", "ORGAO EMISSOR DA IDENTIDADE"),
    "UF_EMISSAO_RG": ("UF DE EMISSAO DO RG", "UF DO RG", "UF DA IDENTIDADE"),
    "ORGAO_EMISSOR_CNH": ("ORGAO EMISSOR DA CNH / UF", "ORGAO EMISSOR DA CNH",
                         "ORGAO RESPONSAVEL PELA CNH", "ORGAO DE TRANSITO"),
    "UF_EMISSAO_CNH": ("UF DE EMISSAO DA CNH", "UF DA CNH", "UF DO ORGAO DE TRANSITO"),
    "REGISTRO_CNH": (
        "NUMERO DE REGISTRO DA CNH", "NUMERO DE REGISTRO", "NUMERO REGISTRO",
        "REGISTRO DA CNH", "REGISTRO CNH", "N REGISTRO", "N. REGISTRO", "NO REGISTRO",
        "N DE REGISTRO", "N. DE REGISTRO", "NO DE REGISTRO", "REGISTRO",
    ),
    "DATA_EMISSAO": ("DATA DE EMISSAO DA CNH", "DATA DE EMISSAO", "DATA EMISSAO",
                    "EMISSAO", "DATA DE EXPEDICAO", "EXPEDICAO"),
    "DATA_PRIMEIRA_HABILITACAO": ("DATA DA PRIMEIRA HABILITACAO", "PRIMEIRA HABILITACAO",
                                 "1A HABILITACAO", "1 HABILITACAO", "1A HAB", "1A HAB."),
    "CATEGORIA_CNH": ("CATEGORIA DE HABILITACAO", "CATEGORIA HAB", "CATEGORIA",
                      "CAT. HAB.", "CAT. HAB", "CAT HAB", "CAT."),
}
_ROTULOS_CNH["NOME"] += ("NOME E SOBRENOME", "NOME E SOBRENOME / NAME AND SURNAME")
_ROTULOS_CNH["CPF"] = tuple(r for r in _ROTULOS["CPF"] if r != "REGISTRO GERAL CPF")
_ROTULOS_CNH["DATA_NASCIMENTO"] += ("DATA DE NASCIMENTO / DATE OF BIRTH",)
_ROTULOS_CNH["DATA_VALIDADE"] += ("DATA DE VALIDADE DA CNH",)
_CONTROLES = ("NUMERO DE CONTROLE", "CODIGO DE CONTROLE", "CONTROLE",
              "RENACH", "NUMERO DO FORMULARIO", "NUMERO DO ESPELHO", "NUMERO DO DOCUMENTO")
_OUTROS = (*_CONTROLES, "ORGAO EMISSOR", "ORGAO EXPEDIDOR", "REGISTRO GERAL CPF",
           "ACC", "AUTORIZACAO PARA CONDUZIR CICLOMOTOR", "RESTRICOES", "PERMISSAO PARA DIRIGIR",
           "CARTEIRA NACIONAL DE HABILITACAO", "LOCAL E DATA", "UF DE NASCIMENTO")
_DOC_SIGLA = re.compile(rf"(?P<rg>.+?)\s+[/ -]*\s*(?P<orgao>{_SIGLA})(?:\s*[/.-]?\s*(?P<uf>{_UF}))?")
_ORDINAL = re.compile(r"^(?:2\s+E\s+1|[1-9][A-D]?)(?:\s*[.):-]\s*|\s+)(?=[A-Z])")


def _padronizar_rotulos(texto):
    texto = re.sub(r"\bN\.?[º°]\s*(?=(?:DE\s+)?REGISTRO\b)", "NUMERO ", texto)
    texto = re.sub(r"\b1[ªº°](?=\s+HAB)", "1A", texto)
    texto = re.sub(r"\bORG\.?\s*EMISSOR\b", "ORGAO EMISSOR", texto)
    texto = re.sub(r"\bDOC\.(?=IDENTIDADE)", "DOC. ", texto)
    texto = re.sub(r"\s*/\s*(?=(?:ORGAO EMISSOR|UF)\b)", " / ", texto)
    texto = re.sub(r"^CPF D[OA] CONDUTOR[A]?\b", "CPF DO TITULAR", texto)
    if re.match(r"^1(?:[.ªA])?\s+HAB", texto):
        return re.sub(r"^1\.\s+", "1 ", texto)
    return _ORDINAL.sub("", texto, count=1)


def _registro_cnh(texto):
    if not re.fullmatch(r"[0-9]+(?: [0-9]+)*", texto):
        return None, False
    numero = texto.replace(" ", "")
    return (numero, True) if len(numero) == 11 else (None, False)


def _categoria(texto):
    if not re.fullmatch(r"[A-E](?:\s*[/+,-]?\s*[A-E]){0,4}", texto):
        return None, False
    if texto == "ACC":
        return None, False
    letras = re.findall(r"[A-E]", texto)
    categorias = set(letras)
    if len(categorias) != len(letras) or (len(categorias) > 1 and (
        len(categorias) != 2 or "A" not in categorias
    )):
        return texto, "AMBIGUO"
    return "".join(sorted(categorias)), True


def _rg_origem(texto):
    partes = _DOC_SIGLA.fullmatch(texto)
    numero = partes["rg"] if partes else texto
    return (texto, True) if _numero_rg(numero) else (None, False)


def _decompor(campo, texto):
    if campo != "RG_ORIGEM" or not (partes := _DOC_SIGLA.fullmatch(texto)):
        return texto, ()
    extras = [("ORGAO_EMISSOR_RG", partes["orgao"])]
    if partes["uf"]:
        extras.append(("UF_EMISSAO_RG", partes["uf"]))
    return partes["rg"], extras


def _complementos_do_documento(campo, rotulo, linhas, indice):
    """Lê caixas vizinhas apenas para o rótulo composto de documento, órgão e UF."""
    if campo != "RG_ORIGEM" or not rotulo.endswith("ORGAO EMISSOR / UF"):
        return
    if _DOC_SIGLA.fullmatch(linhas[indice]["texto"]):
        return
    grupo = linhas[indice].get("grupo_espacial")
    caixas = [(i, l["texto"], _caixa(l)) for i, l in enumerate(linhas) if l.get("grupo_espacial") == grupo]

    def vizinho(origem, destino):
        return (destino is not None and 0 <= destino[0] - origem[2] <= 32
                and _sobreposicao_vertical(origem, destino) >= 0.50)

    rg = _caixa(linhas[indice])
    for i, texto, caixa in caixas:
        if not vizinho(rg, caixa):
            continue
        if _SIGLA_UF.fullmatch(texto):
            yield i, "ORGAO_EMISSOR_RG", texto
        elif re.fullmatch(_SIGLA, texto):
            yield i, "ORGAO_EMISSOR_RG", texto
            for j, uf, caixa_uf in caixas:
                if re.fullmatch(_UF, uf) and vizinho(caixa, caixa_uf):
                    yield j, "UF_EMISSAO_RG", uf


_PERFIL = PerfilDocumental(
    _ROTULOS_CNH,
    outros=_OUTROS, normalizar=_padronizar_rotulos, cpf_contextual=True,
    orgaos={"ORGAO_EMISSOR_RG": "UF_EMISSAO_RG", "ORGAO_EMISSOR_CNH": "UF_EMISSAO_CNH"},
    validadores={"REGISTRO_CNH": _registro_cnh, "CATEGORIA_CNH": _categoria, "RG_ORIGEM": _rg_origem},
    decompor=_decompor, complementar=_complementos_do_documento, reservar=_CONTROLES,
)
_ORDEM_DATAS = ("DATA_NASCIMENTO", "DATA_PRIMEIRA_HABILITACAO", "DATA_EMISSAO", "DATA_VALIDADE")
CAMPOS_CNH = tuple(_ROTULOS_CNH)


def extrair_cnh(documento):
    campos = extrair_campos_perfil(documento, _PERFIL)
    datas = {
        campo: datetime.strptime(campos[campo]["valor"], "%d/%m/%Y")
        for campo in _ORDEM_DATAS if campos[campo]["status"] == "ENCONTRADO"
    }
    pares = [(anterior, posterior) for anterior, posterior in combinations(datas, 2)
             if datas[anterior] > datas[posterior]]
    if pares:
        campos["CRONOLOGIA_CNH"] = {
            **_registro("CRONOLOGIA_CNH", "INCONSISTENCIA_CRONOLOGICA"),
            "pares": pares,
        }
    return campos
