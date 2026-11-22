"""Convenções locais para interpretar identificadores empresariais."""
import re


def interpretar_cnpj(texto):
    if re.fullmatch(r"[0-9]{14}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2}", texto):
        return re.sub(r"\D", "", texto), "ENCONTRADO", "FORMATO_NUMERICO_RECONHECIDO_SEM_VALIDACAO_DV"
    if re.search(r"[A-Z]", texto):
        return None, "EVIDENCIA_INSUFICIENTE", "FORMATO_NAO_SUPORTADO"
    return None, "INVALIDO", "FORMATO_INVALIDO"
