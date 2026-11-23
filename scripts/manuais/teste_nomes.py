
from ceuma.extracao.campos import extrair_nome_titular


def testar():
    print("=== TESTE NOMES CEUMA ===")

    casos = [
        (
            "Nome na mesma linha",
            "NOME: MARIA EDUARDA SILVA",
            "MARIA EDUARDA SILVA"
        ),
        (
            "Nome na linha seguinte",
            "NOME\nJOAO PEDRO OLIVEIRA",
            "JOAO PEDRO OLIVEIRA"
        ),
        (
            "Nome com filiacao",
            "NOME DA MAE: ANA SILVA\n"
            "NOME: CARLOS ALMEIDA",
            "CARLOS ALMEIDA"
        ),
        (
            "Sem nome do titular",
            "NOME DO PAI: PEDRO SILVA",
            None
        )
    ]

    for descricao, texto, esperado in casos:
        resultado = extrair_nome_titular(texto)

        print("\nTeste:", descricao)
        print("Obtido:", resultado["valor"])

        assert resultado["valor"] == esperado

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
