
from analise.extrator_campos import extrair_rg


def testar():
    print("=== TESTE RG CEUMA ===")

    casos = [
        (
            "RG na mesma linha",
            "RG: 12.345.678-9",
            "12.345.678-9"
        ),
        (
            "RG na linha seguinte",
            "REGISTRO GERAL\n123456789",
            "123456789"
        ),
        (
            "Documento sem RG",
            "CPF: 529.982.247-25",
            None
        )
    ]

    for descricao, texto, esperado in casos:
        resultado = extrair_rg(texto)

        print("\nTeste:", descricao)
        print("Obtido:", resultado["valor"])

        assert resultado["valor"] == esperado

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
