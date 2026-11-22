"""Extração conservadora de registros civis de casamento."""
import re

from analise.associador_espacial import _caixa
from analise.datas_extenso import interpretar_data
from analise.extrator_campos import _partes_leitura, normalizar_rotulo, validar_cpf


TITULOS_CASAMENTO = {"CERTIDAO DE CASAMENTO", "REGISTRO DE CASAMENTO",
                    "CERTIDAO DE CASAMENTO COM AVERBACAO"}
TITULOS_EXATOS_ALTERNATIVOS = {
    "CERTIDAO_OBITO": {"CERTIDAO DE OBITO", "CERTIDAO DE REGISTRO DE OBITO"},
    "CASAMENTO_RELIGIOSO": {"CERTIDAO DE CASAMENTO RELIGIOSO"},
    "DECLARACAO_UNIAO_ESTAVEL": {"DECLARACAO DE UNIAO ESTAVEL"},
    "ESCRITURA_UNIAO_ESTAVEL": {"ESCRITURA DE UNIAO ESTAVEL", "ESCRITURA PUBLICA DE UNIAO ESTAVEL"},
    "DOCUMENTO_DIVORCIO": {"CERTIDAO DE DIVORCIO", "TERMO DE AVERBACAO DE DIVORCIO",
                           "CERTIDAO DE AVERBACAO", "DOCUMENTO DE AVERBACAO"},
}
_PESSOA = {
    "NOME": ("NOME", "NOME COMPLETO", "NOME ANTERIOR AO CASAMENTO"),
    "CPF": ("CPF",),
    "DATA_NASCIMENTO": ("DATA DE NASCIMENTO", "NASCIMENTO"),
    "FILIACAO": ("FILIACAO", "NOME DOS PAIS"),
    "PAI": ("NOME DO PAI", "PAI"),
    "MAE": ("NOME DA MAE", "MAE"),
    "NOME_APOS_CASAMENTO": ("NOME APOS O CASAMENTO", "NOME ADOTADO", "NOVO NOME"),
}
_REGISTRO = {
    "DATA_CASAMENTO": ("DATA DO CASAMENTO", "DATA DA CELEBRACAO"),
    "DATA_REGISTRO_CASAMENTO": ("DATA DO REGISTRO", "DATA DO REGISTRO DE CASAMENTO", "DATA DO ASSENTO"),
    "DATA_EMISSAO_CERTIDAO": ("DATA DE EMISSAO", "DATA DE EMISSAO DA CERTIDAO", "DATA DA EMISSAO"),
    "LOCAL_CASAMENTO": ("LOCAL DO CASAMENTO", "LOCAL DA CELEBRACAO"),
    "MATRICULA": ("MATRICULA", "MATRICULA DO REGISTRO"),
    "CARTORIO": ("CARTORIO", "SERVENTIA", "OFICIO DE REGISTRO CIVIL"),
    "REGIME_BENS": ("REGIME DE BENS",),
}
_AVERBACOES = ("ALTERACAO_NOME", "DIVORCIO", "SEPARACAO", "OBITO", "RETIFICACAO")
_CATEGORIAS = {
    "ALTERACAO_NOME": r"\b(?:ALTERACAO DE NOME|NOVO NOME)\b",
    "DIVORCIO": r"\bDIVORCIO\b",
    "SEPARACAO": r"\bSEPARACAO\b",
    "OBITO": r"\bOBITO\b",
    "RETIFICACAO": r"\bRETIFICACAO\b",
}
CAMPOS_CASAMENTO = (
    *(f"{campo}_CONJUGE_{ordem}" for ordem in (1, 2) for campo in _PESSOA),
    *_REGISTRO, "RELACAO_DECLARADA", "AVERBACAO",
    *(f"AVERBACAO_{a}" for a in _AVERBACOES),
    *(f"DATA_AVERBACAO_{a}" for a in _AVERBACOES),
    "NOME_AVERBADO_CONJUGE_1", "NOME_AVERBADO_CONJUGE_2", "DATA_AVERBACAO_SEM_TIPO",
    "DATA_CASAMENTO_AVERBADA", "DATA_REGISTRO_AVERBADA", "MATRICULA_AVERBADA",
)
_CONJUGE = r"(?:CONJUGE\s*(?:N[O.º°]?\s*)?([12])|((?:PRIMEIRO|SEGUNDO)) CONJUGE)"
_RE_CONJUGE = re.compile(rf"^(?:DADOS (?:DO |DE )?)?{_CONJUGE}$")
_RE_CAMPO_CONJUGE = re.compile(rf"^(.+?) (?:DO |DE )?{_CONJUGE}$")
_RETIFICADOS = {
    "DATA DO CASAMENTO RETIFICADA": "DATA_CASAMENTO_AVERBADA",
    "NOVA DATA DO CASAMENTO": "DATA_CASAMENTO_AVERBADA",
    "DATA DO REGISTRO RETIFICADA": "DATA_REGISTRO_AVERBADA",
    "MATRICULA RETIFICADA": "MATRICULA_AVERBADA",
}
_AUSENCIA_AVERBACAO = re.compile(r"^(?:NAO CONSTAM?|NADA CONSTA|NENHUMA|SEM AVERBACOES?)\b")
_RE_AVERBACAO = re.compile(r"^(?:AVERBAC(?:AO|OES)|ANOTAC(?:AO|OES))\b")
_RE_TERCEIROS = re.compile(r"^(?:(?:(?:NOME|CPF) (?:DO|DA) )?(?:TESTEMUNHAS?|OFICIAL|DECLARANTE|TERCEIROS?)|DADOS (?:DO|DA) (?:OFICIAL|TESTEMUNHAS?))\b")
_NARRATIVA = re.compile(
    r"(?:CERTIFICO QUE )?(?:CONTRA[IÍ]RAM MATRIM[OÔ]NIO|CASARAM-SE) (.+),\s*(?:EM|NO DIA) (.+?)\.?",
    re.I,
)


def _ordem(primeiro, segundo):
    return int(primeiro) if primeiro else 1 if segundo == "PRIMEIRO" else 2


def avaliar_casamento(linhas):
    corpo_original = []
    for linha in linhas:
        if _RE_AVERBACAO.match(linha):
            break
        corpo_original.append(linha)
    conjunto = set(corpo_original)
    titulo = bool(conjunto & TITULOS_CASAMENTO)
    corpo = "\n".join(corpo_original)
    registro = bool(re.search(r"\b(?:REGISTRO CIVIL|ASSENTO DE CASAMENTO|REGISTRO DE CASAMENTO)\b", corpo))
    matricula = bool(re.search(r"(?m)^MATRICULA(?: DO REGISTRO)?\s*:\s*[0-9]", corpo))
    ato = bool(re.search(r"(?m)^(?:DATA DO CASAMENTO|DATA DA CELEBRACAO)\s*:\s*\S", corpo)
               or re.search(r"\b(?:CONTRAIRAM MATRIMONIO|CASARAM-SE)\b", corpo))
    secoes = {_ordem(*m.groups()) for linha in corpo_original
              if (m := _RE_CONJUGE.fullmatch(linha.split(":")[0].strip()))}
    casamento = titulo and ((registro and matricula) or (ato and (registro or len(secoes) == 2)))
    return {
        "CERTIDAO_CASAMENTO": (casamento, ["TITULO DE CASAMENTO"] if titulo else []),
        **{tipo: (bool(conjunto & titulos), [tipo] if conjunto & titulos else [])
           for tipo, titulos in TITULOS_EXATOS_ALTERNATIVOS.items()},
    }


def _grupos(pagina):
    partes = _partes_leitura(pagina)
    if pagina.get("linhas") and not pagina.get("grupos_leitura"):
        partes = [(pagina["texto"], pagina["linhas"])]
    for texto, linhas in partes:
        if linhas:
            yield [(l["texto"].strip(), _caixa(l)) for l in linhas]
        else:
            yield [(t.strip(), None) for t in texto.splitlines() if t.strip()]


def _rotulo(texto):
    rotulo, sep, valor = texto.partition(":")
    return " ".join(normalizar_rotulo(rotulo).split()), valor.strip() if sep else ""


def _campo(rotulo, regras):
    return next((c for c, nomes in regras.items() if rotulo in nomes), None)


def _barreira(texto):
    r, _ = _rotulo(texto)
    return (_campo(r, _PESSOA) or _campo(r, _REGISTRO) or _RE_CONJUGE.fullmatch(r)
            or _RE_CAMPO_CONJUGE.fullmatch(r) or _RE_TERCEIROS.match(r) or _RE_AVERBACAO.match(r))


def _alinhado(caixa, linhas):
    if not caixa:
        return ""
    _, y0, x1, y1 = caixa
    alinhadas = [(t, c) for t, c in linhas if c and 0 <= c[0] - x1 <= 100
                 and min(y1, c[3]) - max(y0, c[1]) >= .5 * min(y1 - y0, c[3] - c[1])]
    limite = min((c[0] for t, c in alinhadas if _barreira(t)), default=float("inf"))
    valores = [t for t, c in alinhadas if c[0] < limite and c[2] <= limite]
    return valores[0] if len(valores) == 1 else (None if valores else "")


def _registro(campo, estado, pagina=None, valor=None, evidencia=None):
    return {"campo": campo, "status": estado, "pagina_origem": pagina,
            "valor": valor, "trecho_origem": evidencia}


def _interpretar(campo, valor, pagina, evidencia):
    estado = "ENCONTRADO"
    if valor is None:
        estado = "AMBIGUO"
    elif not valor or "?" in valor or re.fullmatch(r"[-_/ .]+", valor):
        estado = "EVIDENCIA_INSUFICIENTE"
    elif campo.startswith("CPF_"):
        if (re.fullmatch(r"[0-9]{3}[. ]?[0-9]{3}[. ]?[0-9]{3}[- ]?[0-9]{2}", valor)
                and validar_cpf(valor)):
            valor = re.sub(r"\D", "", valor)
        else:
            estado = "INVALIDO"
    elif campo.startswith("DATA_"):
        valor, valida = interpretar_data(valor)
        if not valida:
            estado = "INVALIDO"
    elif campo.startswith("MATRICULA") and not re.fullmatch(r"[0-9][0-9 .-]{0,62}[0-9]|[0-9]", valor):
        estado = "EVIDENCIA_INSUFICIENTE"
    elif campo.startswith(("NOME_", "PAI_", "MAE_")) and not (
        any(c.isalpha() for c in valor) and all(c.isalpha() or c in " .'’-–" for c in valor)
    ):
        estado = "EVIDENCIA_INSUFICIENTE"
    return _registro(campo, estado, pagina, valor if estado == "ENCONTRADO" else None, evidencia)


def _resolver(campo, registros):
    if len({(r["status"], r["valor"]) for r in registros}) > 1:
        return _registro(campo, "AMBIGUO", registros[0]["pagina_origem"], evidencia="EVIDENCIAS_CONFLITANTES")
    return registros[0] if registros else _registro(campo, "NAO_ENCONTRADO")


def _narrar(texto, pagina):
    m = _NARRATIVA.fullmatch(texto)
    if not m:
        return []
    nomes = re.split(r"\s+e\s+", m[1], flags=re.I)
    if len(nomes) != 2:
        return [_registro(f"NOME_CONJUGE_{ordem}", "AMBIGUO", pagina,
                          evidencia="ORDEM_NARRATIVA_INCONCLUSIVA") for ordem in (1, 2)]
    return [
        *(_interpretar(f"NOME_CONJUGE_{ordem}", nome.strip(), pagina, "ATO_NARRATIVO_EXPLICITO")
          for ordem, nome in enumerate(nomes, 1)),
        _interpretar("DATA_CASAMENTO", m[2].rstrip("."), pagina, "ATO_NARRATIVO_EXPLICITO"),
    ]


def extrair_certidao_casamento(documento):
    dados = {c: [] for c in CAMPOS_CASAMENTO}
    averbacao, categoria, pagina_titulo = False, None, None
    for pagina in documento["paginas"]:
        numero = pagina["numero"]
        for linhas in _grupos(pagina):
            conjuge, filiacao = None, False
            for texto, caixa in linhas:
                rotulo, valor = _rotulo(texto)
                normalizado = normalizar_rotulo(texto)
                if not averbacao and rotulo in TITULOS_CASAMENTO and pagina_titulo is None:
                    pagina_titulo = numero
                    if rotulo == "CERTIDAO DE CASAMENTO COM AVERBACAO":
                        dados["AVERBACAO"].append(_registro("AVERBACAO", "ENCONTRADO", numero,
                                                           "PRESENTE", "TITULO_EXPLICITO"))
                if _RE_AVERBACAO.match(rotulo) and _AUSENCIA_AVERBACAO.match(normalizar_rotulo(valor)):
                    dados["AVERBACAO"].append(_registro("AVERBACAO", "ENCONTRADO", numero,
                                                       "AUSENCIA_DECLARADA", "ROTULO_EXPLICITO"))
                    continue
                if _RE_AVERBACAO.match(rotulo):
                    averbacao, categoria, conjuge = True, None, None
                    dados["AVERBACAO"].append(_registro("AVERBACAO", "ENCONTRADO", numero, "PRESENTE",
                                                       "SECAO_POSTERIOR_DO_REGISTRO"))
                if averbacao:
                    categorias = [a for a, regra in _CATEGORIAS.items() if re.search(regra, normalizado)]
                    for a in categorias:
                        c = "AVERBACAO_" + a
                        dados[c].append(_registro(c, "ENCONTRADO", numero, "MENCIONADA",
                                                  "SECAO_POSTERIOR_DO_REGISTRO"))
                    if _RE_AVERBACAO.match(rotulo) or rotulo == "TIPO DE AVERBACAO":
                        categoria = categorias[0] if len(categorias) == 1 else None
                    if rotulo in {"DATA DA AVERBACAO", "DATA DE AVERBACAO"}:
                        c = "DATA_AVERBACAO_" + (categoria or "SEM_TIPO")
                        dados[c].append(_interpretar(c, valor, numero, "DATA_ROTULADA_DA_AVERBACAO"))
                    if c := _RETIFICADOS.get(rotulo):
                        dados[c].append(_interpretar(c, valor, numero, "RETIFICACAO_ROTULADA"))
                    if m := _RE_CAMPO_CONJUGE.fullmatch(rotulo):
                        if m[1] in {"NOVO NOME", "NOME AVERBADO", "NOME RETIFICADO"}:
                            c = "NOME_AVERBADO_CONJUGE_" + str(_ordem(m[2], m[3]))
                            dados[c].append(_interpretar(c, valor, numero, "NOME_AVERBADO_EXPLICITO"))
                    continue
                if _RE_TERCEIROS.match(rotulo):
                    conjuge = None
                    continue
                if ((rotulo in {"FILIACAO", "PAIS", "GENITORES", "DADOS DOS PAIS"} and not valor)
                        or rotulo in {"CPF DO PAI", "CPF DA MAE"}):
                    filiacao = True
                    continue
                if m := _RE_CONJUGE.fullmatch(rotulo):
                    conjuge, filiacao = _ordem(*m.groups()), False
                    if valor:
                        c = f"NOME_CONJUGE_{conjuge}"
                        dados[c].append(_interpretar(c, valor, numero, "ROTULO_EXPLICITO"))
                    continue
                campo = _campo(rotulo, _REGISTRO)
                if m := _RE_CAMPO_CONJUGE.fullmatch(rotulo):
                    if base := _campo(m[1], _PESSOA):
                        campo = f"{base}_CONJUGE_{_ordem(m[2], m[3])}"
                elif conjuge and (base := _campo(rotulo, _PESSOA)):
                    if not filiacao or base in {"PAI", "MAE", "FILIACAO"}:
                        campo = f"{base}_CONJUGE_{conjuge}"
                    if base in {"PAI", "MAE", "FILIACAO"}:
                        filiacao = True
                if campo:
                    evidencia = "ROTULO_EXPLICITO" if valor or not caixa else "ROTULO_E_GEOMETRIA"
                    dados[campo].append(_interpretar(campo, valor or _alinhado(caixa, linhas), numero, evidencia))
                else:
                    for registro in _narrar(texto, numero):
                        dados[registro["campo"]].append(registro)
    dados["RELACAO_DECLARADA"].append(_registro("RELACAO_DECLARADA", "ENCONTRADO", pagina_titulo,
                                               "REGISTRO DE CASAMENTO", "TIPO_DOCUMENTAL"))
    campos = {c: _resolver(c, registros) for c, registros in dados.items()}
    cpfs = [campos[f"CPF_CONJUGE_{ordem}"]["valor"] for ordem in (1, 2)]
    if cpfs[0] and cpfs[0] == cpfs[1]:
        for ordem in (1, 2):
            c = f"CPF_CONJUGE_{ordem}"
            campos[c] = _registro(c, "AMBIGUO", campos[c]["pagina_origem"], evidencia="CPF_REPETIDO_ENTRE_CONJUGES")
    return campos
