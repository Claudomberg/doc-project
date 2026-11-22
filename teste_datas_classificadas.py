
from analise.extrator_campos import extrair_datas_classificadas


def testar():
    print("=== TESTE DATAS CLASSIFICADAS CEUMA ===")

    texto = (
        "DATA DE NASCIMENTO: 15/08/2005\n"
        "EXPEDIÇÃO: 20/03/2022\n"
        "VALIDADE: 31/12/2030\n"
        "OUTRA DATA: 10/01/2020"
    )

    resultados = extrair_datas_classificadas(texto)

    for resultado in resultados:
        print(
            resultado["campo"],
            resultado["valor"],
            "Valida:", resultado["valida"]
        )

    assert [r["campo"] for r in resultados] == [
        "DATA_NASCIMENTO",
        "DATA_EXPEDICAO",
        "DATA_VALIDADE",
        "DATA_NAO_CLASSIFICADA"
    ]

    assert len(resultados) == 4
    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
