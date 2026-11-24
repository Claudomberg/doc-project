from collections import defaultdict

from ceuma.documentos.certidao_casamento import CAMPOS_CASAMENTO
from ceuma.documentos.certidao_nascimento import CAMPOS_CERTIDAO
from ceuma.documentos.cnh import CAMPOS_CNH
from ceuma.documentos.solicitacao_ctps import CAMPOS_SOLICITACAO_CTPS
from ceuma.documentos.contrato_trabalho import CAMPOS_CONTRATO_TRABALHO
from ceuma.documentos.ctps_digital import CAMPOS_CTPS_DIGITAL
from ceuma.documentos.ctps_fisica import CAMPOS_CTPS
from ceuma.documentos.declaracao_vinculo import CAMPOS_DECLARACAO_VINCULO
from ceuma.conferencia.automatica import CAMPOS_COMPARAVEIS
from ceuma.extracao.campos import STATUS_CPF_TITULAR
from ceuma.documentos.identidade import CAMPOS_CIN, CAMPOS_IDENTIDADE
from ceuma.banco import conexao as banco
from ceuma.banco.itens_documentais import projetar_itens_analise
from ceuma.banco.atribuicoes import agrupar_documentos_elegiveis, listar_documentos_sem_atribuicao


class ErroApresentacao(RuntimeError):
    """Falha ao montar ou apresentar o relatório de uma análise."""

    def __init__(self, mensagem, *, codigo=None):
        super().__init__(mensagem)
        self.codigo = codigo


class RelatorioIndisponivel(ValueError):
    """Nenhuma análise concluída e ativa corresponde à solicitação."""


def recuperar_relatorio(conexao, codigo=None, *, analise_id=None):
    """Lê uma análise concluída e ativa sem reprocessar os documentos."""
    if conexao.in_transaction:
        raise ErroApresentacao("Use uma conexão sem transação em andamento.")
    indisponivel = "Nenhuma análise concluída e ativa corresponde à solicitação."
    codigo_local = None
    try:
        with conexao:
            conexao.execute("BEGIN")
            registro = conexao.execute("""
                SELECT id, codigo
                FROM analises
                WHERE status = 'CONCLUIDA'
                  AND (? IS NULL OR codigo = ?)
                  AND (? IS NULL OR id = ?)
                  AND julianday(data_expiracao) > julianday(?)
                ORDER BY id DESC
                LIMIT 1
            """, (codigo, codigo, analise_id, analise_id,
                  banco.agora_utc().isoformat(timespec="seconds"))).fetchone()
            if registro is None:
                raise RelatorioIndisponivel(indisponivel)
            ident, codigo_local = registro
            if banco.consultar_expiracao_ativa(conexao, codigo_local) is None:
                raise RelatorioIndisponivel(indisponivel)
            relatorio = gerar_relatorio(conexao, ident)

        # Revalida depois da leitura, inclusive se o relatório demorar até o vencimento.
        expiracao = banco.consultar_expiracao_ativa(conexao, codigo_local)
        if expiracao is None:
            raise RelatorioIndisponivel(indisponivel)
        return relatorio, expiracao
    except RelatorioIndisponivel:
        raise
    except Exception:
        raise ErroApresentacao(
            "Não foi possível montar o relatório da análise.", codigo=codigo_local,
        ) from None


_CAMPOS_POR_TIPO = {
    "DOCUMENTO_IDENTIDADE": CAMPOS_IDENTIDADE, "CIN": CAMPOS_CIN, "CNH": CAMPOS_CNH,
    "CERTIDAO_NASCIMENTO": CAMPOS_CERTIDAO, "CTPS_FISICA": CAMPOS_CTPS,
    "CTPS_DIGITAL": CAMPOS_CTPS_DIGITAL,
    "SOLICITACAO_CTPS": CAMPOS_SOLICITACAO_CTPS,
    "DECLARACAO_VINCULO": CAMPOS_DECLARACAO_VINCULO,
    "CONTRATO_TRABALHO_AVULSO": CAMPOS_CONTRATO_TRABALHO,
    "CERTIDAO_CASAMENTO": CAMPOS_CASAMENTO,
}


def _estado_recuperado(campo, registros):
    if not registros:
        return "NAO_ENCONTRADO"
    identificados = {
        valor for valor, situacao in registros if valor is not None
        and situacao == (STATUS_CPF_TITULAR if campo == "CPF" else "ENCONTRADO")
    }
    situacoes = {situacao for _, situacao in registros}
    if len(identificados) > 1 or "AMBIGUO" in situacoes:
        return "AMBIGUO"
    if identificados:
        return "ENCONTRADO"
    for situacao in ("INVALIDO", "EVIDENCIA_INSUFICIENTE", "NAO_APLICAVEL", "NAO_ENCONTRADO"):
        if situacao in situacoes:
            return situacao
    return "EVIDENCIA_INSUFICIENTE"


def _estados_por_documento(documentos, campos):
    evidencias = defaultdict(list)
    for ident, _, campo, valor, _, situacao, _ in campos:
        evidencias[ident, campo].append((valor, situacao))
    return [
        {
            "documento_id": ident, "campo": campo,
            "status": ("NAO_APLICAVEL" if tipo == "CIN" and campo == "RG"
                       else _estado_recuperado(campo, evidencias.get((ident, campo)))),
        }
        for ident, _, tipo in documentos for campo in _CAMPOS_POR_TIPO.get(tipo, ())
    ]


def _projetar_conferencia(conexao, analise_id, modo, conferencias):
    vinculos = defaultdict(list)
    for pessoa, documento in conexao.execute("""
        SELECT pessoa_id, documento_id FROM documentos_pessoas
        WHERE analise_id = ? ORDER BY documento_id
    """, (analise_id,)):
        vinculos[pessoa].append(documento)
    pessoas = [
        {"pessoa_id": ident, "papel": papel, "documentos_ids": vinculos[ident]}
        for ident, papel in conexao.execute(
            "SELECT id, papel FROM pessoas WHERE analise_id = ? ORDER BY id", (analise_id,),
        )
    ]
    compartilhados = [
        row[0] for row in conexao.execute("""
            SELECT id FROM documentos WHERE analise_id = ?
                AND escopo = 'COMPARTILHADO' ORDER BY id
        """, (analise_id,))
    ]
    inconclusivos = (
        [row[0] for row in listar_documentos_sem_atribuicao(conexao, analise_id)]
        if modo == "FAMILIAR" else []
    )
    estruturadas = [
        {
            "conferencia_id": ident, "pessoa_id": pessoa, "documento_1_id": primeiro,
            "documento_2_id": segundo, "campo": campo, "resultado": resultado,
        }
        for ident, campo, resultado, _, pessoa, primeiro, segundo in conferencias
        if primeiro is not None
    ]
    total_pares = sum(
        len(ids) * (len(ids) - 1) // 2
        for ids in agrupar_documentos_elegiveis(conexao, analise_id, modo).values()
    )
    confirmadas = sum(item["resultado"] in {"COMPATIVEL", "DIVERGENTE"} for item in estruturadas)
    historicas = sum(
        primeiro is None and (observacao or "").startswith("AUTO:")
        for _, _, _, observacao, _, primeiro, _ in conferencias
    )
    parcial = bool(total_pares and confirmadas < total_pares * len(CAMPOS_COMPARAVEIS))
    if any(item["resultado"] == "DIVERGENTE" for item in estruturadas):
        estado = "COM_DIVERGENCIAS"
    elif historicas:
        estado = "HISTORICO_SEM_ATRIBUICAO"
    elif not total_pares:
        estado = "AGUARDANDO_ATRIBUICAO" if inconclusivos else "SEM_PARES_ELEGIVEIS"
    elif parcial or inconclusivos:
        estado = "PARCIALMENTE_CONFERIDO"
    else:
        estado = "SEM_DIVERGENCIAS"
    return {
        "modo": modo, "pessoas": pessoas, "documentos_compartilhados": compartilhados,
        "documentos_inconclusivos": inconclusivos, "conferencias_estruturadas": estruturadas,
        "estado_conferencia": estado, "total_pares_elegiveis": total_pares,
        "conferencia_parcial": parcial, "aguardando_atribuicao": bool(inconclusivos),
        "total_conferencias_historicas": historicas,
    }


def gerar_relatorio(conexao, analise_id):
    """Consulta todo o relatório no mesmo snapshot SQLite."""
    if conexao.in_transaction:
        return _gerar_relatorio(conexao, analise_id)
    with conexao:
        conexao.execute("BEGIN")
        relatorio = _gerar_relatorio(conexao, analise_id)
    if banco.consultar_expiracao_ativa(conexao, relatorio["codigo"]) is None:
        raise RelatorioIndisponivel("A análise expirou ou não está mais disponível.")
    return relatorio


def _gerar_relatorio(conexao, analise_id):
    analise = conexao.execute(
        """
        SELECT codigo, data_criacao, status, modo
        FROM analises
        WHERE id = ?
        """,
        (analise_id,)
    ).fetchone()

    if analise is None:
        raise ValueError("Análise não encontrada.")
    if banco.consultar_expiracao_ativa(conexao, analise[0]) is None:
        raise RelatorioIndisponivel("A análise expirou ou não está mais disponível.")

    documentos = conexao.execute(
        """
        SELECT id, nome_arquivo, tipo_identificado
        FROM documentos
        WHERE analise_id = ?
        ORDER BY id
        """,
        (analise_id,)
    ).fetchall()

    conferencias = conexao.execute(
        """
        SELECT id, campo_verificado, resultado, observacao,
               pessoa_id, documento_1_id, documento_2_id
        FROM conferencias
        WHERE analise_id = ?
        ORDER BY id
        """,
        (analise_id,)
    ).fetchall()

    resultados = [
        {
            "campo": campo,
            "resultado": resultado,
            "observacao": observacao
        }
        for _, campo, resultado, observacao, _, _, _ in conferencias
    ]

    divergencias = sum(
        1 for item in resultados
        if item["resultado"] == "DIVERGENTE"
    )


    campos = conexao.execute(
        """
        SELECT
            d.id,
            d.nome_arquivo,
            c.nome_campo,
            c.valor,
            c.pagina_origem,
            c.status,
            c.trecho_origem
        FROM documentos d
        JOIN campos_extraidos c
            ON c.documento_id = d.id
        WHERE d.analise_id = ?
        ORDER BY d.id, c.id
        """,
        (analise_id,)
    ).fetchall()

    campos_extraidos = [
        {
            "documento_id": documento_id,
            "arquivo": arquivo,
            "campo": campo,
            "valor": valor,
            "pagina": pagina,
            "status": status,
            "trecho_origem": trecho
        }
        for documento_id, arquivo, campo, valor, pagina, status, trecho in campos
        if status not in ("NAO_ENCONTRADO", "NAO_APLICAVEL")
    ]


    relatorio = {
        "codigo": analise[0],
        "data_criacao": analise[1],
        "status": analise[2],
        "documentos": [
            {
                "id": documento_id,
                "arquivo": nome,
                "tipo": tipo
            }
            for documento_id, nome, tipo in documentos
        ],
        "campos_extraidos": campos_extraidos,
        "estados_campos": _estados_por_documento(documentos, campos),
        "conferencias": resultados,
        "total_documentos": len(documentos),
        "total_conferencias": len(resultados),
        "total_divergencias": divergencias
    }
    pendentes = [row[0] for row in conexao.execute("""
        SELECT id FROM documentos WHERE analise_id = ? AND status_leitura = 'REVISAO_NECESSARIA'
        ORDER BY id
    """, (analise_id,))]
    if pendentes:
        relatorio["leituras_pendentes"] = pendentes
    relatorio["itens_documentais"] = projetar_itens_analise(conexao, analise_id)
    relatorio.update(_projetar_conferencia(conexao, analise_id, analise[3], conferencias))
    if banco.consultar_expiracao_ativa(conexao, analise[0]) is None:
        raise RelatorioIndisponivel("A análise expirou ou não está mais disponível.")
    return relatorio
