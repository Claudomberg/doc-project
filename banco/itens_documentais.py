from contextlib import contextmanager

from banco.conexao import consultar_modo_ativo, transacao_operacao


def _analise_da_origem(registro):
    if registro is None:
        raise ValueError("A origem do item documental está indisponível.")
    return registro[0]


def _analise_do_documento(conexao, documento_id):
    return _analise_da_origem(conexao.execute(
        "SELECT analise_id FROM documentos WHERE id = ?", (documento_id,),
    ).fetchone())


@contextmanager
def _editar_item(conexao, item_id):
    with transacao_operacao(conexao):
        analise_id = _analise_da_origem(conexao.execute(
            "SELECT analise_id FROM itens_documentais WHERE id = ?", (item_id,),
        ).fetchone())
        consultar_modo_ativo(conexao, analise_id)
        yield
        consultar_modo_ativo(conexao, analise_id)


def criar_item_documental(
    conexao, documento_id, tipo, ordem, *, pessoa_id=None, estado_revisao="PENDENTE", campos=(),
):
    """Cria uma ocorrência e, opcionalmente, grava seus campos na mesma transação."""
    with transacao_operacao(conexao):
        analise_id = _analise_do_documento(conexao, documento_id)
        consultar_modo_ativo(conexao, analise_id)
        cursor = conexao.execute("""
            INSERT INTO itens_documentais
                (analise_id, documento_id, tipo, ordem, pessoa_id, estado_revisao)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (analise_id, documento_id, tipo, ordem, pessoa_id, estado_revisao))
        item_id = cursor.lastrowid
        for campo in campos:
            _registrar_campo(conexao, item_id, **campo)
        consultar_modo_ativo(conexao, analise_id)
        return item_id


def _registrar_campo(
    conexao, item_id, nome_campo, valor=None, *, status="ENCONTRADO",
    pagina_origem=None, trecho_origem=None,
):
    conexao.execute("""
        INSERT INTO campos_item (item_id, nome_campo, valor, status, pagina_origem, trecho_origem)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (item_id, nome_campo) DO UPDATE SET
            valor = excluded.valor, status = excluded.status,
            pagina_origem = excluded.pagina_origem, trecho_origem = excluded.trecho_origem
    """, (item_id, nome_campo, valor, status, pagina_origem, trecho_origem))


def registrar_campos_item(conexao, item_id, campos):
    """Registra ou atualiza em lote os campos nomeados de uma única ocorrência."""
    with _editar_item(conexao, item_id):
        for campo in campos:
            _registrar_campo(conexao, item_id, **campo)


def atualizar_item_documental(conexao, item_id, *, tipo=None, ordem=None, estado_revisao=None):
    with _editar_item(conexao, item_id):
        conexao.execute("""
            UPDATE itens_documentais SET
                tipo = COALESCE(?, tipo), ordem = COALESCE(?, ordem),
                estado_revisao = COALESCE(?, estado_revisao)
            WHERE id = ?
        """, (tipo, ordem, estado_revisao, item_id))


def alterar_atribuicao_item(conexao, item_id, pessoa_id):
    """Atribui a um participante vinculado ao documento ou remove o vínculo com None."""
    with _editar_item(conexao, item_id):
        conexao.execute("""
            UPDATE itens_documentais SET pessoa_id = ?, estado_revisao = 'PENDENTE'
            WHERE id = ? AND pessoa_id IS NOT ?
        """, (pessoa_id, item_id, pessoa_id))


def remover_item_documental(conexao, item_id):
    with _editar_item(conexao, item_id):
        conexao.execute("DELETE FROM itens_documentais WHERE id = ?", (item_id,))


def _projetar_itens(conexao, analise_id, *, documento_id=None, pessoa_id=None):
    # Os filtros opcionais são compostos com cláusulas fixas e valores parametrizados.
    condicoes = ["i.analise_id = ?"]
    parametros = [analise_id]
    if documento_id is not None:
        condicoes.append("i.documento_id = ?")
        parametros.append(documento_id)
    if pessoa_id is not None:
        condicoes.append("i.pessoa_id = ?")
        parametros.append(pessoa_id)

    itens = {}
    for linha in conexao.execute(f"""
        SELECT i.id, i.documento_id, i.tipo, i.ordem, i.pessoa_id, i.estado_revisao,
               c.nome_campo, c.valor, c.status, c.pagina_origem, c.trecho_origem
        FROM itens_documentais i LEFT JOIN campos_item c ON c.item_id = i.id
        WHERE {' AND '.join(condicoes)}
        ORDER BY i.documento_id, i.ordem, c.nome_campo
    """, parametros):
        ident, documento, tipo, ordem, pessoa, revisao, campo, valor, status, pagina, trecho = linha
        if ident not in itens:
            itens[ident] = {
                "item_id": ident, "documento_id": documento, "tipo": tipo, "ordem": ordem,
                "pessoa_id": pessoa, "estado_revisao": revisao, "campos": [],
            }
        if campo is not None:
            itens[ident]["campos"].append({
                "nome_campo": campo, "valor": valor, "status": status,
                "pagina_origem": pagina, "trecho_origem": trecho,
            })
    return list(itens.values())


def projetar_itens_analise(conexao, analise_id):
    """Lê os itens no snapshot da análise, já validado pelo chamador."""
    return _projetar_itens(conexao, analise_id)


def listar_itens_por_documento(conexao, documento_id):
    analise_id = _analise_do_documento(conexao, documento_id)
    consultar_modo_ativo(conexao, analise_id)
    itens = _projetar_itens(conexao, analise_id, documento_id=documento_id)
    consultar_modo_ativo(conexao, analise_id)
    return itens


def listar_itens_por_pessoa(conexao, analise_id, pessoa_id):
    consultar_modo_ativo(conexao, analise_id)
    if conexao.execute(
        "SELECT 1 FROM pessoas WHERE analise_id = ? AND id = ?", (analise_id, pessoa_id),
    ).fetchone() is None:
        raise ValueError("Pessoa indisponível nesta análise.")
    itens = _projetar_itens(conexao, analise_id, pessoa_id=pessoa_id)
    consultar_modo_ativo(conexao, analise_id)
    return itens
