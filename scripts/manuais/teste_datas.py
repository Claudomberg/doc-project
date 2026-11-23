
from ceuma.extracao.campos import (
    extrair_datas,
    extrair_datas_documento
)


def testar():
    print("=== TESTE DE DATAS CEUMA ===")

    texto = (
        "DATA DE NASCIMENTO: 15/08/2005\n"
        "DATA DE EXPEDICAO: 20.03.2022\n"
        "DATA DE VALIDADE: 31-12-2030\n"
        "DATA INCORRETA: 31/02/2020"
    )

    datas = extrair_datas(texto)

    for data in datas:
        print(
            "Data:", data["valor"],
            "| Valida:", data["valida"]
        )

    assert len(datas) == 4

    assert datas[0]["valor"] == "15/08/2005"
    assert datas[0]["valida"] is True

    assert datas[1]["valor"] == "20/03/2022"
    assert datas[1]["valida"] is True

    assert datas[2]["valor"] == "31/12/2030"
    assert datas[2]["valida"] is True

    assert datas[3]["valida"] is False

    documento_ficticio = {
        "paginas": [
            {
                "numero": 1,
                "texto": texto,
                "metodo": "DIGITAL"
            }
        ]
    }

    datas_documento = extrair_datas_documento(
        documento_ficticio
    )

    assert len(datas_documento) == 4
    assert all(
        data["pagina_origem"] == 1
        for data in datas_documento
    )

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
