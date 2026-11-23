
from ceuma.leitura.pdf import ler_pdf


print("=== TESTE LEITURA INTEGRADA CEUMA ===")

resultado = ler_pdf("pdf/jpg2pdf.pdf")

print("Arquivo:", resultado["nome_arquivo"])
print("Paginas:", resultado["quantidade_paginas"])

for pagina in resultado["paginas"]:
    print(f"\nPagina: {pagina['numero']}")
    print("Metodo:", pagina["metodo"])
    print("Caracteres:", len(pagina["texto"]))

print("\nTeste concluido!")
