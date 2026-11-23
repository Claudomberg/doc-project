"""Extrai dados expressos em contratos de trabalho, sem interpretar efeitos jurídicos."""
import re

from ceuma.documentos.declaracao_vinculo import (
    _CPF, _DATA, _MOEDAS, _QUALIFICADOR, _SALARIO, _alinhado, _grupos,
    _interpretar, _moeda, _registro, _resolver,
)


_TITULOS = {
    "CONTRATO INDIVIDUAL DE TRABALHO": None,
    "CONTRATO DE TRABALHO": None,
    "CONTRATO DE EXPERIENCIA": "EXPERIENCIA",
    "CONTRATO DE TRABALHO POR PRAZO DETERMINADO": "PRAZO DETERMINADO",
    "CONTRATO DE TRABALHO POR PRAZO INDETERMINADO": "PRAZO INDETERMINADO",
}
_OUTROS_TIPOS = {
    "CONTRATO_PRESTACAO_SERVICOS": {"CONTRATO DE PRESTACAO DE SERVICOS"},
    "CONTRATO_ESTAGIO": {"CONTRATO DE ESTAGIO", "TERMO DE COMPROMISSO DE ESTAGIO"},
    "TERMO_ADITIVO_TRABALHO": {"TERMO ADITIVO", "TERMO ADITIVO AO CONTRATO DE TRABALHO"},
    "TERMO_RESCISAO_TRABALHO": {"TERMO DE RESCISAO", "TERMO DE RESCISAO DO CONTRATO DE TRABALHO"},
    "PROPOSTA_EMPREGO": {"PROPOSTA DE EMPREGO", "PROPOSTA DE TRABALHO"},
}
_ROTULOS = {
    "NOME": r"NOME DO (?:EMPREGADO|TRABALHADOR)|EMPREGADO|TRABALHADOR",
    "CPF": r"CPF DO (?:EMPREGADO|TRABALHADOR)",
    "EMPREGADOR": r"EMPREGADOR|NOME DO EMPREGADOR|RAZAO SOCIAL",
    "CPF_EMPREGADOR": r"CPF DO EMPREGADOR",
    "CNPJ_EMPREGADOR": r"CNPJ DO EMPREGADOR|CNPJ",
    "CARGO": r"CARGO|FUNCAO|CARGO DO EMPREGADO|FUNCAO DO EMPREGADO",
    "ADMISSAO": r"ADMISSAO|DATA DE ADMISSAO",
    "DATA_INICIO_VIGENCIA": r"DATA DE INICIO (?:DA VIGENCIA|DO CONTRATO)|INICIO DA VIGENCIA",
    "DATA_TERMINO_PREVISTO": r"DATA PREVISTA DE TERMINO|TERMINO PREVISTO",
    "DESLIGAMENTO": r"DATA EFETIVA DE DESLIGAMENTO|DATA DO DESLIGAMENTO EFETIVO",
    "DATA_ELABORACAO": r"DATA DE ELABORACAO",
    "DATA_ASSINATURA": r"DATA DA ASSINATURA|DATA DE ASSINATURA|ASSINADO EM",
    "TIPO_CONTRATO": r"TIPO DE CONTRATO|MODALIDADE CONTRATUAL",
    "PRAZO_CONTRATUAL": r"PRAZO CONTRATUAL|PRAZO DO CONTRATO",
    "JORNADA_DECLARADA": r"JORNADA(?: DE TRABALHO)?",
    "JORNADA_DIARIA": r"JORNADA DIARIA",
    "JORNADA_SEMANAL": r"JORNADA SEMANAL",
    "JORNADA_MENSAL": r"JORNADA MENSAL",
    "LOCAL_TRABALHO": r"LOCAL DE TRABALHO|LOCAL DA PRESTACAO DO TRABALHO",
}
CAMPOS_CONTRATO_TRABALHO = (*_ROTULOS, *(
    f"{c}{s}" for c in _MOEDAS for s in ("", "_ORIGINAL", "_PERIODICIDADE")
))
_PARTES = re.compile(
    rf"^(EMPREGADO|TRABALHADOR|EMPREGADOR)\b\s*:?\s*([A-Z][A-Z0-9 .&'-]{{2,90}}),\s*"
    rf"(?:(?:INSCRIT[OA] NO) )?(CPF|CNPJ)\s*:?\s*({_CPF}|[A-Z0-9]+(?:[./-][A-Z0-9]+)*)\.?\s*$"
)
_TERCEIROS = re.compile(
    r"^(?:(?:DADOS (?:DO|DA|DAS) |NOME (?:DO|DA) |CPF (?:DO|DA) )?"
    r"(?:REPRESENTANTE|TESTEMUNHAS?|RESPONSAVEL|TERCEIRO)|ASSINATURAS?)\b"
)
_SECOES = {
    "DADOS DO EMPREGADO": "EMPREGADO",
    "DADOS DO TRABALHADOR": "EMPREGADO",
    "DADOS DO EMPREGADOR": "EMPREGADOR",
}
_CLAUSULA = re.compile(r"^CLAUSULA\b(?:\s+(?:[0-9IVX]+[A-Zºª.]*|PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA))?\s*(?:[-:.–]\s*)?(.*)$")
_TEMAS = {
    "REMUNERACAO": r"(?:DO |DA )?(?:SALARIO|REMUNERACAO)",
    "VIGENCIA": r"(?:DO |DA )?(?:VIGENCIA|PRAZO|DURACAO)",
    "JORNADA": r"(?:DA )?JORNADA(?: DE TRABALHO)?",
    "FUNCAO": r"(?:DA |DO )?(?:FUNCAO|CARGO|OBJETO)",
    "LOCAL": r"(?:DO )?LOCAL DE TRABALHO",
}
_FINANCEIRO_ALHEIO = re.compile(r"^(?:MULTA|INDENIZACAO|BENEFICIO|DESCONTO|RESCISAO|PENALIDADE)\b")
_REMUNERACAO = re.compile(
    rf"^(?:O EMPREGADO )?(?:RECEBERA|PERCEBERA) (?:SALARIO|REMUNERACAO)"
    rf"(?P<qual>(?: (?:{_QUALIFICADOR}))*) (?:DE )?(?P<valor>R\$.*)$"
)
_PRAZO = re.compile(r"[1-9][0-9]* (?:DIAS?|MESES|MES|ANOS?)")
_HORAS = r"[0-9]+(?:,[0-9]+)?\s*(?:HORAS?|H)"
_JORNADA = re.compile(rf"({_HORAS})(?:\s+(DIARIAS?|SEMANAIS?|MENSAIS?|POR DIA|POR SEMANA|POR MES))?\.?")


def _campo(texto):
    for campo, regra in _ROTULOS.items():
        if m := re.fullmatch(rf"(?:{regra})(?:\s*:\s*(.*))?", texto):
            return campo, m[1] or ""
    return None, ""


def avaliar_contratos_trabalho(linhas, *, contexto_ctps=False):
    titulos = set(linhas)
    cabecalhos = titulos & _TITULOS.keys()
    ctps = any(t in titulos for t in ("CTPS DIGITAL", "CTPS FISICA", "CARTEIRA DE TRABALHO DIGITAL",
                                      "CARTEIRA DE TRABALHO E PREVIDENCIA SOCIAL"))
    if ctps or contexto_ctps:
        cabecalhos.discard("CONTRATO DE TRABALHO")
    rotulos = {_campo(l)[0] for l in linhas}
    rotulos.update("EMPREGADOR" if m[1] == "EMPREGADOR" else "NOME"
                   for l in linhas if (m := _PARTES.fullmatch(l)))
    rotulos.update({"NOME" if l in _SECOES and _SECOES[l] == "EMPREGADO" else "EMPREGADOR"
                    for l in linhas if l in _SECOES})
    natureza = any(_CLAUSULA.fullmatch(t) for t in linhas) or bool(
        rotulos & {"CARGO", "DATA_INICIO_VIGENCIA", "ADMISSAO", "PRAZO_CONTRATUAL", "JORNADA_DECLARADA"}
    )
    return {
        "CONTRATO_TRABALHO_AVULSO": (
            bool(cabecalhos and {"NOME", "EMPREGADOR"} <= rotulos and natureza),
            ["TITULO DE CONTRATO"] if cabecalhos else [],
        ),
        **{tipo: (bool(titulos & titulos_proprios), [tipo] if titulos & titulos_proprios else [])
           for tipo, titulos_proprios in _OUTROS_TIPOS.items()},
    }


def _clausula(texto):
    m = _CLAUSULA.fullmatch(texto)
    if not m and (_campo(texto)[0] or _SALARIO.match(texto)) and ":" in texto:
        return None, None
    corpo = m[1].strip() if m else texto
    for tema, regra in _TEMAS.items():
        match = re.fullmatch(rf"(?:{regra})(?:\s*:\s*(.*))?", corpo)
        if match:
            valor = match[1] or ""
            if tema == "REMUNERACAO" and valor.startswith("R$"):
                valor = "REMUNERACAO: " + valor
            if tema == "JORNADA" and _JORNADA.fullmatch(valor):
                valor = "JORNADA: " + valor
            return tema, valor
    return (None, corpo) if m else (None, None)


def _interpretar_contrato(campo, valor, pagina, evidencia):
    if campo.startswith("JORNADA_") and valor:
        m = _JORNADA.fullmatch(valor)
        if not m:
            return _registro(campo, "EVIDENCIA_INSUFICIENTE", pagina, evidencia=evidencia)
        quantidade, periodo = m.groups()
        if campo == "JORNADA_DECLARADA" and periodo:
            if "DIARI" in periodo or periodo == "POR DIA":
                campo = "JORNADA_DIARIA"
            elif "SEMAN" in periodo:
                campo = "JORNADA_SEMANAL"
            else:
                campo = "JORNADA_MENSAL"
        if periodo:
            periodo_real = ("DIARIA" if "DIARI" in periodo or periodo == "POR DIA"
                            else "SEMANAL" if "SEMAN" in periodo else "MENSAL")
            if campo != "JORNADA_" + periodo_real:
                return _registro(campo, "AMBIGUO", pagina, evidencia=evidencia)
        return _registro(campo, "ENCONTRADO", pagina, valor.rstrip("."), evidencia)
    if campo == "PRAZO_CONTRATUAL" and valor and not _PRAZO.fullmatch(valor):
        return _registro(campo, "EVIDENCIA_INSUFICIENTE", pagina, evidencia=evidencia)
    return _interpretar(campo, valor, pagina, evidencia)


def _narrativo(tema, texto):
    if tema == "REMUNERACAO":
        if m := _REMUNERACAO.fullmatch(texto):
            return [("REMUNERACAO", (m["qual"], m["valor"]))]
    elif tema == "FUNCAO":
        if m := re.fullmatch(r"(?:O EMPREGADO )?(?:EXERCERA A FUNCAO DE|OCUPARA O CARGO DE) (.+?)\.?", texto):
            return [("CARGO", m[1])]
    elif tema == "VIGENCIA":
        if m := re.fullmatch(rf"O CONTRATO (?:VIGORA|VIGORARA|TEM VIGENCIA) DE ({_DATA}) A ({_DATA})\.?", texto):
            return [("DATA_INICIO_VIGENCIA", m[1]), ("DATA_TERMINO_PREVISTO", m[2])]
        if m := re.fullmatch(rf"O CONTRATO (?:TEM INICIO|INICIA(?:RA)?) EM ({_DATA})\.?", texto):
            return [("DATA_INICIO_VIGENCIA", m[1])]
    elif tema == "JORNADA":
        if m := re.fullmatch(r"(?:A )?JORNADA (?:SERA|E) DE (.+)", texto):
            return [("JORNADA_DECLARADA", m[1])]
    elif tema == "LOCAL":
        if m := re.fullmatch(r"(?:O )?TRABALHO SERA PRESTADO EM (.+?)\.?", texto):
            return [("LOCAL_TRABALHO", m[1])]
    return []


def extrair_contrato_trabalho(documento):
    candidatos = {campo: [] for campo in CAMPOS_CONTRATO_TRABALHO}
    cabecalhos = set()
    for pagina in documento["paginas"]:
        numero = pagina["numero"]
        for grupo, linhas in enumerate(_grupos(pagina)):
            contexto = None
            for i, (texto, caixa) in enumerate(linhas):
                if texto in _TITULOS:
                    cabecalhos.add((numero, grupo, i, texto))
                    contexto = None
                    if _TITULOS[texto]:
                        candidatos["TIPO_CONTRATO"].append(_registro(
                            "TIPO_CONTRATO", "ENCONTRADO", numero, _TITULOS[texto], "TITULO_EXPLICITO",
                        ))
                    continue
                if texto in _SECOES:
                    contexto = _SECOES[texto]
                    continue
                if _TERCEIROS.match(texto) or _FINANCEIRO_ALHEIO.match(texto):
                    contexto = "ASSINATURAS" if texto.startswith("ASSINATURA") else "OUTRO"
                    continue
                tema, corpo = _clausula(texto)
                if corpo is not None:
                    contexto = tema or "OUTRO"
                    texto = corpo
                if contexto == "OUTRO" or (
                    contexto == "ASSINATURAS" and _campo(texto)[0] != "DATA_ASSINATURA"
                ):
                    continue
                if m := _PARTES.fullmatch(texto):
                    papel, nome, documento_campo, valor = m.groups()
                    if papel != "EMPREGADOR" and documento_campo != "CPF":
                        continue
                    par = (("EMPREGADOR", documento_campo + "_EMPREGADOR") if papel == "EMPREGADOR"
                           else ("NOME", "CPF"))
                    for campo, v in zip(par, (nome, valor)):
                        candidatos[campo].append(_interpretar(campo, v, numero, "PARTE_EXPLICITA"))
                    continue
                campo, valor = _campo(texto)
                if contexto in {"EMPREGADO", "EMPREGADOR"}:
                    if m := re.fullmatch(r"(NOME|CPF)\s*:\s*(.*)", texto):
                        campo, valor = m.groups()
                        if contexto == "EMPREGADOR":
                            campo = "EMPREGADOR" if campo == "NOME" else "CPF_EMPREGADOR"
                    if contexto == "EMPREGADO" and campo in {"CPF_EMPREGADOR", "CNPJ_EMPREGADOR"}:
                        continue
                if campo == "CNPJ_EMPREGADOR" and re.match(r"^CNPJ(?:\s*:|$)", texto) and contexto != "EMPREGADOR":
                    continue
                if campo:
                    evid = "ROTULO_EXPLICITO" if valor or not caixa else "ROTULO_E_GEOMETRIA"
                    v = valor or _alinhado(caixa, linhas, outro_rotulo=lambda t: _campo(t)[0] or _CLAUSULA.match(t) or re.match(r"^(?:NOME|CPF)\s*:", t))
                    registro = _interpretar_contrato(campo, v, numero, evid)
                    candidatos[registro["campo"]].append(registro)
                elif m := _SALARIO.match(texto):
                    if contexto not in {None, "REMUNERACAO"}:
                        continue
                    for registro in _moeda(m["qual"], texto[m.end():], numero, "REMUNERACAO_CONTRATADA"):
                        candidatos[registro["campo"]].append(registro)
                else:
                    for campo, v in _narrativo(contexto, texto):
                        if campo == "REMUNERACAO":
                            for registro in _moeda(*v, numero, "REMUNERACAO_CONTRATADA"):
                                candidatos[registro["campo"]].append(registro)
                        else:
                            registro = _interpretar_contrato(campo, v, numero, "CLAUSULA_EXPLICITA")
                            candidatos[registro["campo"]].append(registro)

    campos = {c: _resolver(c, valores) for c, valores in candidatos.items()}
    partes = ("NOME", "CPF", "EMPREGADOR", "CPF_EMPREGADOR", "CNPJ_EMPREGADOR")
    if len(cabecalhos) > 1 or any(campos[c]["status"] == "AMBIGUO" for c in partes):
        for c, r in campos.items():
            if r["status"] != "NAO_ENCONTRADO":
                campos[c] = _registro(c, "AMBIGUO", r["pagina_origem"], evidencia="CONTRATO_INCONCLUSIVO")
    inicio, fim = campos["DATA_INICIO_VIGENCIA"], campos["DATA_TERMINO_PREVISTO"]
    if inicio["valor"] and fim["valor"] and (
        tuple(reversed(inicio["valor"].split("/"))) > tuple(reversed(fim["valor"].split("/")))
    ):
        for r in (inicio, fim):
            r.update(valor=None, status="INVALIDO", trecho_origem="CRONOLOGIA_INCONSISTENTE")
    for base in _MOEDAS:
        chaves = (base, base + "_ORIGINAL", base + "_PERIODICIDADE")
        if any(campos[c]["status"] == "AMBIGUO" for c in chaves):
            for c in chaves:
                campos[c] = _registro(c, "AMBIGUO", campos[c]["pagina_origem"], evidencia="REMUNERACAO_INCONCLUSIVA")
    return campos
