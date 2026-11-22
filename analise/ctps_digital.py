"""Extrai rótulos explícitos de CTPS Digital. Os layouts testados são sintéticos."""
import re
from datetime import datetime

from analise.extrator_campos import normalizar_rotulo, validar_cpf, validar_data
from analise.identificador_empregador import interpretar_cnpj


CAMPOS_CTPS_DIGITAL = ("NOME", "CPF", "DATA_NASCIMENTO")
_PESSOA = {
    "NOME": r"NOME(?: COMPLETO| DO (?:TRABALHADOR|TITULAR))?",
    "CPF": r"CPF(?: DO (?:TRABALHADOR|TITULAR))?",
    "DATA_NASCIMENTO": r"(?:DATA DE )?NASCIMENTO",
}
_TRABALHO = {
    "EMPREGADOR": r"(?:NOME DO )?EMPREGADOR|RAZAO SOCIAL",
    "CNPJ": r"CNPJ(?: DO EMPREGADOR)?",
    "IDENTIFICADOR_EMPREGADOR": r"IDENTIFICADOR DO EMPREGADOR|CAEPF|CEI|CNO",
    "CARGO": r"CARGO|FUNCAO",
    "ADMISSAO": r"(?:DATA DE )?ADMISSAO",
    "DESLIGAMENTO": r"(?:DATA DE )?(?:DESLIGAMENTO|RESCISAO|SAIDA)",
    "SITUACAO": r"SITUACAO(?: DO CONTRATO| DO VINCULO)?",
}
_TITULOS = {"CTPS DIGITAL", "CARTEIRA DE TRABALHO DIGITAL"}
_DADOS = {"DADOS PESSOAIS", "DADOS DO TRABALHADOR", "IDENTIFICACAO DO TRABALHADOR"}
_CONTRATOS = {"CONTRATOS DE TRABALHO", "VINCULOS TRABALHISTAS"}
_BLOCO = re.compile(r"(CONTINUACAO (?:DO )?)?CONTRATO(?: DE TRABALHO)?(?:\s+(?:N[.º°]?\s*)?([A-Z0-9-]+))?\s*:?")
_INTERROMPER = re.compile(r"^(?:ANOTACOES|ALTERACOES|ATUALIZACAO|DADOS DO EMPREGADOR|"
                          r"DADOS (?:DO|DA) (?:REPRESENTANTE|RESPONSAVEL)|"
                          r"(?:REPRESENTANTE|RESPONSAVEL|FILIACAO|TESTEMUNHA)(?=\s*:|\b)|"
                          r"CPF (?:DO|DA) (?:EMPREGADOR|REPRESENTANTE|RESPONSAVEL|MAE|PAI))\b")
_DATA = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")


def avaliar_ctps_digital(linhas, *, tem_trabalhador):
    titulo = any(linha in _TITULOS for linha in linhas)
    contexto = any(linha in _DADOS or linha in _CONTRATOS
                   or re.match(r"^DATA DE EMISSAO\b", linha) for linha in linhas)
    return titulo and tem_trabalhador and contexto, ["TITULO CTPS DIGITAL"] if titulo else []


def _grupos(pagina):
    linhas = pagina.get("linhas") or pagina.get("linhas_ocr")
    grupos = pagina.get("grupos_leitura") or ([linhas] if linhas else None)
    if grupos:
        for grupo in grupos:
            yield [(normalizar_rotulo(l["texto"]).strip(), l.get("caixa")) for l in grupo]
    else:
        yield [(normalizar_rotulo(t).strip(), None) for t in pagina["texto"].splitlines() if t.strip()]


def _colunas(linhas):
    titulos = [(t, c) for t, c in linhas if c and _BLOCO.fullmatch(t)]
    if len(titulos) >= 2:
        primeira = min((c for _, c in titulos), key=lambda c: c[1])
        colunas = sorted((c for _, c in titulos if min(c[3], primeira[3]) > max(c[1], primeira[1])),
                         key=lambda c: c[0])
        if len(colunas) >= 2:
            yield [(t, c) for t, c in linhas if c and c[3] <= primeira[1]], False
            if any(a[2] >= b[0] for a, b in zip(colunas, colunas[1:])):
                yield [], True
                return
            cortes = [float("-inf"), *((a[2] + b[0]) / 2 for a, b in zip(colunas, colunas[1:])), float("inf")]
            for ini, fim in zip(cortes, cortes[1:]):
                yield [(t, c) for t, c in linhas if c and c[3] > primeira[1] and ini <= c[0] and c[2] <= fim], False
            return
    yield linhas, False


def _campo(texto, regras):
    for nome, expressao in regras.items():
        if m := re.fullmatch(rf"(?:{expressao})(?:\s*:\s*(.*))?", texto):
            return nome, m[1] or ""
    return None, ""


def _valor_alinhado(caixa, linhas, regras):
    if caixa is None:
        return ""
    _, y0, x1, y1 = caixa
    alinhados = [
        (t, c) for t, c in linhas if c and 0 <= c[0] - x1 <= 100
        and min(y1, c[3]) - max(y0, c[1]) >= .5 * min(y1 - y0, c[3] - c[1])
    ]
    barreira = min((c[0] for t, c in alinhados if _campo(t, regras)[0] or _BLOCO.fullmatch(t)
                    or _INTERROMPER.match(t)), default=float("inf"))
    valores = [t for t, c in alinhados if c[2] <= barreira and c[0] < barreira]
    return valores[0] if len(valores) == 1 else (None if valores else "")


def _registro(campo, valor=None, status="NAO_ENCONTRADO", pagina=None, motivo=None):
    return {"campo": campo, "valor": valor, "status": status, "pagina_origem": pagina,
            "trecho_origem": motivo}


def _interpretar(campo, valor, pagina):
    estado, motivo = "ENCONTRADO", None
    if valor is None:
        estado = "AMBIGUO"
    elif not valor or re.fullmatch(r"[-_/ .]+", valor):
        estado = "NAO_ENCONTRADO"
    elif "ILEGIVEL" in valor or "?" in valor:
        estado = "EVIDENCIA_INSUFICIENTE"
    elif campo == "CPF":
        if re.fullmatch(r"[0-9]{3}[. ]?[0-9]{3}[. ]?[0-9]{3}[- ]?[0-9]{2}", valor) and validar_cpf(valor):
            valor = re.sub(r"\D", "", valor)
        else:
            estado = "INVALIDO"
    elif campo in {"DATA_NASCIMENTO", "ADMISSAO", "DESLIGAMENTO"}:
        if _DATA.fullmatch(valor) and validar_data(valor):
            valor = datetime.strptime(valor, "%d/%m/%Y").strftime("%d/%m/%Y")
        else:
            estado = "INVALIDO"
    elif campo == "CNPJ":
        valor, estado, motivo = interpretar_cnpj(valor)
    elif campo == "IDENTIFICADOR_EMPREGADOR":
        estado, motivo = "EVIDENCIA_INSUFICIENTE", "FORMATO_NAO_SUPORTADO"
    elif campo in {"EMPREGADOR", "CARGO", "SITUACAO"} and (
        not re.search(r"[A-Z]", valor) or re.search(
            r"\b(?:CNPJ|CPF|ADMISSAO|DESLIGAMENTO|CARGO|EMPREGADOR)\s*:", valor
        )
    ):
        estado = "EVIDENCIA_INSUFICIENTE"
    elif campo == "NOME" and not re.fullmatch(r"[A-Z][A-Z .'-]{2,70}", valor):
        estado = "EVIDENCIA_INSUFICIENTE"
    if estado != "ENCONTRADO" and campo != "IDENTIFICADOR_EMPREGADOR":
        valor = None
    return _registro(campo, valor, estado, pagina, motivo)


def _resolver(campo, registros):
    uteis = [r for r in registros if r["status"] != "NAO_ENCONTRADO"]
    if len({(r["status"], r["valor"]) for r in uteis}) > 1:
        return _registro(campo, status="AMBIGUO", pagina=uteis[0]["pagina_origem"])
    return (uteis or registros or [_registro(campo)])[0]


def _novo_contrato(contratos, ref, pagina, continuacao=False):
    contrato = {"referencia": ref, "paginas": [pagina], "continuacao_inconclusiva": continuacao,
                "campos": {campo: [] for campo in _TRABALHO}}
    contratos.append(contrato)
    return contrato


def extrair_ctps_digital(documento):
    pessoais = {campo: [] for campo in CAMPOS_CTPS_DIGITAL}
    contratos, pendencias = [], set()
    for pagina in documento["paginas"]:
        numero = pagina["numero"]
        for grupo in _grupos(pagina):
            for linhas, ambigua in _colunas(grupo):
                if ambigua:
                    pendencias.add("REGIOES_CONTRATUAIS_INCONCLUSIVAS")
                contexto = None
                atual = None
                for texto, caixa in linhas:
                    if texto in _TITULOS or texto in _DADOS or texto in _CONTRATOS:
                        contexto = "PESSOA" if texto in _DADOS else "CONTRATOS" if texto in _CONTRATOS else None
                        atual = None
                    elif bloco := _BLOCO.fullmatch(texto):
                        continuacao, ref = bloco.groups()
                        anteriores = [c for c in contratos if ref and c["referencia"] == ref
                                     and c["paginas"][-1] == numero - 1 and not c["continuacao_inconclusiva"]]
                        if continuacao and len(anteriores) == 1:
                            atual = anteriores[0]
                            atual["paginas"].append(numero)
                        else:
                            atual = _novo_contrato(contratos, ref, numero, bool(continuacao))
                        contexto = "CONTRATOS"
                    elif texto in {"DADOS DO EMPREGADOR", "DADOS DO CONTRATO"} and contexto == "CONTRATOS":
                        continue
                    elif _INTERROMPER.match(texto):
                        contexto, atual = None, None
                    elif contexto == "PESSOA":
                        campo, valor = _campo(texto, _PESSOA)
                        if campo:
                            valor = valor or _valor_alinhado(caixa, linhas, _PESSOA)
                            pessoais[campo].append(_interpretar(campo, valor, numero))
                    elif contexto == "CONTRATOS":
                        campo, valor = _campo(texto, _TRABALHO)
                        if not campo:
                            continue
                        if atual is None or (campo == "EMPREGADOR" and atual["campos"][campo]
                                             and not atual["referencia"]):
                            atual = _novo_contrato(contratos, None, numero)
                        valor = valor or _valor_alinhado(caixa, linhas, _TRABALHO)
                        atual["campos"][campo].append(_interpretar(campo, valor, numero))
                    elif _campo(texto, _TRABALHO)[0] in {"EMPREGADOR", "ADMISSAO", "DESLIGAMENTO"}:
                        pendencias.add("CAMPO_CONTRATUAL_SEM_BLOCO")
    for contrato in contratos:
        campos = {c: _resolver(c, valores) for c, valores in contrato["campos"].items()}
        contrato["campos"] = campos
        if campos["CNPJ"]["valor"] and not campos["EMPREGADOR"]["valor"]:
            campos["CNPJ"].update(status="EVIDENCIA_INSUFICIENTE", trecho_origem="TITULARIDADE_INCONCLUSIVA")
        inicio, fim = campos["ADMISSAO"], campos["DESLIGAMENTO"]
        if inicio["valor"] and fim["valor"] and (
            datetime.strptime(inicio["valor"], "%d/%m/%Y") > datetime.strptime(fim["valor"], "%d/%m/%Y")
        ):
            for item in (inicio, fim):
                item.update(valor=None, status="INVALIDO", trecho_origem="CRONOLOGIA_INCONSISTENTE")
        if (contrato["continuacao_inconclusiva"]
                or any(campos[c]["status"] != "ENCONTRADO" for c in ("EMPREGADOR", "ADMISSAO"))
                or any(c["status"] in {"AMBIGUO", "INVALIDO", "EVIDENCIA_INSUFICIENTE"}
                       for c in campos.values())):
            pendencias.add("CONTRATO_REQUER_REVISAO")
    pessoais = {c: _resolver(c, registros) for c, registros in pessoais.items()}
    if any(r["status"] in {"AMBIGUO", "INVALIDO", "EVIDENCIA_INSUFICIENTE"} for r in pessoais.values()):
        pendencias.add("TITULARIDADE_REQUER_REVISAO")
    return {"pessoais": pessoais, "contratos": contratos, "pendencias": sorted(pendencias)}
