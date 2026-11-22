"""Datas em português, com gramática limitada a expressões usuais de registro."""

import re

from analise.extrator_campos import extrair_datas, normalizar_rotulo, validar_data


_MESES = dict(zip(
    "JANEIRO FEVEREIRO MARCO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO".split(),
    range(1, 13),
))
_UNIDADES = dict(zip(
    "ZERO UM DOIS TRES QUATRO CINCO SEIS SETE OITO NOVE DEZ ONZE DOZE TREZE CATORZE "
    "QUINZE DEZESSEIS DEZESSETE DEZOITO DEZENOVE".split(), range(20),
))
_UNIDADES.update(UMA=1, DUAS=2, QUATORZE=14, PRIMEIRO=1, PRIMEIRA=1)
_DEZENAS = dict(zip("VINTE TRINTA QUARENTA CINQUENTA SESSENTA SETENTA OITENTA NOVENTA".split(), range(20, 100, 10)))
_CENTENAS = dict(zip(
    "CENTO DUZENTOS TREZENTOS QUATROCENTOS QUINHENTOS SEISCENTOS SETECENTOS OITOCENTOS NOVECENTOS".split(),
    range(100, 1000, 100),
))
_PALAVRA_ATE_99 = "|".join((*_UNIDADES, *_DEZENAS))
_PALAVRA_ANO = "|".join((*_UNIDADES, *_DEZENAS, *_CENTENAS, "CEM", "MIL"))
_DIA = rf"(?:[0-9]{{1,2}}(?:[º°])?|(?:{_PALAVRA_ATE_99})(?: E (?:{_PALAVRA_ATE_99}))?)"
_ANO = rf"(?:[0-9]{{4}}|(?:{_PALAVRA_ANO})(?: (?:E )?(?:{_PALAVRA_ANO})){{0,6}})"
_CORPO_EXTENSO = rf"(?:AOS? )?(?P<dia>{_DIA})(?: DIAS?(?: DO MES)?)? DE (?P<mes>{'|'.join(_MESES)}) (?:DO ANO )?DE (?P<ano>{_ANO})"
_EXTENSO = re.compile(_CORPO_EXTENSO)
_NUMERICA = r"[0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{4}"
PADRAO_DATA = rf"(?:{_NUMERICA}(?!\w)|{_CORPO_EXTENSO}(?!\w| +E\b))"


def _ate_99(texto):
    if texto in _UNIDADES:
        return _UNIDADES[texto]
    dezena, _, unidade = texto.partition(" E ")
    if dezena in _DEZENAS and (not unidade or 1 <= _UNIDADES.get(unidade, -1) <= 9):
        return _DEZENAS[dezena] + _UNIDADES.get(unidade, 0)
    return None


def _ate_999(texto):
    if texto == "CEM":
        return 100
    centena, separador, resto = texto.partition(" E ")
    if centena in _CENTENAS:
        if not separador:
            return _CENTENAS[centena] if centena != "CENTO" else None
        valor = _ate_99(resto)
        return _CENTENAS[centena] + valor if valor else None
    return _ate_99(texto)


def _ano(texto):
    if texto.isdecimal():
        return int(texto)
    partes = texto.split()
    if partes.count("MIL") != 1:
        return None
    indice = partes.index("MIL")
    milhar = _UNIDADES.get(" ".join(partes[:indice]), None) if indice else 1
    if milhar is None or not 1 <= milhar <= 9:
        return None
    sufixo = partes[indice + 1:]
    if sufixo[:1] == ["E"]:
        sufixo = sufixo[1:]
        if not sufixo:
            return None
    resto = _ate_999(" ".join(sufixo)) if sufixo else 0
    return milhar * 1000 + resto if resto is not None else None


def interpretar_data(texto):
    """Retorna a data normalizada e sua validade, ou ausência de leitura segura."""
    texto = " ".join(normalizar_rotulo(texto).split())
    if re.fullmatch(_NUMERICA, texto):
        data, = extrair_datas(texto)
        return data["valor"], data["valida"]
    partes = _EXTENSO.fullmatch(texto)
    if partes is None:
        return None, False
    dia_lido = partes["dia"].rstrip("º°")
    dia = int(dia_lido) if dia_lido.isdecimal() else _ate_99(dia_lido)
    ano = _ano(partes["ano"])
    if dia is None or ano is None:
        return None, False
    valor = f"{dia:02d}/{_MESES[partes['mes']]:02d}/{ano:04d}"
    return valor, validar_data(valor)
