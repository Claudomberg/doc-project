
from leitor.leitor_pdf import ler_pdf
from analise.extrator_campos import extrair_cpfs_documento


def testar():
    print("=== TESTE EXTRACAO DOCUMENTO CEUMA ===")

    documento = ler_pdf("pdf/jpg2pdf.pdf")

    cpfs = extrair_cpfs_documento(documento)

    print("Arquivo:", documento["nome_arquivo"])
    print("Total de paginas:", documento["quantidade_paginas"])
    print("CPFs encontrados:", len(cpfs))

    for cpf in cpfs:
        # Não imprime o CPF completo no terminal.
        print("\nCPF encontrado:")
        print("Valido:", cpf["valido"])
        print("Pagina:", cpf["pagina_origem"])
        print("Metodo:", cpf["metodo_leitura"])

    print("\nTeste concluido!")


if __name__ == "__main__":
    testar()
