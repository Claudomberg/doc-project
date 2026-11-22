"""Extrai informações declaradas em vínculos trabalhistas com papéis explícitos."""
import re
from decimal import Decimal

from analise.associador_espacial import _caixa
from analise.extrator_campos import _partes_leitura, normalizar_rotulo, validar_cpf, validar_data
from analise.identificador_empregador import interpretar_cnpj


_ROTULOS = {
    "NOME": r"NOME DO (?:TRABALHADOR|EMPREGADO|FUNCIONARIO)|TRABALHADOR|EMPREGADO|FUNCIONARIO",
    "CPF": r"CPF DO (?:TRABALHADOR|EMPREGADO|FUNCIONARIO)",
    "EMPREGADOR": r"EMPREGADOR|NOME DO EMPREGADOR|RAZAO SOCIAL",
    "CNPJ_EMPREGADOR": r"CNPJ(?: DO EMPREGADOR)?",
    "CPF_EMPREGADOR": r"CPF DO EMPREGADOR",
    "CARGO": r"CARGO|FUNCAO|CARGO DO TRABALHADOR|FUNCAO DO TRABALHADOR",
    "ADMISSAO": r"(?:DATA DE )?ADMISSAO|DATA DE INICIO DO VINCULO|INICIO DO VINCULO",
    "DESLIGAMENTO": r"(?:DATA DE )?DESLIGAMENTO|DATA DE ENCERRAMENTO DO VINCULO",
    "SITUACAO_VINCULO": r"SITUACAO DO VINCULO|STATUS DO VINCULO",
    "DATA_DECLARACAO": r"DATA DA DECLARACAO|DECLARACAO EMITIDA EM",
    "RESPONSAVEL_DECLARACAO": r"RESPONSAVEL PELA DECLARACAO|NOME DO RESPONSAVEL|NOME DO REPRESENTANTE|REPRESENTANTE DA EMPRESA",
    "FUNCAO_RESPONSAVEL": r"(?:FUNCAO|CARGO) DO (?:RESPONSAVEL|REPRESENTANTE)",
}
_TITULOS = {"DECLARACAO DE VINCULO", "DECLARACAO DE VINCULO EMPREGATICIO",
            "DECLARACAO DE VINCULO TRABALHISTA", "DECLARACAO DE TRABALHO"}
_ALTERNATIVAS = {
    "DECLARACAO_PRESTACAO_SERVICOS": {"DECLARACAO DE PRESTACAO DE SERVICOS"},
    "DECLARACAO_TRABALHO_AUTONOMO": {"DECLARACAO DE TRABALHO AUTONOMO", "DECLARACAO DE TRABALHADOR AUTONOMO"},
    "DECLARACAO_RENDA": {"DECLARACAO DE RENDA"},
    "DECLARACAO_DESEMPREGO": {"DECLARACAO DE DESEMPREGO"},
}
_EXCLUSAO = re.compile(
    r"\b(?:SEM VINCULO EMPREGATICIO|(?:TRABALHA|ATUA|PRESTA SERVICOS) COMO AUTONOM[OA])\b"
    r"|(?:^|\n)(?:REGIME|NATUREZA DA RELACAO)\s*:\s*(?:AUTONOM[OA]|PRESTACAO DE SERVICOS)\b"
)
_TRABALHADOR = {"DADOS DO TRABALHADOR", "DADOS DO EMPREGADO", "IDENTIFICACAO DO TRABALHADOR"}
_VINCULO = {"DADOS DO VINCULO", "DADOS PROFISSIONAIS"}
_SECAO_TERCEIRO = re.compile(r"^DADOS (?:DO|DA) (?:REPRESENTANTE|RESPONSAVEL|EMPREGADOR|TESTEMUNHA|TERCEIRO)$")
_OUTROS = re.compile(r"^(?:DADOS (?:DO|DA) )?(?:REPRESENTANTE|RESPONSAVEL|EMPREGADOR|TESTEMUNHA|TERCEIRO)\b")
_CPF = r"[0-9]{3}[. ]?[0-9]{3}[. ]?[0-9]{3}[- ]?[0-9]{2}"
_DATA = r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}"
_NOME = r"[A-Z][A-Z .'-]{2,70}?"
_NARRATIVA = re.compile(
    rf"\bDECLAR(?:AMOS|O) QUE (?P<nome>{_NOME})"
    rf"(?:,\s*(?:INSCRIT[OA] NO )?CPF\s*:?\s*(?P<cpf>{_CPF})\s*,?)?"
    r"\s+(?:TRABALHA|TRABALHOU) (?:PARA|NA EMPRESA|NA ENTIDADE)\s+"
    r"(?P<empregador>[A-Z][A-Z0-9 .&'-]{1,90}?)"
    r"(?=,\s*|;|\s+(?:DESDE|NA FUNCAO|NO CARGO|EXERCENDO|COM REMUNERACAO|COM SALARIO)\b|[.!?](?:\s|$)|$)"
)
_RESPONSAVEL = re.compile(
    rf"\bDECLARACAO(?: E| FOI)? ASSINADA POR (?P<nome>{_NOME}),\s*"
    r"NA QUALIDADE DE (?P<funcao>[A-Z][A-Z .'-]{2,70}?)(?=[.;]|$)"
)
_NARRATIVOS = {
    "CARGO": r"(?:NA FUNCAO DE|NO CARGO DE|EXERCENDO A FUNCAO DE) ([A-Z][A-Z .'-]{2,70}?)"
             r"(?=,|;|[.]| DESDE\b| COM (?:SALARIO|REMUNERACAO)\b|$)",
    "ADMISSAO": rf"(?:\bADMITID[OA] EM|\bCOM INICIO EM|^[, ]*DESDE) ({_DATA})(?![0-9])",
    "DESLIGAMENTO": rf"(?:DESLIGAD[OA] EM|COM DESLIGAMENTO EM) ({_DATA})(?![0-9])",
    "SITUACAO_VINCULO": r"\bVINCULO (?:FOI |ESTA )?(ENCERRADO|ATIVO|SUSPENSO)\b",
    "CNPJ_EMPREGADOR": r"^[, ]+(?:INSCRIT[OA] NO )?CNPJ\s*:?\s*([A-Z0-9./-]+)(?=,|;|\s|$)",
}
_NATUREZAS = ("BRUTA", "LIQUIDA", "DECLARADA")
_MOEDAS = tuple(f"REMUNERACAO_{n}" for n in _NATUREZAS)
CAMPOS_DECLARACAO_VINCULO = (*_ROTULOS, *(
    f"{base}{sufixo}" for base in _MOEDAS for sufixo in ("", "_ORIGINAL", "_PERIODICIDADE")
))
_QUALIFICADOR = r"BRUT[OA]|LIQUID[OA]|MENSA(?:L|IS)|SEMANA(?:L|IS)|DIARI[OA]S?|HORARI[OA]S?|ANUA(?:L|IS)|QUINZENAL"
_SALARIO = re.compile(rf"(?:SALARIO|REMUNERACAO)(?P<qual>(?: (?:{_QUALIFICADOR}))*)\s*:")
_SALARIO_NARRATIVO = re.compile(rf"\b(?:COM|RECEBENDO) (?:SALARIO|REMUNERACAO)"
                               rf"(?P<qual>(?: (?:{_QUALIFICADOR}))*) (?:DE )?")
_DINHEIRO = re.compile(r"R\$\s*(?:[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)(?:,[0-9]{2})?(?![0-9.,])")


def _campo(texto):
    for campo, expressao in _ROTULOS.items():
        if m := re.fullmatch(rf"(?:{expressao})(?:\s*:\s*(.*))?", texto):
            return campo, m[1] or ""
    return None, ""


def avaliar_declaracoes_trabalhistas(linhas):
    conjunto = set(linhas)
    texto = " ".join(linhas)
    titulo = bool(conjunto & _TITULOS)
    narrativo = bool(_NARRATIVA.search(texto))
    rotulos = {_campo(l)[0] for l in linhas}
    estruturado = {"NOME", "EMPREGADOR"}.issubset(rotulos)
    negacao = bool(_EXCLUSAO.search("\n".join(linhas)))
    evidencias = ["TITULO DE DECLARACAO"] if titulo else []
    if negacao and titulo:
        evidencias.append("RELACAO DECLARADA INCOMPATIVEL")
    return {
        "DECLARACAO_VINCULO": (
            titulo and not negacao and (narrativo or (estruturado and ("DECLARACAO DE TRABALHO" not in conjunto
                              or bool(rotulos & {"ADMISSAO", "SITUACAO_VINCULO", "CPF"})))),
            evidencias,
        ),
        **{tipo: (bool(conjunto & nomes), [tipo] if conjunto & nomes else [])
           for tipo, nomes in _ALTERNATIVAS.items()},
    }


def _grupos(pagina):
    partes = _partes_leitura(pagina)
    if pagina.get("linhas") and not pagina.get("grupos_leitura"):
        partes = [(pagina["texto"], pagina["linhas"])]
    for texto, linhas in partes:
        if linhas:
            yield [(normalizar_rotulo(l["texto"]).strip(), _caixa(l)) for l in linhas]
        else:
            yield [(normalizar_rotulo(l).strip(), None) for l in texto.splitlines() if l.strip()]


def _alinhado(caixa, linhas, *, outro_rotulo=None):
    if not caixa:
        return ""
    _, y0, x1, y1 = caixa
    alinhados = [(t, c) for t, c in linhas if c and 0 <= c[0] - x1 <= 100
                 and min(c[3], y1) - max(c[1], y0) >= .5 * min(c[3] - c[1], y1 - y0)]
    barreira = min((c[0] for t, c in alinhados if (_campo(t)[0] or _OUTROS.match(t) or _SALARIO.match(t)
                       or (outro_rotulo and outro_rotulo(t)))),
                   default=float("inf"))
    certos = [t for t, c in alinhados if c[2] <= barreira and c[0] < barreira]
    return certos[0] if len(certos) == 1 else (None if certos else "")


def _registro(campo, status, pagina=None, valor=None, evidencia=None):
    return {"campo": campo, "valor": valor, "status": status, "pagina_origem": pagina,
            "trecho_origem": evidencia}


def _interpretar(campo, valor, pagina, evidencia):
    status = "ENCONTRADO"
    if valor is None:
        status = "AMBIGUO"
    elif not valor or "?" in valor or "ILEGIVEL" in valor or re.fullmatch(r"[-_/ .]+", valor):
        status = "EVIDENCIA_INSUFICIENTE"
    elif campo in {"CPF", "CPF_EMPREGADOR"}:
        if re.fullmatch(_CPF, valor) and validar_cpf(valor):
            valor = re.sub(r"\D", "", valor)
        else:
            status = "INVALIDO"
    elif campo == "CNPJ_EMPREGADOR":
        valor, status, evidencia = interpretar_cnpj(valor)
    elif campo in {"ADMISSAO", "DESLIGAMENTO"} or campo.startswith("DATA_"):
        if re.fullmatch(_DATA, valor) and validar_data(valor):
            dia, mes, ano = valor.split("/")
            valor = f"{dia.zfill(2)}/{mes.zfill(2)}/{ano}"
        else:
            status = "INVALIDO"
    elif campo in {"NOME", "RESPONSAVEL_DECLARACAO"} and not re.fullmatch(_NOME, valor):
        status = "EVIDENCIA_INSUFICIENTE"
    return _registro(campo, status, pagina, valor if status == "ENCONTRADO" else None, evidencia)


def _moeda(qualificadores, texto, pagina, evidencia):
    qual = qualificadores.strip()
    naturezas = {n for n, padrao in (("BRUTA", r"\bBRUT[OA]\b"), ("LIQUIDA", r"\bLIQUID[OA]\b"))
                 if re.search(padrao, qual)}
    base = "REMUNERACAO_" + (next(iter(naturezas)) if len(naturezas) == 1 else "DECLARADA")
    sufixo = re.fullmatch(rf"\s*({_DINHEIRO.pattern})\s*(?:POR\s+([A-Z]+)|({_QUALIFICADOR}))?\s*[.;]?", texto)
    if not sufixo or len(naturezas) > 1:
        estado = "AMBIGUO" if len(_DINHEIRO.findall(texto)) > 1 or len(naturezas) > 1 else "EVIDENCIA_INSUFICIENTE"
        return [_registro(base, estado, pagina, evidencia=evidencia)]
    original, por, frequencia = sufixo.groups()
    texto_periodo = " ".join(filter(None, (qual, por, frequencia)))
    periodos = {p for p, rx in (
        ("HORARIA", r"\b(?:HORARI[OA]S?|HORA)\b"),
        ("MENSAL", r"\b(?:MENSA(?:L|IS)|MES)\b"), ("SEMANAL", r"\b(?:SEMANA(?:L|IS)|SEMANA)\b"),
        ("DIARIA", r"\b(?:DIARI[OA]S?|DIA)\b"), ("ANUAL", r"\b(?:ANUA(?:L|IS)|ANO)\b"),
        ("QUINZENAL", r"\b(?:QUINZENAL|QUINZENA)\b"),
    ) if re.search(rx, texto_periodo)}
    if por and not periodos:
        periodos.add(f"POR {por}")
    numero = Decimal(original[2:].strip().replace(".", "").replace(",", "."))
    return [
        _registro(base, "ENCONTRADO", pagina, format(numero, ".2f"), evidencia),
        _registro(base + "_ORIGINAL", "ENCONTRADO", pagina, original, evidencia),
        _registro(base + "_PERIODICIDADE", "AMBIGUO" if len(periodos) > 1 else "ENCONTRADO" if periodos else "NAO_ENCONTRADO",
                  pagina, next(iter(periodos)) if len(periodos) == 1 else None, evidencia),
    ]


def _resolver(campo, candidatos):
    uteis = [c for c in candidatos if c["status"] not in {"NAO_ENCONTRADO", "EVIDENCIA_INSUFICIENTE"}]
    if campo in _MOEDAS and any(c["status"] == "EVIDENCIA_INSUFICIENTE" for c in candidatos):
        uteis = candidatos
    if len({(r["status"], r["valor"]) for r in uteis}) > 1:
        return _registro(campo, "AMBIGUO", uteis[0]["pagina_origem"], evidencia="EVIDENCIAS_CONFLITANTES")
    return (uteis or candidatos or [_registro(campo, "NAO_ENCONTRADO")])[0]


def _narrar(texto, pagina):
    evidencia = "RELACAO_NARRATIVA_EXPLICITA"
    vinculos = list(_NARRATIVA.finditer(texto))
    for indice, m in enumerate(vinculos):
        for campo, grupo in (("NOME", "nome"), ("CPF", "cpf"), ("EMPREGADOR", "empregador")):
            if m[grupo]:
                yield _interpretar(campo, m[grupo].strip(), pagina, evidencia)
        fim = vinculos[indice + 1].start() if indice + 1 < len(vinculos) else len(texto)
        cauda = re.split(r"[.!?;](?=\s|$)|\b(?:REPRESENTANTE|RESPONSAVEL|TESTEMUNHA)\b",
                        texto[m.end():fim], maxsplit=1)[0]
        for campo, expressao in _NARRATIVOS.items():
            for valor in re.findall(expressao, cauda):
                yield _interpretar(campo, valor.strip(), pagina, evidencia)
        salarios = list(_SALARIO_NARRATIVO.finditer(cauda))
        for i, salario in enumerate(salarios):
            limite = salarios[i + 1].start() if i + 1 < len(salarios) else len(cauda)
            bruto = re.split(r",(?=\s+[A-Z])|\b(?:BENEFICIOS?|DESCONTOS?)\b",
                             cauda[salario.end():limite], maxsplit=1)[0]
            bruto = re.sub(r"\s+E\s*$", "", bruto.strip(" ,"))
            yield from _moeda(salario["qual"], bruto, pagina, evidencia)
    for responsavel in _RESPONSAVEL.finditer(texto):
        yield _interpretar("RESPONSAVEL_DECLARACAO", responsavel["nome"], pagina, evidencia)
        yield _interpretar("FUNCAO_RESPONSAVEL", responsavel["funcao"], pagina, evidencia)


def extrair_declaracao_vinculo_trabalhista(documento):
    candidatos = {campo: [] for campo in CAMPOS_DECLARACAO_VINCULO}
    for pagina in documento["paginas"]:
        numero = pagina["numero"]
        for linhas in _grupos(pagina):
            secao_trabalhador, secao_terceiro, bloco_terceiro = False, False, False
            prosa = []
            for texto, caixa in linhas:
                if texto in _TRABALHADOR or texto in _VINCULO:
                    secao_trabalhador, secao_terceiro, bloco_terceiro = texto in _TRABALHADOR, False, False
                    prosa.append(";")
                    continue
                campo, valor = _campo(texto)
                if _SECAO_TERCEIRO.fullmatch(texto):
                    bloco_terceiro = True
                if _OUTROS.match(texto):
                    secao_trabalhador, secao_terceiro = False, True
                if secao_trabalhador and campo is None:
                    for chave in ("NOME", "CPF"):
                        if m := re.fullmatch(rf"{chave}\s*:\s*(.*)", texto):
                            campo, valor = chave, m[1]
                            break
                cargo_generico = campo == "CARGO" and re.match(r"^(?:CARGO|FUNCAO)\s*:", texto)
                if bloco_terceiro and (cargo_generico or campo in {"ADMISSAO", "DESLIGAMENTO", "SITUACAO_VINCULO"}):
                    prosa.append(";")
                    continue
                if campo:
                    prosa.append(";")
                    evid = "ROTULO_EXPLICITO" if valor or caixa is None else "ROTULO_E_GEOMETRIA"
                    candidatos[campo].append(_interpretar(campo, valor or _alinhado(caixa, linhas), numero, evid))
                elif salario := _SALARIO.match(texto):
                    prosa.append(";")
                    if bloco_terceiro:
                        continue
                    valor = texto[salario.end():].strip() or _alinhado(caixa, linhas)
                    for r in _moeda(salario["qual"], valor or "", numero, "REMUNERACAO_DECLARADA"):
                        candidatos[r["campo"]].append(r)
                elif not secao_terceiro and re.match(r"^CPF\s*:", texto):
                    candidatos["CPF"].append(_registro(
                        "CPF", "EVIDENCIA_INSUFICIENTE", numero, evidencia="TITULARIDADE_INCONCLUSIVA",
                    ))
                    prosa.append(";")
                elif texto and not _OUTROS.match(texto):
                    prosa.append(texto)
                else:
                    prosa.append(";")
            for r in _narrar(" ".join(prosa), numero):
                candidatos[r["campo"]].append(r)

    campos = {c: _resolver(c, v) for c, v in candidatos.items()}
    trabalhadores = any(campos[c]["status"] == "AMBIGUO" for c in ("NOME", "CPF"))
    empregadores = any(campos[c]["status"] == "AMBIGUO" for c in ("EMPREGADOR", "CNPJ_EMPREGADOR", "CPF_EMPREGADOR"))
    if trabalhadores or empregadores or campos["ADMISSAO"]["status"] == "AMBIGUO":
        excluir = {"DATA_DECLARACAO", "RESPONSAVEL_DECLARACAO", "FUNCAO_RESPONSAVEL"}
        if not trabalhadores:
            excluir.update(("NOME", "CPF"))
        for c, r in campos.items():
            if c not in excluir and r["status"] != "NAO_ENCONTRADO":
                campos[c] = _registro(c, "AMBIGUO", r["pagina_origem"], evidencia="VINCULO_INCONCLUSIVO")
    inicio, fim = campos["ADMISSAO"], campos["DESLIGAMENTO"]
    if inicio["valor"] and fim["valor"] and (
        tuple(reversed(inicio["valor"].split("/"))) > tuple(reversed(fim["valor"].split("/")))
    ):
        for campo in (inicio, fim):
            campo.update(valor=None, status="INVALIDO", trecho_origem="CRONOLOGIA_INCONSISTENTE")
    for base in _MOEDAS:
        chaves = (base, base + "_ORIGINAL", base + "_PERIODICIDADE")
        if (campos[base]["status"] == "AMBIGUO" or campos[chaves[2]]["status"] == "AMBIGUO"):
            for c in chaves:
                campos[c] = _registro(c, "AMBIGUO", campos[c]["pagina_origem"], evidencia="REMUNERACAO_INCONCLUSIVA")
        elif campos[base]["status"] == "ENCONTRADO" and campos[chaves[1]]["status"] == "AMBIGUO":
            campos[chaves[1]] = candidatos[chaves[1]][0]
    return campos
