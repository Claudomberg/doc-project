
from ceuma.documentos.analisador import analisar_documento


def testar():
    print("=== TESTE ANALISADOR CEUMA ===")

    documento = {
        "nome_arquivo": "identidade_ficticia.pdf",
        "caminho_origem": "teste/identidade_ficticia.pdf",
        "quantidade_paginas": 2,
        "paginas": [
            {
                "numero": 1,
                "metodo": "DIGITAL",
                "texto": (
                    "CARTEIRA DE IDENTIDADE\n"
                    "NOME: MARIA EDUARDA SILVA\n"
                    "CPF: 529.982.247-25\n"
                    "NASCIMENTO: 15/08/2005"
                )
            },
            {
                "numero": 2,
                "metodo": "OCR",
                "texto": (
                    "REGISTRO GERAL\n"
                    "12.345.678-9\n"
                    "EXPEDICAO: 20/03/2022"
                )
            }
        ]
    }

    resultado = analisar_documento(documento)

    identificacao = resultado["identificacao"]
    campos = resultado["campos"]

    print("Arquivo:", resultado["arquivo"])
    print("Tipo:", identificacao["tipo"])
    print("Nome encontrado:", campos["nome"]["status"])
    print("CPFs encontrados:", len(campos["cpfs"]))
    print("RG encontrado:", campos["rg"]["status"])
    print("Datas encontradas:", len(campos["datas"]))

    assert identificacao["tipo"] == "DOCUMENTO_IDENTIDADE"

    assert campos["nome"]["valor"] == "MARIA EDUARDA SILVA"

    assert len(campos["cpfs"]) == 1
    assert campos["cpfs"][0]["valido"] is True

    assert campos["rg"]["valor"] == "12.345.678-9"
    assert campos["rg"]["pagina_origem"] == 2

    assert len(campos["datas"]) == 2

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
