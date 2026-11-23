
from ceuma.documentos.identificador import (
    normalizar_texto,
    REGRAS_DOCUMENTOS
)
from ceuma.leitura.pdf import ler_pdf


print("=== DIAGNOSTICO IDENTIFICADOR CEUMA ===")

resultado = ler_pdf("pdf/jpg2pdf.pdf")

texto = "\n".join(
    pagina["texto"]
    for pagina in resultado["paginas"]
)

texto_normalizado = normalizar_texto(texto)

print("\n=== VERIFICACAO DE EXPRESSOES ===")

expressoes = [
    "CARTEIRA DE IDENTIDADE",
    "CARTEIRA DEIDENTIDADE",
    "REGISTRO GERAL",
    "INSTITUTO DE IDENTIFICACAO",
    "SECRETARIA DE ESTADO DA SEGURANCA PUBLICA"
]

for expressao in expressoes:
    encontrada = expressao in texto_normalizado

    print(
        f"{expressao}: "
        f"{'ENCONTRADA' if encontrada else 'NAO ENCONTRADA'}"
    )

print("\n=== PONTUACAO POR TIPO ===")

for tipo, palavras in REGRAS_DOCUMENTOS.items():
    encontradas = [
        palavra for palavra in palavras
        if palavra in texto_normalizado
    ]

    print(f"{tipo}: {len(encontradas)}")

print("\nDiagnostico concluido!")
