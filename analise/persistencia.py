
from analise.extrator_campos import STATUS_CPF_TITULAR
from banco.conexao import transacao_operacao
from banco.itens_documentais import criar_item_documental
from banco.operacoes import (
    adicionar_documento,
    salvar_campo_extraido
)


def _registros_perfil(campos, *, preservar_evidencia=False):
    for campo, item in campos.items():
        situacao = item["status"]
        if situacao == "NAO_ENCONTRADO" or (situacao == "NAO_APLICAVEL" and item["pagina_origem"] is None):
            continue
        yield {
            "campo": campo,
            "valor": item["valor"] if situacao == "ENCONTRADO" else None,
            "pagina": item["pagina_origem"],
            "trecho": item["trecho_origem"] if preservar_evidencia else None,
            "status": STATUS_CPF_TITULAR if campo == "CPF" and situacao == "ENCONTRADO" else situacao,
        }


def _registros_genericos(campos):
    registros = []

    nome = campos["nome"]

    if nome["status"] == "ENCONTRADO":
        registros.append({
            "campo": "NOME",
            "valor": nome["valor"],
            "pagina": nome["pagina_origem"],
            "trecho": nome["trecho_origem"],
            "status": nome["status"]
        })

    for cpf in campos["cpfs"]:
        papel = cpf.get("papel", "AMBIGUO")
        registros.append({
            "campo": "CPF",
            "valor": cpf["valor"],
            "pagina": cpf["pagina_origem"],
            "trecho": None,
            "status": (
                (STATUS_CPF_TITULAR if papel == "TITULAR" else papel)
                if cpf["valido"] else "INVALIDO"
            ),
        })

    rg = campos["rg"]

    if rg["status"] in ("ENCONTRADO", "AMBIGUO"):
        registros.append({
            "campo": "RG",
            "valor": rg["valor"],
            "pagina": rg["pagina_origem"],
            "trecho": rg["trecho_origem"],
            "status": rg["status"]
        })

    for data in campos["datas"]:
        registros.append({
            "campo": data["campo"],
            "valor": data["valor"],
            "pagina": data["pagina_origem"],
            "trecho": data["trecho_origem"],
            "status": (
                "ENCONTRADO"
                if data["valida"]
                else "INVALIDO"
            )
        })
    return registros


def salvar_resultado_analise(
    conexao,
    analise_id,
    resultado,
    confirmar=True
):

    if ("ctps_fisica" in resultado or "ctps_digital" in resultado
            or any(chave in resultado["campos"] for chave in ("solicitacao_ctps", "declaracao_vinculo", "contrato_trabalho", "casamento"))):
        with transacao_operacao(conexao, confirmar=confirmar):
            return _salvar_documento(conexao, analise_id, resultado, confirmar=False)
    return _salvar_documento(conexao, analise_id, resultado, confirmar)


def _salvar_documento(conexao, analise_id, resultado, confirmar):
    identificacao = resultado["identificacao"]
    campos = resultado["campos"]

    documento_id = adicionar_documento(
        conexao=conexao,
        analise_id=analise_id,
        nome_arquivo=resultado["arquivo"],
        caminho_origem=resultado.get("caminho_origem"),
        tipo_identificado=identificacao["tipo"],
        quantidade_paginas=resultado["quantidade_paginas"],
        status_leitura="REVISAO_NECESSARIA" if resultado.get("pendencias_leitura") else "CONCLUIDO",
        confirmar=confirmar
    )

    especificos = next((campos[chave] for chave in (
        "identidade", "cnh", "certidao_nascimento", "ctps_fisica", "ctps_digital", "solicitacao_ctps", "declaracao_vinculo",
        "contrato_trabalho", "casamento",
    ) if chave in campos), None)
    evidencia_categorica = any(chave in campos for chave in ("solicitacao_ctps", "declaracao_vinculo", "contrato_trabalho", "casamento"))
    registros = (list(_registros_perfil(especificos, preservar_evidencia=evidencia_categorica))
                 if especificos is not None else _registros_genericos(campos))

    for registro in registros:
        salvar_campo_extraido(
            conexao=conexao,
            documento_id=documento_id,
            nome_campo=registro["campo"],
            valor=registro["valor"],
            pagina_origem=registro["pagina"],
            trecho_origem=registro["trecho"],
            confianca=None,
            status=registro["status"],
            confirmar=confirmar
        )

    total_campos = len(registros)
    ctps = resultado.get("ctps_fisica")
    if ctps is not None:
        for campo, valor in (
            ("SEQUENCIA_CTPS", ctps["sequencia"]),
            ("PAGINA_SEGUINTE_CTPS", ctps["pagina_seguinte"]),
            ("ULTIMO_CONTRATO_APRESENTADO", ctps["ultimo_entre_apresentados"]),
        ):
            if valor is not None:
                salvar_campo_extraido(conexao, documento_id, campo, str(valor), confirmar=False)
                total_campos += 1

    fonte = ctps or resultado.get("ctps_digital")
    for ordem, contrato in enumerate(fonte["contratos"] if fonte else (), 1):
        campos_item = [
            {"nome_campo": item["campo"], **{k: item[k] for k in (
                "valor", "status", "pagina_origem", "trecho_origem",
            )}}
            for item in contrato["campos"].values()
        ]
        if contrato["continuacao_inconclusiva"]:
            campos_item.append({
                "nome_campo": "CONTINUIDADE", "status": "EVIDENCIA_INSUFICIENTE",
                "pagina_origem": contrato["paginas"][0],
            })
        criar_item_documental(
            conexao, documento_id, "VINCULO_TRABALHISTA", ordem, campos=campos_item,
        )

    return {
        "documento_id": documento_id,
        "campos_salvos": total_campos
    }
