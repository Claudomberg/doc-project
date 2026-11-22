"""Regras genéricas para comprovantes de solicitação da CTPS."""
import re
from datetime import datetime

from analise.associador_espacial import _caixa
from analise.extrator_campos import _partes_leitura, normalizar_rotulo, validar_cpf, validar_data


_ROTULOS = {
    "NOME": r"NOME DO SOLICITANTE|SOLICITANTE|NOME",
    "CPF": r"CPF DO SOLICITANTE|CPF",
    "NUMERO_PROTOCOLO": r"PROTOCOLO(?: DE SOLICITACAO| DE ATENDIMENTO)?|(?:NUMERO|N[º°.]?) (?:DO )?PROTOCOLO|NUMERO DA SOLICITACAO",
    "DATA_SOLICITACAO": r"DATA (?:DA|DE) SOLICITACAO|SOLICITADO EM",
    "DATA_ATENDIMENTO": r"DATA (?:DO|DE) ATENDIMENTO|ATENDIDO EM",
    "DATA_AGENDAMENTO": r"DATA (?:DO|DE) AGENDAMENTO|AGENDADO PARA",
    "DATA_EMISSAO_COMPROVANTE": r"DATA (?:DA|DE) EMISSAO DO COMPROVANTE|COMPROVANTE EMITIDO EM",
    "TIPO_SOLICITACAO": r"TIPO (?:DA|DE) SOLICITACAO|TIPO DE PEDIDO",
    "ORGAO_EMISSOR": r"ORGAO EMISSOR|ENTIDADE EMISSORA|EMISSOR DO COMPROVANTE",
    "SITUACAO_SOLICITACAO": r"SITUACAO(?: DA SOLICITACAO| DO PEDIDO)?|STATUS(?: DA SOLICITACAO| DO PEDIDO)?",
}
CAMPOS_SOLICITACAO_CTPS = tuple(_ROTULOS)

_CTPS = r"(?:CTPS|CARTEIRA DE TRABALHO(?: E PREVIDENCIA SOCIAL)?)"
_TITULOS = {
    "SOLICITACAO_CTPS": r"COMPROVANTE DE SOLICITACAO",
    "PROTOCOLO_ATENDIMENTO_CTPS": r"PROTOCOLO DE ATENDIMENTO",
    "AGENDAMENTO_CTPS": r"COMPROVANTE DE AGENDAMENTO",
    "EMISSAO_CTPS": r"COMPROVANTE DE EMISSAO",
}
_SECAO = {"DADOS DO SOLICITANTE", "IDENTIFICACAO DO SOLICITANTE"}
_OUTROS = re.compile(r"^(?:DADOS (?:DO|DA) )?(?:ATENDENTE|RESPONSAVEL|REPRESENTANTE|EMPREGADOR|TERCEIRO)\b"
                     r"|^(?:NOME|CPF) (?:DO|DA) (?:ATENDENTE|RESPONSAVEL|REPRESENTANTE|EMPREGADOR|TERCEIRO)\b")
_SERVICO = re.compile(rf"(?:SERVICO|ASSUNTO|FINALIDADE)\s*:\s*(?:(?:SOLICITACAO|EMISSAO|AGENDAMENTO) (?:DA |DE )?)?{_CTPS}")
_PROTOCOLO = re.compile(r"(?=[A-Z0-9./-]{1,48}$)(?=.*[0-9])[A-Z0-9]+(?:[./-][A-Z0-9]+)*")
_DATA = re.compile(r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}")


def _campo(texto):
    for chave, regra in _ROTULOS.items():
        if m := re.fullmatch(rf"({regra})(?:\s*:\s*(.*))?", texto):
            return chave, m[1], m[2] or ""
    return None, "", ""


def _titulo(texto, titulo, *, servico):
    return bool(re.fullmatch(rf"{titulo} (?:DA|DE) {_CTPS}", texto)
                or (servico and texto == titulo))


def avaliar_comprovantes_ctps(linhas):
    servico = any(_SERVICO.fullmatch(t) for t in linhas)
    campos = [_campo(t) for t in linhas]
    protocolo = any(c == "NUMERO_PROTOCOLO" and _PROTOCOLO.fullmatch(v) for c, _, v in campos)
    data_finalidade = {
        "SOLICITACAO_CTPS": "DATA_SOLICITACAO",
        "PROTOCOLO_ATENDIMENTO_CTPS": "DATA_ATENDIMENTO",
        "AGENDAMENTO_CTPS": "DATA_AGENDAMENTO",
    }
    data_emissao_ctps = any(
        _DATA.fullmatch(m[1]) and validar_data(m[1])
        for t in linhas
        if (m := re.fullmatch(rf"DATA (?:DA|DE) EMISSAO (?:DA|DE) {_CTPS}\s*:\s*(.+)", t))
    )
    avaliacoes = {}
    for tipo, titulo in _TITULOS.items():
        cabecalho = any(_titulo(t, titulo, servico=servico) for t in linhas)
        data_confirmada = any(c == data_finalidade.get(tipo) and _DATA.fullmatch(v) and validar_data(v)
                             for c, _, v in campos)
        confirmada = cabecalho and (protocolo or data_confirmada or (tipo == "EMISSAO_CTPS" and data_emissao_ctps))
        avaliacoes[tipo] = (confirmada, [titulo] if cabecalho else [])
    return avaliacoes


def _grupos(pagina):
    partes = _partes_leitura(pagina)
    if not pagina.get("grupos_leitura") and pagina.get("linhas"):
        partes = [(pagina["texto"], pagina["linhas"])]
    for texto, linhas in partes:
        if linhas:
            yield [(normalizar_rotulo(l["texto"]).strip(), _caixa(l)) for l in linhas]
        else:
            yield [(normalizar_rotulo(t).strip(), None) for t in texto.splitlines() if t.strip()]


def _valor_alinhado(caixa, linhas):
    if caixa is None:
        return ""
    _, y0, x1, y1 = caixa
    alinhados = [
        (t, c) for t, c in linhas if c and 0 <= c[0] - x1 <= 100
        and min(y1, c[3]) - max(y0, c[1]) >= .5 * min(y1 - y0, c[3] - c[1])
    ]
    barreira = min((c[0] for t, c in alinhados if _campo(t)[0] or _OUTROS.match(t)),
                   default=float("inf"))
    candidatos = [t for t, c in alinhados if c[2] <= barreira and c[0] < barreira]
    return candidatos[0] if len(candidatos) == 1 else (None if candidatos else "")


def _registro(campo, status, pagina=None, valor=None, evidencia=None):
    return {"campo": campo, "status": status, "pagina_origem": pagina,
            "valor": valor, "trecho_origem": evidencia}


def _interpretar(campo, valor, pagina, evidencia):
    status = "ENCONTRADO"
    if valor is None:
        status = "AMBIGUO"
    elif not valor or re.fullmatch(r"[-_./ ]+", valor) or "ILEGIVEL" in valor or "?" in valor:
        status = "EVIDENCIA_INSUFICIENTE"
    elif campo == "CPF":
        if (re.fullmatch(r"[0-9]{3}[. ]?[0-9]{3}[. ]?[0-9]{3}[- ]?[0-9]{2}", valor)
                and validar_cpf(valor)):
            valor = re.sub(r"\D", "", valor)
        else:
            status = "INVALIDO"
    elif campo.startswith("DATA_"):
        if _DATA.fullmatch(valor) and validar_data(valor):
            valor = datetime.strptime(valor, "%d/%m/%Y").strftime("%d/%m/%Y")
        else:
            status = "INVALIDO"
    elif campo == "NUMERO_PROTOCOLO" and not _PROTOCOLO.fullmatch(valor):
        status = "INVALIDO"
    elif campo == "NOME" and not re.fullmatch(r"[A-Z][A-Z .'-]{2,70}", valor):
        status = "EVIDENCIA_INSUFICIENTE"
    return _registro(campo, status, pagina, valor if status == "ENCONTRADO" else None, evidencia)


def _resolver(campo, registros):
    if not registros:
        return _registro(campo, "NAO_ENCONTRADO")
    uteis = [r for r in registros if r["status"] != "EVIDENCIA_INSUFICIENTE"]
    if len({(r["status"], r["valor"]) for r in uteis}) > 1:
        return _registro(campo, "AMBIGUO", uteis[0]["pagina_origem"], evidencia="EVIDENCIAS_CONFLITANTES")
    return (uteis or registros)[0]


def extrair_comprovante_solicitacao_ctps(documento):
    candidatos = {c: [] for c in CAMPOS_SOLICITACAO_CTPS}
    for pagina in documento["paginas"]:
        for linhas in _grupos(pagina):
            secao_solicitante = False
            for texto, caixa in linhas:
                if texto in _SECAO:
                    secao_solicitante = True
                    continue
                if _OUTROS.match(texto):
                    secao_solicitante = False
                    continue
                campo, rotulo, valor = _campo(texto)
                if campo is None:
                    continue
                if campo in {"CPF", "NOME"} and rotulo == campo and not secao_solicitante:
                    candidatos[campo].append(_registro(
                        campo, "EVIDENCIA_INSUFICIENTE", pagina["numero"], evidencia="TITULARIDADE_INCONCLUSIVA",
                    ))
                    continue
                evidencia = "ROTULO_EXPLICITO" if valor else "ROTULO_E_GEOMETRIA" if caixa else "ROTULO_EXPLICITO"
                if not valor:
                    valor = _valor_alinhado(caixa, linhas)
                candidatos[campo].append(_interpretar(campo, valor, pagina["numero"], evidencia))
    return {campo: _resolver(campo, registros) for campo, registros in candidatos.items()}
