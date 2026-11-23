
from ceuma.extracao.campos import (
    extrair_datas_classificadas_documento
)


def testar():
    print("=== TESTE DATAS PDF CEUMA ===")

    # Documento fictício para testar a integração
    # sem precisar inicializar o OCR.
    documento = {
        "nome_arquivo": "teste_ficticio.pdf",
        "quantidade_paginas": 2,
        "paginas": [
            {
                "numero": 1,
                "metodo": "DIGITAL",
                "texto": (
                    "NASCIMENTO: 15/08/2005\n"
                    "EXPEDICAO: 20/03/2022"
                )
            },
            {
                "numero": 2,
                "metodo": "OCR",
                "texto": (
                    "VALIDADE: 31/12/2030\n"
                    "OUTRA DATA: 10/01/2020"
                )
            }
        ]
    }

    resultados = extrair_datas_classificadas_documento(
        documento
    )

    for resultado in resultados:
        print(
            resultado["campo"],
            resultado["valor"],
            "| Pagina:", resultado["pagina_origem"],
            "| Metodo:", resultado["metodo_leitura"]
        )

    assert len(resultados) == 4

    assert resultados[0]["campo"] == "DATA_NASCIMENTO"
    assert resultados[0]["pagina_origem"] == 1

    assert resultados[1]["campo"] == "DATA_EXPEDICAO"
    assert resultados[1]["pagina_origem"] == 1

    assert resultados[2]["campo"] == "DATA_VALIDADE"
    assert resultados[2]["pagina_origem"] == 2

    assert resultados[3]["campo"] == "DATA_NAO_CLASSIFICADA"
    assert resultados[3]["pagina_origem"] == 2

    assert all(
        resultado["trecho_origem"]
        for resultado in resultados
    )

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
