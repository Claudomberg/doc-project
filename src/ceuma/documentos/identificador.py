"""Identificação do tipo documental por evidências, sem validar autenticidade."""

import re
import unicodedata
from difflib import SequenceMatcher

from ceuma.documentos.certidao_casamento import avaliar_casamento
from ceuma.documentos.solicitacao_ctps import avaliar_comprovantes_ctps
from ceuma.documentos.contrato_trabalho import avaliar_contratos_trabalho
from ceuma.documentos.ctps_fisica import avaliar_tipos_trabalhistas
from ceuma.documentos.declaracao_vinculo import avaliar_declaracoes_trabalhistas


_RG = "DOCUMENTO_IDENTIDADE"
_CIN = "CIN"
_CNH = "CNH"
_NASCIMENTO = "CERTIDAO_NASCIMENTO"
_DESCONHECIDO = "DOCUMENTO_DESCONHECIDO"

_TITULOS = {
    _RG: ("CARTEIRA DE IDENTIDADE", "CARTEIRA DE IDENTIDADE CIVIL", "CEDULA DE IDENTIDADE"),
    _CIN: ("CARTEIRA DE IDENTIDADE NACIONAL", "CARTEIRA DE IDENTIDADE CIN", "IDENTIDADE NACIONAL"),
    _CNH: ("CARTEIRA NACIONAL DE HABILITACAO", "PERMISSAO PARA DIRIGIR"),
    _NASCIMENTO: ("CERTIDAO DE NASCIMENTO", "CERTIDAO DO REGISTRO DE NASCIMENTO",
                  "REGISTRO DE NASCIMENTO"),
}
_SUBTITULOS = (
    "DRIVER LICENSE", "DRIVING LICENSE", "PERMISO DE CONDUCCION",
    "DRIVER LICENSE PERMISO DE CONDUCCION", "NATIONAL IDENTITY CARD",
    "CNH", "CIN", "INTEIRO TEOR", "BREVE RELATO", "SEGUNDA VIA",
)
_ROTULO_REGISTRO_CPF = re.compile(
    r"(?<!\w)(?:REGISTRO[ \t]+GERAL[ /:().–—-]+C\.?P\.?F\.?\)?|"
    r"C\.?P\.?F\.?[ /:().–—-]+REGISTRO[ \t]+GERAL\)?)(?!\w)"
)

# Referência para o diagnóstico local. A identificação dos quatro tipos usa
# combinações de sinais. Casamento e vínculo mantêm a pontuação anterior.
REGRAS_DOCUMENTOS = {
    **{tipo: dict.fromkeys(titulos, 5) for tipo, titulos in _TITULOS.items()},
    "CERTIDAO_CASAMENTO": {"CERTIDAO DE CASAMENTO": 5, "REGISTRO DE CASAMENTO": 4},
    "VINCULO_TRABALHISTA": {
        "CARTEIRA DE TRABALHO": 5,
        "CONTRATO DE TRABALHO": 5,
        "DECLARACAO DE VINCULO EMPREGATICIO": 5,
        "CTPS": 4,
    },
}


def _texto_sem_acentos(texto):
    texto = unicodedata.normalize("NFKD", texto).upper()
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\bDE\s*[IL1]DENTIDADE\b", "DE IDENTIDADE", texto)


def normalizar_texto(texto):
    """Normaliza texto para o diagnóstico e as regras legadas."""
    return " ".join(_texto_sem_acentos(texto).split())


def _palavras_de_titulo(linha):
    palavras = re.findall(r"[A-Z0-9]+", linha)
    return tuple(
        p.replace("0", "O").replace("1", "I") if len(p) >= 5 else p
        for p in palavras
    )


def _candidatos_a_titulo(linhas):
    # Permite cabeçalhos quebrados em até três linhas e subtítulos conhecidos.
    palavras = [_palavras_de_titulo(linha) for linha in linhas]
    candidatos = set()
    for indice in range(len(palavras)):
        frase = ()
        for linha in palavras[indice:indice + 3]:
            frase += linha
            if len(frase) > 14:
                break
            candidatos.add(frase)
    return candidatos


def _titulo_encontrado(candidatos, titulos):
    subtitulos = {(), *(tuple(s.split()) for s in _SUBTITULOS)}
    for titulo in titulos:
        esperado = tuple(titulo.split())
        tamanho = len(esperado)
        for candidato in candidatos:
            restante = candidato[tamanho:]
            # Fragmentos de até 2 caracteres após o título são ruído de bordas e brasões.
            ruido = all(len(parte) <= 2 for parte in restante)
            if len(candidato) < tamanho or (restante not in subtitulos and not ruido):
                continue
            pares = [(a, b) for a, b in zip(candidato, esperado) if a != b]
            if len(pares) <= 2 and all(
                len(b) >= 5 and (a[:1] == b[:1] or {a[:1], b[:1]} <= {"I", "L"})
                and SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.84
                for a, b in pares
            ):
                return titulo
    return None


def _bloco_bilingue_cin(corpo):
    """Reconhece os rótulos do cartão quando o título não foi recuperado."""
    return all(re.search(padrao, corpo, re.MULTILINE) for padrao in (
        r"^REGISTRO GERAL[ /:().–—-]+C\.?P\.?F\.?[ \t]*/[ \t]*PERSONAL NUMBER\b",
        r"^NOME[ \t]*/[ \t]*NAME\b",
        r"^(?:DATA DE )?NASCIMENTO[ \t]*/[ \t]*(?:DATE OF BIRTH|BIRTH DATE)\b",
        r"^(?:DATA DE )?VALIDADE[ \t]*/[ \t]*(?:EXPIRY|EXPIRATION DATE)\b",
    ))


def _avaliar_identidades(candidatos, corpo, rotulos_pessoais):
    # O OCR pode devolver as duas palavras do rótulo em linhas consecutivas.
    corpo = re.sub(r"(?m)^REGISTRO\nGERAL(?=[: .-]|$)", "REGISTRO GERAL", corpo)
    titulo_rg = _titulo_encontrado(candidatos, _TITULOS[_RG])
    titulo_cin = _titulo_encontrado(candidatos, _TITULOS[_CIN])
    emissor = bool(re.search(
        r"(?m)^(?:INSTITUTO DE IDENTIFICACAO|POLICIA CIVIL|"
        r"SECRETARIA (?:DE ESTADO )?(?:DA |DE )?SEGURANCA PUBLICA)\b", corpo,
    ))
    rotulo_rg = bool(re.search(r"(?m)^(?:R\.?\s*G\.?|REGISTRO GERAL)(?=[: .-]|$)", corpo))
    numero_rg_distinto = bool(re.search(
        r"(?m)^(?:R\.?\s*G\.?|REGISTRO GERAL)[ :.-]*\d", corpo,
    ))
    registro_cpf = any(_ROTULO_REGISTRO_CPF.match(linha) for linha in corpo.splitlines())
    # O verso do RG pode trazer a frase de validade no lugar do título e do emissor.
    validade_nacional = re.search(r"(?m)^VALIDA EM TODO O TERRITORIO NACIONAL[.!]?$", corpo)
    numero_no_verso = re.search(
        r"(?m)^REGISTRO GERAL[ :.-]*\n?[ ]*([0-9][0-9. -]*[0-9X])[ ]*$", corpo,
    )
    verso_rg = bool(
        validade_nacional and numero_no_verso and not registro_cpf and rotulos_pessoais
        and 5 <= sum(c.isdecimal() for c in numero_no_verso[1]) <= 10
    )
    codigo_cin = ("CIN",) in candidatos
    tem_cpf = bool(re.search(r"(?m)^CPF\b", corpo))
    bloco_bilingue = (
        emissor and registro_cpf and not numero_rg_distinto and _bloco_bilingue_cin(corpo)
    )

    evidencias_rg = [
        sinal for sinal in (
            titulo_rg, "ORGAO DE IDENTIFICACAO" if emissor else None,
            "VALIDADE EM TERRITORIO NACIONAL" if verso_rg or validade_nacional else None,
            "REGISTRO GERAL" if numero_rg_distinto or (rotulo_rg and not registro_cpf) else None,
            "CAMPOS DE IDENTIFICACAO" if rotulos_pessoais else None,
        ) if sinal
    ]
    evidencias_cin = [
        sinal for sinal in (
            titulo_cin, "REGISTRO GERAL VINCULADO AO CPF" if registro_cpf else None,
            "CIN" if codigo_cin else None,
            "BLOCO BILINGUE DA CIN" if bloco_bilingue else None,
            "CAMPOS DE IDENTIFICACAO" if rotulos_pessoais or tem_cpf else None,
        ) if sinal
    ]

    rg = bool(
        (titulo_rg and (rotulo_rg or (emissor and rotulos_pessoais)))
        or (emissor and rotulo_rg and not registro_cpf and rotulos_pessoais)
        or verso_rg
        # Em fotos, "REGISTRO GERAL" e o órgão emissor saem em letra miúda e costumam se perder.
        or (titulo_rg and validade_nacional and rotulos_pessoais and not registro_cpf)
    )
    cin = bool(
        (titulo_cin and (registro_cpf or tem_cpf or rotulos_pessoais))
        or (titulo_rg and registro_cpf)
        or (codigo_cin and registro_cpf and rotulos_pessoais)
        or bloco_bilingue
    )
    # A CIN pode repetir o cabeçalho genérico e o próprio rótulo Registro Geral.
    # Um número de RG separado em um bloco com título próprio mantém a disputa.
    if cin and not (titulo_rg and numero_rg_distinto):
        rg = False
    indicios_rg = titulo_rg or rotulo_rg or emissor
    indicios_cin = titulo_cin or registro_cpf or codigo_cin
    return {
        _RG: (rg, evidencias_rg if indicios_rg else []),
        _CIN: (cin, evidencias_cin if indicios_cin else []),
    }


def _avaliar_cnh(candidatos, corpo, rotulos_pessoais):
    titulo = _titulo_encontrado(candidatos, _TITULOS[_CNH])
    categoria = bool(re.search(r"\bCAT(?:EGORIA)?[. ]+HAB\b|\bCATEGORIA\s*:\s*[A-E]\b", corpo))
    habilitacao = bool(re.search(r"\b(?:PRIMEIRA|1\s*[A.]?)\s+HABILITACAO\b", corpo))
    registro = bool(re.search(
        r"(?m)^(?:5[. ):-]+)?(?:N(?:UMERO|[O.º° ]{0,3})\s+(?:DE )?REGISTRO|REGISTRO DA CNH)\b",
        corpo,
    ))
    emissor = bool(re.search(r"\b(?:DETRAN|SENATRAN|DENATRAN|DEPARTAMENTO NACIONAL DE TRANSITO)\b", corpo))
    evidencias = [
        sinal for sinal in (
            titulo, "CATEGORIA HAB" if categoria else None,
            "PRIMEIRA HABILITACAO" if habilitacao else None,
            "NUMERO DE REGISTRO" if registro else None,
            "ORGAO DE TRANSITO" if emissor else None,
        ) if sinal
    ]
    identificado = bool((titulo and (categoria or habilitacao or registro or (emissor and rotulos_pessoais)))
                        or (categoria and habilitacao)
                        or (registro and emissor and (categoria or habilitacao)))
    return identificado, evidencias


def _avaliar_nascimento(candidatos, corpo):
    titulo = _titulo_encontrado(candidatos, _TITULOS[_NASCIMENTO])
    registro_civil = bool(re.search(r"\bREGISTRO CIVIL(?: DAS PESSOAS NATURAIS)?\b", corpo))
    matricula = bool(re.search(r"(?m)^MATRICULA\b", corpo))
    assento = all(re.search(rf"\b{palavra}\b", corpo) for palavra in ("LIVRO", "FOLHA", "TERMO"))
    ocorrido = bool(re.search(r"\b(?:NASCEU|NASCID[OA] (?:EM|AOS)|ASSENTO DE NASCIMENTO)\b", corpo))
    evidencias = [
        sinal for sinal in (
            titulo, "REGISTRO CIVIL" if registro_civil else None,
            "MATRICULA" if matricula else None, "LIVRO FOLHA TERMO" if assento else None,
            "RELATO DO NASCIMENTO" if ocorrido else None,
        ) if sinal
    ]
    certidao_casamento = _titulo_encontrado(candidatos, ("CERTIDAO DE CASAMENTO", "REGISTRO DE CASAMENTO"))
    identificado = bool(
        (titulo and (registro_civil or matricula or assento or ocorrido))
        or (not certidao_casamento and registro_civil and ocorrido and (matricula or assento))
    )
    return identificado, evidencias


def _resultado(tipo, status, evidencias, pontuacao):
    return {"tipo": tipo, "pontuacao": pontuacao, "status": status, "evidencias": evidencias}


def identificar_documento(texto, *, exigir_evidencia_casamento=False):
    normalizado = _texto_sem_acentos(texto)
    normalizado = re.sub(r"(?<=[A-Z])-\s*\n\s*(?=[A-Z])", "", normalizado)
    linhas = [limpa for linha in normalizado.splitlines() if (limpa := " ".join(linha.split()))]
    corpo = "\n".join(linhas)
    candidatos = _candidatos_a_titulo(linhas)
    rotulos_pessoais = bool(re.search(r"(?m)^(?:NOME|FILIACAO|DATA DE NASCIMENTO|NASCIMENTO)\b", corpo))
    casamento = avaliar_casamento(linhas)
    comprovantes_ctps = avaliar_comprovantes_ctps(linhas)
    declaracoes = avaliar_declaracoes_trabalhistas(linhas)
    carteiras = avaliar_tipos_trabalhistas(linhas)
    contratos = avaliar_contratos_trabalho(linhas, contexto_ctps=any(ok for ok, _ in carteiras.values()))
    avaliacoes = {
        **_avaliar_identidades(candidatos, corpo, rotulos_pessoais),
        _CNH: _avaliar_cnh(candidatos, corpo, rotulos_pessoais),
        _NASCIMENTO: _avaliar_nascimento(candidatos, corpo),
        **carteiras,
        **comprovantes_ctps,
        **declaracoes,
        **contratos,
        **casamento,
    }
    confirmados = [tipo for tipo, (identificado, _) in avaliacoes.items() if identificado]
    if len(confirmados) > 1:
        resultado = _resultado(_DESCONHECIDO, "AMBIGUO", [],
                               2 * max(len(avaliacoes[tipo][1]) for tipo in confirmados))
        if _RG in confirmados or _CIN in confirmados:
            resultado["modelo_identidade"] = "IDENTIDADE_MODELO_INDETERMINADO"
        if any(tipo in comprovantes_ctps or tipo in declaracoes or tipo in contratos or tipo in casamento for tipo in confirmados):
            resultado["revisao_manual"] = True
        return resultado
    if confirmados:
        tipo, = confirmados
        evidencias = avaliacoes[tipo][1]
        resultado = _resultado(tipo, "IDENTIFICADO", evidencias, 2 * len(evidencias))
        if tipo in {_RG, _CIN}:
            resultado["modelo_identidade"] = "RG_TRADICIONAL" if tipo == _RG else "CIN"
        if ((tipo in declaracoes and tipo != "DECLARACAO_VINCULO")
                or (tipo in contratos and tipo != "CONTRATO_TRABALHO_AVULSO")
                or (tipo in casamento and tipo != "CERTIDAO_CASAMENTO")):
            resultado["revisao_manual"] = True
        return resultado

    if linhas == ["CERTIDAO DE CASAMENTO"] and not exigir_evidencia_casamento:
        # Compatibilidade com a identificação de título dos chamadores antigos.
        return _resultado("CERTIDAO_CASAMENTO", "IDENTIFICADO", ["CERTIDAO DE CASAMENTO"], 5)
    indicios_comprovante = [
        sinal for familia in (comprovantes_ctps, declaracoes, casamento,
                        contratos if linhas != ["CONTRATO DE TRABALHO"] else {})
        for _, sinais in familia.values() for sinal in sinais
    ]
    if indicios_comprovante:
        return {**_resultado(_DESCONHECIDO, "EVIDENCIA_INSUFICIENTE", indicios_comprovante, 2),
                "revisao_manual": True}

    # Preserva a identificação legada fora do escopo, sem criar validações novas.
    texto_linear = " ".join(linhas)
    legados = {}
    for tipo in ("VINCULO_TRABALHISTA",):
        regras = REGRAS_DOCUMENTOS[tipo]
        if tipo == "VINCULO_TRABALHISTA":
            evidencias = [sinal for sinal in ("CONTRATO DE TRABALHO",) if sinal in linhas]
            if not evidencias and re.search(r"\b(?:CTPS|CARTEIRA DE TRABALHO)\b", texto_linear):
                evidencias = ["CTPS"]
        else:
            evidencias = [sinal for sinal in regras if sinal in texto_linear]
        legados[tipo] = sum(regras[sinal] for sinal in evidencias), evidencias
    maior = max(pontos for pontos, _ in legados.values())
    if maior:
        melhores = [tipo for tipo, (pontos, _) in legados.items() if pontos == maior]
        if len(melhores) > 1:
            return _resultado(_DESCONHECIDO, "AMBIGUO", [], maior)
        tipo, = melhores
        evidencias = legados[tipo][1]
        if maior >= 5:
            return _resultado(tipo, "IDENTIFICADO", evidencias, maior)
        return _resultado(_DESCONHECIDO, "EVIDENCIA_INSUFICIENTE", evidencias, maior)

    indicios = list(dict.fromkeys(
        sinal for _, evidencias in avaliacoes.values() for sinal in evidencias
    ))
    status = "EVIDENCIA_INSUFICIENTE" if indicios else "NAO_IDENTIFICADO"
    resultado = _resultado(_DESCONHECIDO, status, indicios, 2 * len(indicios))
    if any(_titulo_encontrado(candidatos, _TITULOS[tipo]) for tipo in (_RG, _CIN)):
        resultado["modelo_identidade"] = "IDENTIDADE_MODELO_INDETERMINADO"
    return resultado
