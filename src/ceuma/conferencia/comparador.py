
import re
import unicodedata


def normalizar_valor(valor):
    """Padroniza valores para comparação."""

    if valor is None:
        return ""

    texto = str(valor).strip().upper()

    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        caractere for caractere in texto
        if unicodedata.category(caractere) != "Mn"
    )

    texto = re.sub(r"\s+", " ", texto)

    return texto


def comparar_campo(nome_campo, valor_1, valor_2):
    """Compara dois valores de um mesmo campo."""

    primeiro = normalizar_valor(valor_1)
    segundo = normalizar_valor(valor_2)

    if not primeiro or not segundo:
        resultado = "NAO_CONFERIDO"

    elif primeiro == segundo:
        resultado = "COMPATIVEL"

    else:
        resultado = "DIVERGENTE"

    return {
        "campo": nome_campo,
        "resultado": resultado
    }
