
from pathlib import Path
from leitor.leitor_pdf import ler_pdf


def testar():
    caminho = input("Digite o caminho do PDF: ").strip().strip('"')

    resultado = ler_pdf(caminho)

    print("\n=== TESTE DO LEITOR CEUMA ===")
    print("Arquivo:", resultado["nome_arquivo"])
    print("Total de páginas:", resultado["quantidade_paginas"])

    for pagina in resultado["paginas"]:
        print(f"\n--- PÁGINA {pagina['numero']} ---")
        print("Possui texto:", pagina["possui_texto"])
        print("Caracteres:", len(pagina["texto"]))

        # Não exibimos o conteúdo para evitar expor dados.
        if not pagina["possui_texto"]:
            print("Página sem texto digital detectado.")

    print("\nLeitura concluída!")


if __name__ == "__main__":
    try:
        testar()
    except Exception as erro:
        print(f"\nErro: {erro}")
