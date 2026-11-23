
from ceuma.documentos.identificador import identificar_documento

from ceuma.documentos.certidao_casamento import extrair_certidao_casamento
from ceuma.documentos.certidao_nascimento import extrair_certidao_nascimento
from ceuma.documentos.cnh import extrair_cnh
from ceuma.documentos.solicitacao_ctps import extrair_comprovante_solicitacao_ctps
from ceuma.documentos.contrato_trabalho import extrair_contrato_trabalho
from ceuma.documentos.ctps_digital import extrair_ctps_digital
from ceuma.documentos.ctps_fisica import extrair_ctps_fisica
from ceuma.documentos.declaracao_vinculo import extrair_declaracao_vinculo_trabalhista
from ceuma.extracao.campos import (
    extrair_nome_documento,
    extrair_cpfs_documento,
    extrair_rg_documento,
    extrair_datas_classificadas_documento,
    validar_cpf,
)
from ceuma.documentos.identidade import extrair_identidade


def analisar_documento(documento):
    """
    Reúne a identificação e os campos extraídos
    em um único resultado estruturado.
    """

    texto_completo = "\n".join(
        pagina["texto"]
        for pagina in documento["paginas"]
    )

    identificacao = identificar_documento(texto_completo, exigir_evidencia_casamento=True)

    tipo = identificacao["tipo"]
    ctps = None
    ctps_digital = None
    if tipo == "CERTIDAO_CASAMENTO":
        campos = {
            "nome": {"status": "NAO_APLICAVEL"}, "cpfs": [],
            "rg": {"status": "NAO_APLICAVEL"}, "datas": [],
            "casamento": extrair_certidao_casamento(documento),
        }
    elif tipo in {"DOCUMENTO_IDENTIDADE", "CIN", "CNH", "CERTIDAO_NASCIMENTO", "CTPS_FISICA", "CTPS_DIGITAL", "SOLICITACAO_CTPS", "DECLARACAO_VINCULO", "CONTRATO_TRABALHO_AVULSO"}:
        if tipo == "CTPS_FISICA":
            ctps = extrair_ctps_fisica(documento)
            especificos = ctps["pessoais"]
            chave = "ctps_fisica"
        elif tipo == "CTPS_DIGITAL":
            ctps_digital = extrair_ctps_digital(documento)
            especificos = ctps_digital["pessoais"]
            chave = "ctps_digital"
        elif tipo == "SOLICITACAO_CTPS":
            especificos = extrair_comprovante_solicitacao_ctps(documento)
            chave = "solicitacao_ctps"
        elif tipo == "DECLARACAO_VINCULO":
            especificos = extrair_declaracao_vinculo_trabalhista(documento)
            chave = "declaracao_vinculo"
        elif tipo == "CONTRATO_TRABALHO_AVULSO":
            especificos = extrair_contrato_trabalho(documento)
            chave = "contrato_trabalho"
        elif tipo == "CNH":
            especificos = extrair_cnh(documento)
            chave = "cnh"
        elif tipo == "CERTIDAO_NASCIMENTO":
            especificos = extrair_certidao_nascimento(documento)
            chave = "certidao_nascimento"
        else:
            rg_compartilhado = extrair_rg_documento(documento) if tipo == "DOCUMENTO_IDENTIDADE" else None
            especificos = extrair_identidade(documento, tipo, rg_compartilhado)
            chave = "identidade"
        cpf = especificos["CPF"]
        campos = dict(
            nome=especificos["NOME"],
            rg=especificos.get("RG_ORIGEM" if tipo == "CNH" else "RG", {
                "campo": "RG", "valor": None, "status": "NAO_APLICAVEL",
                "pagina_origem": None, "metodo_leitura": None, "trecho_origem": None,
            }),
            cpfs=[
                {**cpf, "valido": validar_cpf(cpf["valor"]),
                 "papel": "TITULAR" if cpf["status"] == "ENCONTRADO" else "AMBIGUO"},
            ] if cpf["valor"] is not None else [],
            datas=[
                {**item, "valida": item["status"] == "ENCONTRADO"}
                for item in especificos.values()
                if item["campo"].startswith("DATA_") and item["valor"] is not None
            ],
        )
        campos[chave] = especificos
    elif identificacao.get("revisao_manual") or tipo in {
        "AGENDAMENTO_CTPS",
        "EMISSAO_CTPS", "PROTOCOLO_ATENDIMENTO_CTPS",
    }:
        campos = {
            "nome": {"status": "NAO_ENCONTRADO"},
            "cpfs": [], "rg": {"status": "NAO_ENCONTRADO"}, "datas": [],
        }
        identificacao["revisao_manual"] = True
    else:
        campos = {
            "nome": extrair_nome_documento(documento),
            "cpfs": extrair_cpfs_documento(documento),
            "rg": extrair_rg_documento(documento),
            "datas": extrair_datas_classificadas_documento(documento),
        }

    paginas_ocr = [p["diagnostico_ocr"] for p in documento["paginas"]
                   if "diagnostico_ocr" in p]
    pendencias = {p for pagina in paginas_ocr for p in pagina["pendencias"]}
    if not texto_completo.strip():
        pendencias.add("SEM_TEXTO_UTIL")
    if tipo == "CERTIDAO_CASAMENTO":
        dados_casamento = campos["casamento"]
        if any(r["status"] in {"AMBIGUO", "INVALIDO", "EVIDENCIA_INSUFICIENTE"}
               for r in dados_casamento.values()) or any(
            dados_casamento[f"NOME_CONJUGE_{n}"]["status"] != "ENCONTRADO" for n in (1, 2)
        ):
            pendencias.add("DADOS_CASAMENTO_REQUEREM_REVISAO")
        if dados_casamento["AVERBACAO"]["valor"] == "PRESENTE":
            pendencias.add("AVERBACAO_REQUER_CONFERENCIA")
    if ctps_digital is not None:
        pendencias.update(ctps_digital["pendencias"])
    if tipo == "SOLICITACAO_CTPS" and any(
        valor["status"] in {"AMBIGUO", "INVALIDO", "EVIDENCIA_INSUFICIENTE"}
        for valor in campos["solicitacao_ctps"].values()
    ):
        pendencias.add("SOLICITACAO_CTPS_REQUER_REVISAO")
    if tipo == "DECLARACAO_VINCULO" and any(
        registro["status"] in {"AMBIGUO", "INVALIDO", "EVIDENCIA_INSUFICIENTE"}
        for registro in campos["declaracao_vinculo"].values()
    ):
        pendencias.add("DECLARACAO_VINCULO_REQUER_REVISAO")
    if tipo == "CONTRATO_TRABALHO_AVULSO" and any(
        registro["status"] in {"AMBIGUO", "INVALIDO", "EVIDENCIA_INSUFICIENTE"}
        for registro in campos["contrato_trabalho"].values()
    ):
        pendencias.add("CONTRATO_TRABALHO_REQUER_REVISAO")
    if identificacao.get("revisao_manual") and identificacao["status"] != "IDENTIFICADO":
        pendencias.add("CLASSIFICACAO_INCONCLUSIVA")
    if tipo in {"AGENDAMENTO_CTPS", "EMISSAO_CTPS", "PROTOCOLO_ATENDIMENTO_CTPS"}:
        pendencias.add("DOCUMENTO_SEM_EXTRATOR")
    if any(p["metricas"] for p in paginas_ocr):
        if identificacao.get("status") != "IDENTIFICADO":
            pendencias.add("CLASSIFICACAO_INCONCLUSIVA")
        if tipo == "CIN" and campos["identidade"]["CPF"]["status"] != "ENCONTRADO":
            pendencias.add("CPF_CIN_NAO_CONFIRMADO")
        if tipo != "CERTIDAO_CASAMENTO" and campos["nome"]["status"] != "ENCONTRADO":
            pendencias.add("NOME_NAO_CONFIRMADO")
        if ctps is not None and any(
            contrato["campos"][campo]["status"] != "ENCONTRADO"
            for contrato in ctps["contratos"] for campo in ("EMPREGADOR", "ADMISSAO")
        ):
            pendencias.add("ASSOCIACAO_CONTRATO_INCONCLUSIVA")
        for chave in ("identidade", "cnh", "certidao_nascimento", "ctps_fisica", "ctps_digital"):
            if any(c["status"] in {"AMBIGUO", "INVALIDO", "EVIDENCIA_INSUFICIENTE"}
                   for c in campos.get(chave, {}).values()):
                pendencias.add("CAMPO_REQUER_REVISAO")

    return {
        "arquivo": documento["nome_arquivo"],
        "caminho_origem": documento.get("caminho_origem"),
        "quantidade_paginas": documento["quantidade_paginas"],
        "identificacao": identificacao,
        "campos": campos,
        **({"pendencias_leitura": sorted(pendencias)} if pendencias else {}),
        **({"ctps_fisica": ctps} if ctps is not None else {}),
        **({"ctps_digital": ctps_digital} if ctps_digital is not None else {}),
    }
