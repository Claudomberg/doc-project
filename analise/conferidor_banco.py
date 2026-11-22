
from itertools import combinations

from analise.conferidor import comparar_campo, normalizar_valor
from analise.extrator_campos import STATUS_CPF_TITULAR, validar_cpf
from banco.atribuicoes import agrupar_documentos_elegiveis
from banco.conexao import consultar_modo_ativo, transacao_operacao
from banco.operacoes import registrar_conferencia


CAMPOS_COMPARAVEIS = ("NOME", "CPF", "DATA_NASCIMENTO")


def conferir_analise(
    conexao,
    analise_id,
    confirmar=True
):
    """Atualiza as conferências automáticas em uma única operação."""
    with transacao_operacao(conexao, confirmar=confirmar):
        modo = consultar_modo_ativo(conexao, analise_id)
        # A rotina anterior remove apenas observações que começam com AUTO:.
        prefixo = "AUTO:" if modo == "INDIVIDUAL" else "AUTO_FAMILIAR:"
        conexao.execute(
            """DELETE FROM conferencias
               WHERE analise_id = ? AND instr(observacao, ?) = 1""",
            (analise_id, prefixo),
        )
        resultados = _conferir_pares(conexao, analise_id, modo, prefixo)
        consultar_modo_ativo(conexao, analise_id)
        return resultados


def _conferir_pares(conexao, analise_id, modo, prefixo):
    grupos = agrupar_documentos_elegiveis(conexao, analise_id, modo)
    elegiveis = {documento_id for ids in grupos.values() for documento_id in ids if len(ids) > 1}
    if not elegiveis:
        return []

    registros = conexao.execute(
        """
        SELECT
            d.id,
            d.nome_arquivo,
            c.nome_campo,
            c.valor,
            c.status
        FROM documentos d
        LEFT JOIN campos_extraidos c
            ON c.documento_id = d.id
            AND c.nome_campo IN (?, ?, ?)
            AND (c.nome_campo = 'CPF' OR c.status = 'ENCONTRADO')
        WHERE d.analise_id = ?
        ORDER BY d.id, c.id
        """,
        (*CAMPOS_COMPARAVEIS, analise_id)
    )

    documentos = {}
    for documento_id, arquivo, campo, valor, status in registros:
        if documento_id not in elegiveis:
            continue
        dados = documentos.setdefault(documento_id, {"arquivo": arquivo, "campos": {}})
        if campo is None:
            continue
        valores = dados["campos"].setdefault(campo, set())
        if campo == "CPF":
            # Registros antigos marcados apenas como ENCONTRADO não provam titularidade.
            if status != STATUS_CPF_TITULAR or not validar_cpf(valor):
                continue
            valor = "".join(digito for digito in valor if digito.isdecimal())
        if normalizado := normalizar_valor(valor):
            valores.add(normalizado)

    resultados = []
    pares = (
        (pessoa_id, primeiro_id, segundo_id)
        for pessoa_id, ids in grupos.items()
        for primeiro_id, segundo_id in combinations(ids, 2)
    )
    for pessoa_id, primeiro_id, segundo_id in pares:
        primeiro, segundo = documentos[primeiro_id], documentos[segundo_id]
        for campo in CAMPOS_COMPARAVEIS:
            valores_1 = primeiro["campos"].get(campo)
            valores_2 = segundo["campos"].get(campo)
            if modo == "INDIVIDUAL":
                if valores_1 is None and valores_2 is None:
                    continue
                if campo != "CPF" and (valores_1 is None or valores_2 is None):
                    continue

            observacao = f"{prefixo} Documento {primeiro_id} x documento {segundo_id}"
            if len(valores_1 or ()) == len(valores_2 or ()) == 1:
                resultado = comparar_campo(campo, next(iter(valores_1)),
                                           next(iter(valores_2)))["resultado"]
            else:
                resultado = "NAO_CONFERIDO"
                motivo = ("CPF do titular sem confirmação inequívoca" if campo == "CPF"
                          else "valores ausentes ou conflitantes")
                observacao += f" | Revisão manual: {motivo}."

            registrar_conferencia(
                conexao=conexao,
                analise_id=analise_id,
                campo_verificado=campo,
                resultado=resultado,
                observacao=observacao,
                confirmar=False,
                pessoa_id=pessoa_id,
                documento_1_id=primeiro_id,
                documento_2_id=segundo_id,
            )

            resultados.append({
                "campo": campo,
                "documento_1": primeiro["arquivo"],
                "documento_2": segundo["arquivo"],
                "resultado": resultado,
                "pessoa_id": pessoa_id,
                "documento_1_id": primeiro_id,
                "documento_2_id": segundo_id,
            })

    return resultados
