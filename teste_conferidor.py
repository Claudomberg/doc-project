
from analise.conferidor import comparar_campo


def testar():
    print("=== TESTE CONFERIDOR CEUMA ===")

    casos = [
        (
            "NOME",
            "MARIA EDUARDA SILVA",
            "Maria Eduarda Silva",
            "COMPATIVEL"
        ),
        (
            "NOME",
            "JOÃO PEDRO OLIVEIRA",
            "JOAO PEDRO OLIVEIRA",
            "COMPATIVEL"
        ),
        (
            "NOME",
            "MARIA EDUARDA SILVA",
            "MARIA EDUARDA SOUSA",
            "DIVERGENTE"
        ),
        (
            "DATA_NASCIMENTO",
            "15/08/2005",
            "15/08/2005",
            "COMPATIVEL"
        ),
        (
            "NOME",
            "MARIA EDUARDA SILVA",
            None,
            "NAO_CONFERIDO"
        )
    ]

    for campo, valor_1, valor_2, esperado in casos:
        resultado = comparar_campo(
            campo,
            valor_1,
            valor_2
        )

        print(
            campo,
            "| Resultado:",
            resultado["resultado"]
        )

        assert resultado["resultado"] == esperado

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
