from banco.conexao import consultar_modo_ativo, transacao_operacao


def criar_pessoa(conexao, analise_id, papel="INDEFINIDO"):
    """Cria um identificador temporário no contexto da análise."""
    with transacao_operacao(conexao):
        consultar_modo_ativo(conexao, analise_id)
        cursor = conexao.execute(
            "INSERT INTO pessoas (analise_id, papel) VALUES (?, ?)", (analise_id, papel),
        )
        consultar_modo_ativo(conexao, analise_id)
        return cursor.lastrowid


def _alterar_atribuicao(conexao, documento_id, escopo, pessoas):
    with transacao_operacao(conexao):
        documento = conexao.execute(
            "SELECT analise_id, escopo FROM documentos WHERE id = ?", (documento_id,),
        ).fetchone()
        if documento is None:
            raise ValueError("Documento indisponível.")
        analise_id, escopo_atual = documento
        if consultar_modo_ativo(conexao, analise_id) != "FAMILIAR":
            raise ValueError("A atribuição explícita exige uma análise familiar.")
        vinculos = {row[0] for row in conexao.execute("""
            SELECT pessoa_id FROM documentos_pessoas
            WHERE analise_id = ? AND documento_id = ?
        """, (analise_id, documento_id))}
        if escopo_atual == escopo and vinculos == pessoas:
            return
        conexao.executemany("""
            DELETE FROM documentos_pessoas
            WHERE analise_id = ? AND documento_id = ? AND pessoa_id = ?
        """, ((analise_id, documento_id, pessoa_id) for pessoa_id in sorted(vinculos - pessoas)))
        conexao.execute("UPDATE documentos SET escopo = ? WHERE id = ?", (escopo, documento_id))
        conexao.executemany("""
            INSERT INTO documentos_pessoas (analise_id, documento_id, pessoa_id)
            VALUES (?, ?, ?)
        """, ((analise_id, documento_id, pessoa_id) for pessoa_id in sorted(pessoas - vinculos)))
        consultar_modo_ativo(conexao, analise_id)


def atribuir_documento(conexao, documento_id, pessoa_id):
    """Atribui ou substitui explicitamente a pessoa a quem o documento pertence."""
    _alterar_atribuicao(conexao, documento_id, "INDIVIDUAL", {pessoa_id})


def marcar_documento_compartilhado(conexao, documento_id, pessoas_ids=()):
    """Um documento compartilhado pode ter vínculos explícitos ou abranger o processo."""
    _alterar_atribuicao(conexao, documento_id, "COMPARTILHADO", set(pessoas_ids))


def marcar_documento_inconclusivo(conexao, documento_id):
    _alterar_atribuicao(conexao, documento_id, "INCONCLUSIVO", set())


def remover_atribuicao(conexao, documento_id):
    """Devolve o documento à fila de atribuição."""
    marcar_documento_inconclusivo(conexao, documento_id)


def listar_documentos_da_pessoa(conexao, analise_id, pessoa_id):
    consultar_modo_ativo(conexao, analise_id)
    return conexao.execute("""
        SELECT d.id, d.nome_arquivo, d.escopo
        FROM documentos_pessoas v JOIN documentos d ON d.id = v.documento_id
        WHERE v.analise_id = ? AND v.pessoa_id = ? ORDER BY d.id
    """, (analise_id, pessoa_id)).fetchall()


def listar_documentos_sem_atribuicao(conexao, analise_id):
    consultar_modo_ativo(conexao, analise_id)
    return conexao.execute("""
        SELECT d.id, d.nome_arquivo, d.escopo FROM documentos d
        WHERE d.analise_id = ? AND (d.escopo IS NULL OR d.escopo = 'INCONCLUSIVO'
            OR (d.escopo = 'INDIVIDUAL' AND NOT EXISTS (
                SELECT 1 FROM documentos_pessoas v
                WHERE v.analise_id = d.analise_id AND v.documento_id = d.id)))
        ORDER BY d.id
    """, (analise_id,)).fetchall()


def agrupar_documentos_elegiveis(conexao, analise_id, modo):
    """Agrupa IDs em ordem de documento, sem inferir identidades."""
    if modo == "INDIVIDUAL":
        ids = [row[0] for row in conexao.execute(
            """SELECT id FROM documentos WHERE analise_id = ?
               AND COALESCE(tipo_identificado, '') <> 'CERTIDAO_CASAMENTO' ORDER BY id""",
            (analise_id,),
        )]
        return {None: ids}

    grupos = {}
    for pessoa_id, documento_id in conexao.execute("""
        SELECT v.pessoa_id, v.documento_id
        FROM documentos_pessoas v JOIN documentos d ON d.id = v.documento_id
        WHERE v.analise_id = ? AND d.escopo = 'INDIVIDUAL'
          AND COALESCE(d.tipo_identificado, '') <> 'CERTIDAO_CASAMENTO'
        ORDER BY v.pessoa_id, v.documento_id
    """, (analise_id,)):
        grupos.setdefault(pessoa_id, []).append(documento_id)
    return grupos
