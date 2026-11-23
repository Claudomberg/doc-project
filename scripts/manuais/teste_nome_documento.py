
from ceuma.extracao.campos import extrair_nome_documento


def testar():
    print("=== TESTE NOME DOCUMENTO CEUMA ===")

    documento = {
        "nome_arquivo": "documento_ficticio.pdf",
        "quantidade_paginas": 2,
        "paginas": [
            {
                "numero": 1,
                "metodo": "DIGITAL",
                "texto": (
                    "NOME DA MAE: ANA SILVA\n"
                    "DATA DE NASCIMENTO: 15/08/2005"
                )
            },
            {
                "numero": 2,
                "metodo": "OCR",
                "texto": (
                    "NOME: MARIA EDUARDA SILVA\n"
                    "CPF: 529.982.247-25"
                )
            }
        ]
    }

    resultado = extrair_nome_documento(documento)

    print("Campo:", resultado["campo"])
    print("Nome:", resultado["valor"])
    print("Pagina:", resultado["pagina_origem"])
    print("Metodo:", resultado["metodo_leitura"])
    print("Status:", resultado["status"])

    assert resultado["valor"] == "MARIA EDUARDA SILVA"
    assert resultado["pagina_origem"] == 2
    assert resultado["metodo_leitura"] == "OCR"
    assert resultado["status"] == "ENCONTRADO"

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
