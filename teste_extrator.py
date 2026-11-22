
from analise.extrator_campos import (
    validar_cpf,
    extrair_cpfs
)


def testar():
    print("=== TESTE EXTRATOR CEUMA ===")

    # Exemplos fictícios para testar a validação.
    cpf_valido = "529.982.247-25"
    cpf_invalido = "111.111.111-11"

    assert validar_cpf(cpf_valido) is True
    assert validar_cpf(cpf_invalido) is False

    texto = (
        "CPF do titular: 529.982.247-25\n"
        "CPF informado incorretamente: 111.111.111-11"
    )

    resultados = extrair_cpfs(texto)

    for resultado in resultados:
        print(
            "CPF:",
            resultado["valor"],
            "| Valido:",
            resultado["valido"]
        )

    assert len(resultados) == 2

    print("\nTeste concluido com sucesso!")


if __name__ == "__main__":
    testar()
