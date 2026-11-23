
from ceuma.documentos.identificador import identificar_documento
from ceuma.leitura.pdf import ler_pdf


def testar():
    print("=== TESTE IDENTIFICADOR CEUMA ===")

    resultado = ler_pdf("pdf/jpg2pdf.pdf")

    texto_completo = "\n".join(
        pagina["texto"]
        for pagina in resultado["paginas"]
    )

    identificacao = identificar_documento(texto_completo)

    print("\nArquivo:", resultado["nome_arquivo"])
    print("Tipo:", identificacao["tipo"])
    print("Pontuação:", identificacao["pontuacao"])
    print("Status:", identificacao["status"])

    print("\nTeste concluído!")


if __name__ == "__main__":
    testar()
