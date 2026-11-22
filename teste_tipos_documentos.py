
from documentos.identificador import identificar_documento

testes = [
    (
        "RG",
        "INSTITUTO DE IDENTIFICAÇÃO\n"
        "SECRETARIA DE ESTADO DA SEGURANÇA PÚBLICA\n"
        "REGISTRO GERAL\nNOME: PESSOA FICTÍCIA",
        "DOCUMENTO_IDENTIDADE"
    ),
    (
        "CNH",
        "CARTEIRA NACIONAL DE HABILITAÇÃO\nCAT. HAB.",
        "CNH"
    ),
    (
        "Certidão de nascimento",
        "CERTIDÃO DE NASCIMENTO\nREGISTRO CIVIL DAS PESSOAS NATURAIS",
        "CERTIDAO_NASCIMENTO"
    ),
    (
        "Certidão de casamento",
        "CERTIDÃO DE CASAMENTO",
        "CERTIDAO_CASAMENTO"
    ),
    (
        "Contrato de trabalho",
        "CONTRATO DE TRABALHO",
        "VINCULO_TRABALHISTA"
    ),
    (
        "Documento desconhecido",
        "DOCUMENTO ADMINISTRATIVO SEM IDENTIFICAÇÃO",
        "DOCUMENTO_DESCONHECIDO"
    )
]

print("=== TESTE DE TIPOS DE DOCUMENTOS CEUMA ===")

aprovados = 0

for nome, texto, esperado in testes:
    resultado = identificar_documento(texto)

    correto = resultado["tipo"] == esperado

    if correto:
        aprovados += 1

    print(f"\nDocumento: {nome}")
    print(f"Esperado: {esperado}")
    print(f"Obtido: {resultado['tipo']}")
    print(f"Resultado: {'OK' if correto else 'FALHOU'}")

print(f"\nTestes aprovados: {aprovados}/{len(testes)}")
assert aprovados == len(testes)
