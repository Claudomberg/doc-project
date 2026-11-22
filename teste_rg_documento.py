
from analise.extrator_campos import extrair_rg_documento


def testar():
    print("=== TESTE RG DOCUMENTO CEUMA ===")

    documento = {
        "nome_arquivo": "documento_ficticio.pdf",
        "quantidade_paginas": 2,
        "paginas": [
            {
                "numero": 1,
                "metodo": "DIGITAL",
                "texto": "CPF: 529.982.247-25"
            },
            {
                "numero": 2,
                "metodo": "OCR",
                "texto": "REGISTRO GERAL\n12.345.678-9"
            }
        ]
    }

    resultado = extrair_rg_documento(documento)

    print("Campo:", resultado["campo"])
    print("RG:", resultado["valor"])
    print("Pagina:", resultado["pagina_origem"])
    print("Metodo:", resultado["metodo_leitura"])
    print("Status:", resultado["status"])

    assert resultado["valor"] == "12.345.678-9"
    assert resultado["pagina_origem"] == 2
    assert resultado["metodo_leitura"] == "OCR"
    assert resultado["status"] == "ENCONTRADO"

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
