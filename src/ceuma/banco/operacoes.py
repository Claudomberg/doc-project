
def adicionar_documento(
    conexao,
    analise_id,
    nome_arquivo,
    caminho_origem,
    tipo_identificado=None,
    quantidade_paginas=None,
    status_leitura="PENDENTE",
    confirmar=True
):
    cursor = conexao.execute("""
        INSERT INTO documentos (
            analise_id,
            nome_arquivo,
            caminho_origem,
            tipo_identificado,
            quantidade_paginas,
            status_leitura,
            escopo
        )
        VALUES (?, ?, ?, ?, ?, ?, CASE WHEN (SELECT modo FROM analises WHERE id = ?)
                                      = 'FAMILIAR' THEN 'INCONCLUSIVO' END)
    """, (
        analise_id,
        nome_arquivo,
        caminho_origem,
        tipo_identificado,
        quantidade_paginas,
        status_leitura,
        analise_id
    ))

    if confirmar:
        conexao.commit()

    return cursor.lastrowid


def salvar_campo_extraido(
    conexao,
    documento_id,
    nome_campo,
    valor,
    pagina_origem=None,
    trecho_origem=None,
    confianca=None,
    status="ENCONTRADO",
    confirmar=True
):
    cursor = conexao.execute("""
        INSERT INTO campos_extraidos (
            documento_id,
            nome_campo,
            valor,
            pagina_origem,
            trecho_origem,
            confianca,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        documento_id,
        nome_campo,
        valor,
        pagina_origem,
        trecho_origem,
        confianca,
        status
    ))

    if confirmar:
        conexao.commit()

    return cursor.lastrowid


def registrar_conferencia(
    conexao,
    analise_id,
    campo_verificado,
    resultado,
    observacao=None,
    confirmar=True,
    *,
    pessoa_id=None,
    documento_1_id=None,
    documento_2_id=None
):
    cursor = conexao.execute("""
        INSERT INTO conferencias (
            analise_id,
            campo_verificado,
            resultado,
            observacao,
            pessoa_id,
            documento_1_id,
            documento_2_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        analise_id,
        campo_verificado,
        resultado,
        observacao,
        pessoa_id,
        documento_1_id,
        documento_2_id
    ))

    if confirmar:
        conexao.commit()

    return cursor.lastrowid
