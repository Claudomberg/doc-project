"""Extrai evidências de CTPS física, sem inferir situação trabalhista."""
import re
from datetime import datetime

from ceuma.documentos.ctps_digital import avaliar_ctps_digital
from ceuma.extracao.empregador import interpretar_cnpj
from ceuma.extracao.campos import normalizar_rotulo, validar_cpf, validar_data

CAMPOS_CTPS = ("NOME", "CPF", "DATA_NASCIMENTO", "NUMERO_CTPS", "SERIE", "UF")
CAMPOS_CONTRATO = ("EMPREGADOR", "CNPJ", "CARGO", "ADMISSAO", "DESLIGAMENTO", "SITUACAO")
_PESSOA = {
    "NOME": r"NOME(?: COMPLETO| DO TRABALHADOR| DO TITULAR)?",
    "CPF": r"CPF(?: DO TRABALHADOR| DO TITULAR)?",
    "DATA_NASCIMENTO": r"(?:DATA DE )?NASCIMENTO",
    "NUMERO_CTPS": r"(?:NUMERO|N[.º°])(?: DA CTPS)?|CTPS N[.º°]?",
    "SERIE": r"SERIE", "UF": r"UF",
}
_CONTRATO = {
    "EMPREGADOR": r"EMPREGADOR|RAZAO SOCIAL",
    "CNPJ": r"CNPJ(?: DO EMPREGADOR)?",
    "CARGO": r"CARGO|FUNCAO",
    "ADMISSAO": r"(?:DATA DE )?ADMISSAO",
    "DESLIGAMENTO": r"(?:DATA DE )?(?:DESLIGAMENTO|SAIDA|RESCISAO)",
    "SITUACAO": r"SITUACAO(?: DO CONTRATO)?",
}
_TITULO = re.compile(
    r"(?:(CONTINUACAO(?: DO)? )?CONTRATO DE TRABALHO)(?:\s*(?:N[.º°]?\s*)?(\d+))?\s*[:.]?"
)
_PESSOAL = {"IDENTIFICACAO DO TRABALHADOR", "QUALIFICACAO CIVIL"}
_PAGINACAO = re.compile(r"PAGINA\s+(\d+)\s+DE\s+(\d+)")
_PAPEIS_TERCEIROS = (
    r"REPRESENTANTE|RESPONSAVEL|FILIACAO|GENITOR[AE]?|MAE|PAI|CONJUGE|"
    r"TESTEMUNHA|PROCURADOR[AE]?|TUTOR[AE]?|DECLARANTE|EMPREGADOR|TERCEIRO"
)
_TERCEIROS = re.compile(rf"^(?:CPF\s*:?\s*(?:(?:DO|DA|DE) )?)?(?:{_PAPEIS_TERCEIROS})\b")
_ANOTACOES = {"ANOTACOES GERAIS", "ANOTACOES"}
_DATA = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
_UFS = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())


def avaliar_tipos_trabalhistas(linhas):
    """Usa títulos autônomos e um segundo indício próprio do documento."""
    titulos = set(linhas)
    corpo = "\n".join(linhas)
    pessoal = bool(titulos & _PESSOAL)
    contrato = any(_TITULO.fullmatch(linha) for linha in linhas)
    capa = bool(titulos & {"CARTEIRA DE TRABALHO E PREVIDENCIA SOCIAL", "CTPS FISICA"})
    numero_serie = bool(re.search(r"(?m)^(?:NUMERO DA CTPS|CTPS N[.º°]?)\b", corpo)
                        and re.search(r"(?m)^SERIE\b", corpo))
    trabalhador = bool(re.search(r"(?m)^(?:EMPREGADO|TRABALHADOR|NOME|CPF)\b", corpo))
    digital, evidencias_digitais = avaliar_ctps_digital(linhas, tem_trabalhador=trabalhador)
    fisica = (capa and (pessoal or numero_serie or contrato)) or (pessoal and numero_serie)
    return {
        "CTPS_FISICA": (fisica, ["FORMULARIO CTPS E CONTEXTO"] if fisica else []),
        "CTPS_DIGITAL": (digital, evidencias_digitais),
    }


def _linhas(pagina):
    grupos = pagina.get("grupos_leitura") or [pagina.get("linhas") or pagina.get("linhas_ocr") or []]
    for grupo in grupos:
        dados = [(normalizar_rotulo(l["texto"]).strip(), l.get("caixa")) for l in grupo]
        dados = [(texto, caixa) for texto, caixa in dados if texto]
        if dados:
            yield dados
    if not any(grupos):
        yield [(normalizar_rotulo(linha).strip(), None) for linha in pagina["texto"].splitlines()
               if linha.strip()]


def _faixas(linhas):
    """Separa colunas quando cabeçalhos alinhados estabelecem limites distintos."""
    titulos = [(texto, caixa) for texto, caixa in linhas if caixa and _TITULO.fullmatch(texto)]
    if not titulos or not all(c for _, c in linhas):
        yield linhas
        return
    topo = min((c for _, c in titulos), key=lambda c: c[1])
    colunas = sorted((c for _, c in titulos if min(c[3], topo[3]) > max(c[1], topo[1])),
                     key=lambda c: c[0])
    if len(colunas) < 2:
        yield linhas
        return
    yield [(t, c) for t, c in linhas if c[3] <= topo[1]]
    if any(a[2] >= b[0] for a, b in zip(colunas, colunas[1:])):
        return
    cortes = [float("-inf"), *((a[2] + b[0]) / 2 for a, b in zip(colunas, colunas[1:])),
              float("inf")]
    for ini, fim in zip(cortes, cortes[1:]):
        yield [(t, c) for t, c in linhas if c[3] > topo[1] and ini <= c[0] and c[2] <= fim]


def _rotulo(texto, regras):
    for campo, padrao in regras.items():
        if m := re.fullmatch(rf"(?:{padrao})(?=$|[\s:])\s*:?\s*(.*)", texto):
            return campo, m[1].strip()
    return None, ""


def _geometrico(caixa, linhas, regras):
    if not caixa:
        return ""
    x0, y0, x1, y1 = caixa
    alinhados = [
        (texto, c) for texto, c in linhas if c and 0 <= c[0] - x1 <= 100
        and min(y1, c[3]) - max(y0, c[1]) >= 0.5 * min(y1 - y0, c[3] - c[1])
    ]
    barreira = min((c[0] for texto, c in alinhados
                   if _rotulo(texto, regras)[0] or _TITULO.fullmatch(texto)), default=float("inf"))
    candidatos = [texto for texto, c in alinhados if c[2] <= barreira and c[0] < barreira]
    return candidatos[0] if len(candidatos) == 1 else (None if candidatos else "")


def _registro(campo, valor, pagina, *, status="ENCONTRADO", motivo=None):
    numero = pagina["numero"] if pagina else None
    return {"campo": campo, "valor": valor, "status": status, "pagina_origem": numero,
            "metodo_leitura": pagina.get("metodo") if pagina else None, "trecho_origem": motivo}


def _interpretar(campo, texto, pagina):
    if texto is None:
        return _registro(campo, None, pagina, status="AMBIGUO")
    if texto in {"", "-", "___"} or re.fullmatch(r"[_./ -]+", texto):
        return _registro(campo, None, pagina, status="NAO_ENCONTRADO")
    if "ILEGIVEL" in texto or "MANUSCRITO" in texto or "?" in texto:
        return _registro(campo, None, pagina, status="EVIDENCIA_INSUFICIENTE")
    if campo in {"DATA_NASCIMENTO", "ADMISSAO", "DESLIGAMENTO"}:
        if not (_DATA.fullmatch(texto) and validar_data(texto)):
            return _registro(campo, None, pagina, status="INVALIDO")
        texto = datetime.strptime(texto, "%d/%m/%Y").strftime("%d/%m/%Y")
    elif campo == "CPF":
        if not re.fullmatch(r"[0-9]{3}[. ]?[0-9]{3}[. ]?[0-9]{3}[- ]?[0-9]{2}", texto) or not validar_cpf(texto):
            return _registro(campo, None, pagina, status="INVALIDO")
        texto = re.sub(r"\D", "", texto)
    elif campo == "CNPJ":
        texto, status, motivo = interpretar_cnpj(texto)
        return _registro(campo, texto, pagina, status=status, motivo=motivo)
    elif campo in {"NUMERO_CTPS", "SERIE"} and not re.fullmatch(r"[0-9]+", texto):
        return _registro(campo, None, pagina, status="INVALIDO")
    elif campo == "UF" and texto not in _UFS:
        return _registro(campo, None, pagina, status="INVALIDO")
    elif campo in {"NOME", "EMPREGADOR", "CARGO", "SITUACAO"} and not re.search(r"[A-Z]", texto):
        return _registro(campo, None, pagina, status="EVIDENCIA_INSUFICIENTE")
    return _registro(campo, texto, pagina)


def _resolver(campo, candidatos):
    uteis = [c for c in candidatos if c["status"] != "NAO_ENCONTRADO"]
    if not uteis:
        return candidatos[0] if candidatos else _registro(campo, None, None, status="NAO_ENCONTRADO")
    if len({(c["status"], c["valor"]) for c in uteis}) > 1:
        return _registro(campo, None, None, status="AMBIGUO")
    return uteis[0]


def extrair_ctps_fisica(documento):
    pessoais = {campo: [] for campo in CAMPOS_CTPS}
    ocorrencias, paginas = [], []
    ultimo = None
    numeracao = {}
    for pagina in documento["paginas"]:
        numero = pagina["numero"]
        regioes = []
        referencias = []
        for grupo in _linhas(pagina):
            for linhas in _faixas(grupo):
                bloco = None
                contexto = None
                for texto, caixa in linhas:
                    if folha := _PAGINACAO.fullmatch(texto):
                        numeracao.setdefault(numero, set()).add(tuple(map(int, folha.groups())))
                    elif texto in _PESSOAL:
                        contexto, bloco = texto.replace(" ", "_"), None
                        regioes.append(contexto)
                    elif texto in _ANOTACOES:
                        contexto, bloco = None, None
                        regioes.append("ANOTACOES_GERAIS")
                    elif texto in {"CARTEIRA DE TRABALHO E PREVIDENCIA SOCIAL", "CTPS FISICA"}:
                        contexto, bloco = "CAPA", None
                        regioes.append("CAPA")
                    elif titulo := _TITULO.fullmatch(texto):
                        contexto = None
                        continuacao, referencia = titulo.groups()
                        if (continuacao and referencia and ultimo and referencia == ultimo["referencia"]
                                and numero == ultimo["paginas"][-1] + 1
                                and sum(b["referencia"] == referencia and numero - 1 in b["paginas"]
                                        for b in ocorrencias) == 1):
                            bloco = ultimo
                            bloco["paginas"].append(numero)
                        else:
                            bloco = {"referencia": referencia, "paginas": [numero],
                                     "campos": {c: [] for c in CAMPOS_CONTRATO},
                                     "continuacao_inconclusiva": bool(continuacao)}
                            ocorrencias.append(bloco)
                            ultimo = bloco
                        regioes.append("CONTRATO")
                        referencias.append({"regiao": "CONTRATO", "caixa": list(caixa) if caixa else None})
                    elif bloco is not None:
                        campo, valor = _rotulo(texto, _CONTRATO)
                        if campo:
                            valor = valor or _geometrico(caixa, linhas, _CONTRATO)
                            bloco["campos"][campo].append(_interpretar(campo, valor, pagina))
                        elif texto:
                            bloco["outro_conteudo"] = True
                    elif contexto and _TERCEIROS.match(texto):
                        contexto = None
                    elif contexto:
                        campo, valor = _rotulo(texto, _PESSOA)
                        if campo == "CPF" and contexto == "CAPA":
                            continue
                        if campo:
                            valor = valor or _geometrico(caixa, linhas, _PESSOA)
                            pessoais[campo].append(_interpretar(campo, valor, pagina))
        paginas.append({"pagina": numero, "regioes": list(dict.fromkeys(regioes)) or ["INCONCLUSIVA"],
                        "referencias": referencias})

    contratos, formularios = [], set()
    for bloco in ocorrencias:
        campos = {c: _resolver(c, valores) for c, valores in bloco["campos"].items()}
        if not bloco.get("outro_conteudo") and (
            not bloco["continuacao_inconclusiva"] and len(bloco["paginas"]) == 1
            and sum(bool(v) for v in bloco["campos"].values()) >= 3
            and all(c["status"] == "NAO_ENCONTRADO" for c in campos.values())
        ):
            formularios.update(bloco["paginas"])
            continue
        inicio, fim = campos["ADMISSAO"], campos["DESLIGAMENTO"]
        if inicio["valor"] and fim["valor"] and (
            datetime.strptime(inicio["valor"], "%d/%m/%Y") > datetime.strptime(fim["valor"], "%d/%m/%Y")
        ):
            for c in (inicio, fim):
                c.update(valor=None, status="INVALIDO", trecho_origem="CRONOLOGIA_INCONSISTENTE")
        if campos["CNPJ"]["valor"] and not campos["EMPREGADOR"]["valor"]:
            campos["CNPJ"]["status"] = "EVIDENCIA_INSUFICIENTE"
            campos["CNPJ"]["trecho_origem"] = "TITULARIDADE_INCONCLUSIVA"
        contratos.append({**bloco, "campos": campos})
    paginas_contrato = {p for contrato in contratos for p in contrato["paginas"]}
    preenchidas = {
        campo["pagina_origem"] for contrato in contratos for campo in contrato["campos"].values()
        if campo["valor"] is not None or campo["status"] == "INVALIDO"
    }
    for pagina in paginas:
        if pagina["pagina"] in formularios:
            if pagina["pagina"] not in paginas_contrato:
                pagina["regioes"].remove("CONTRATO")
            pagina["regioes"].append("FORMULARIO_APARENTEMENTE_SEM_PREENCHIMENTO")
    confirmados = [c for c in contratos if c["campos"]["EMPREGADOR"]["valor"]
                   and c["campos"]["ADMISSAO"]["valor"] and not c["continuacao_inconclusiva"]]
    final = confirmados[-1] if confirmados else None
    seguinte = max(final["paginas"]) + 1 if final else None
    estado_seguinte = ("PREENCHIDA" if seguinte in preenchidas else
                       "APARENTEMENTE_SEM_PREENCHIMENTO" if seguinte in formularios else
                       "AUSENTE_OU_INCONCLUSIVA")
    total = len(paginas)
    sequencia_verificada = (
        bool(final) and len(confirmados) == len(contratos)
        and estado_seguinte == "APARENTEMENTE_SEM_PREENCHIMENTO" and seguinte == total
        and all(p["pagina"] == i and numeracao.get(i) == {(i, total)}
                for i, p in enumerate(paginas, 1))
    )
    return {
        "pessoais": {c: _resolver(c, valores) for c, valores in pessoais.items()},
        "contratos": contratos, "paginas": paginas,
        "ultimo_entre_apresentados": next(
            (i for i, contrato in reversed(list(enumerate(contratos, 1))) if contrato is final), None,
        ),
        "sequencia": "VERIFICADA_NAS_PAGINAS_APRESENTADAS" if sequencia_verificada else "INCONCLUSIVA",
        "pagina_seguinte": estado_seguinte,
    }
