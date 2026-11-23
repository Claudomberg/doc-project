
import re


_PADRAO_CPF = re.compile(r"(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}(?!\d)")
STATUS_CPF_TITULAR = "TITULAR_CONFIRMADO"

_PAPEIS_CPF = {
    "TITULAR": r"TITULAR",
    "RESPONSAVEL": r"RESPONSAVEL(?: LEGAL)?|REPRESENTANTE(?: LEGAL)?|TUTOR(?:A)?|CURADOR(?:A)?",
    "FILIACAO": r"FILIACAO|PAI|MAE|GENITOR(?:A|ES)?",
    "OUTRO_PARTICIPANTE": r"DECLARANTE|CONJUGE|COMPANHEIR[OA]|TESTEMUNHA|PROCURADOR(?:A)?",
}
_ROTULOS_CPF_POR_PAPEL = {
    papel: re.compile(
        rf"(?:\bCPF\s*[:\-]?\s*(?:(?:DO|DA|DE)\s+)?(?:{rotulos})"
        rf"|(?:^|[;\n])\s*(?:{rotulos})\s*[:\-]?\s*CPF)\s*[:\-]?\s*\Z"
    )
    for papel, rotulos in _PAPEIS_CPF.items()
}


def validar_cpf(cpf):
    """Verifica os dígitos verificadores de um CPF."""

    numeros = re.sub(r"\D", "", str(cpf))

    if len(numeros) != 11:
        return False

    if len(set(numeros)) == 1:
        return False

    for tamanho in (9, 10):
        soma = sum(
            int(numeros[indice]) * (tamanho + 1 - indice)
            for indice in range(tamanho)
        )

        resto = (soma * 10) % 11

        if resto == 10:
            resto = 0

        if resto != int(numeros[tamanho]):
            return False

    return True



def extrair_cpfs(texto):
    """Localiza possíveis CPFs e verifica sua validade."""

    encontrados = _PADRAO_CPF.findall(texto)

    resultados = []
    vistos = set()

    for cpf in encontrados:
        numeros = re.sub(r"\D", "", cpf)

        if numeros in vistos:
            continue

        vistos.add(numeros)

        resultados.append({
            "valor": numeros,
            "valido": validar_cpf(numeros)
        })

    return resultados


def _partes_leitura(pagina):
    """Mantém separados os referenciais de leitura de uma página mista."""
    if grupos := pagina.get("grupos_leitura"):
        return [("\n".join(linha["texto"] for linha in grupo), grupo) for grupo in grupos]
    return [(pagina["texto"], pagina.get("linhas_ocr") or [])]


def extrair_cpfs_documento(documento):
    """Localiza CPFs por página e atribui papel por rótulo explícito."""
    resultados = []
    evidencias = {}
    for pagina in documento["paginas"]:
        cpfs_pagina = set()
        for trecho, _ in _partes_leitura(pagina):
            texto = normalizar_rotulo(trecho)
            fim_anterior = 0
            for ocorrencia in _PADRAO_CPF.finditer(texto):
                valor = re.sub(r"\D", "", ocorrencia.group())
                prefixo = "\n".join(texto[fim_anterior:ocorrencia.start()].rsplit("\n", 3)[-3:])
                papeis = evidencias.setdefault(valor, set())
                papeis.update(papel for papel, rotulo in _ROTULOS_CPF_POR_PAPEL.items()
                              if rotulo.search(prefixo))
                fim_anterior = ocorrencia.end()
                if valor in cpfs_pagina:
                    continue
                cpfs_pagina.add(valor)
                resultados.append({
                    "campo": "CPF",
                    "valor": valor,
                    "valido": validar_cpf(valor),
                    "pagina_origem": pagina["numero"],
                    "metodo_leitura": pagina["metodo"]
                })

    titulares = {cpf["valor"] for cpf in resultados
                 if cpf["valido"] and "TITULAR" in evidencias[cpf["valor"]]}
    for cpf in resultados:
        papeis = evidencias[cpf["valor"]]
        papel = next(iter(papeis)) if len(papeis) == 1 else "AMBIGUO"
        cpf["papel"] = "AMBIGUO" if papel == "TITULAR" and len(titulares) > 1 else papel
    return resultados


from datetime import datetime


def validar_data(data):
    """Verifica se uma data existe no calendário."""

    try:
        datetime.strptime(data, "%d/%m/%Y")
        return True
    except ValueError:
        return False


def extrair_datas(texto):
    """Localiza datas no formato brasileiro."""

    padrao = r"(?<!\d)\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}(?!\d)"

    encontradas = re.findall(padrao, texto)

    resultados = []
    vistos = set()

    for data in encontradas:
        data_normalizada = re.sub(r"[.\-]", "/", data)

        partes = data_normalizada.split("/")

        dia = partes[0].zfill(2)
        mes = partes[1].zfill(2)
        ano = partes[2]

        data_formatada = f"{dia}/{mes}/{ano}"

        if data_formatada in vistos:
            continue

        vistos.add(data_formatada)

        resultados.append({
            "valor": data_formatada,
            "valida": validar_data(data_formatada)
        })

    return resultados


def extrair_datas_documento(documento):
    """Localiza datas e preserva suas páginas de origem."""

    resultados = []

    for pagina in documento["paginas"]:
        datas = extrair_datas(pagina["texto"])

        for data in datas:
            resultados.append({
                "campo": "DATA_NAO_CLASSIFICADA",
                "valor": data["valor"],
                "valida": data["valida"],
                "pagina_origem": pagina["numero"],
                "metodo_leitura": pagina["metodo"]
            })

    return resultados


import unicodedata


def normalizar_rotulo(texto):
    """Remove acentos e padroniza o texto."""
    texto = unicodedata.normalize("NFD", texto.upper())
    return "".join(
        caractere for caractere in texto
        if unicodedata.category(caractere) != "Mn"
    )


def identificar_tipo_data(texto):
    """Classifica uma data apenas quando há um rótulo inequívoco."""
    rotulos = {
        "DATA_NASCIMENTO": (
            r"\bDATA\s+(?:DE\s+)?NASCIMENTO\b",
            r"\bNASCIMENTO\b",
            r"\bDT\.?\s*NASC\.?\b",
            r"\bDATA\s+NASC\.?\b",
        ),
        "DATA_EXPEDICAO": (
            r"\bDATA\s+(?:DE\s+)?EXPEDICAO\b",
            r"\bEXPEDICAO\b",
            r"\bEMISSAO\b",
            r"\bDATA\s+(?:DE\s+)?EMISSAO\b",
        ),
        "DATA_VALIDADE": (
            r"\bVALIDADE\b",
            r"\bVENCIMENTO\b",
        ),
    }
    normalizado = normalizar_rotulo(texto)
    tipos = {
        tipo
        for tipo, padroes in rotulos.items()
        if any(re.search(padrao, normalizado) for padrao in padroes)
    }
    return next(iter(tipos)) if len(tipos) == 1 else "DATA_NAO_CLASSIFICADA"


def _rotulo_data_isolado(linha):
    """Aceita o rótulo na linha anterior somente quando ela contém apenas o rótulo."""
    normalizada = normalizar_rotulo(linha).strip().rstrip(":- ")
    padroes = (
        r"(?:DATA\s+(?:DE\s+)?)?NASCIMENTO",
        r"(?:DT\.?\s*|DATA\s+)NASC\.?",
        r"(?:DATA\s+(?:DE\s+)?)?EXPEDICAO",
        r"(?:DATA\s+(?:DE\s+)?)?EMISSAO",
        r"(?:DATA\s+(?:DE\s+)?)?VALIDADE",
        r"VENCIMENTO",
    )
    return any(re.fullmatch(padrao, normalizada) for padrao in padroes)


def _datas_com_origem(texto):
    """Retorna datas e linhas de origem sem inferir tipo pelo valor da data."""
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    resultados = []
    for indice, linha in enumerate(linhas):
        datas = extrair_datas(linha)
        if not datas:
            continue
        tipo = "DATA_NAO_CLASSIFICADA"
        if len(datas) == 1:
            tipo = identificar_tipo_data(linha)
            if (tipo == "DATA_NAO_CLASSIFICADA" and indice > 0
                    and _rotulo_data_isolado(linhas[indice - 1])):
                tipo = identificar_tipo_data(linhas[indice - 1])
        for data in datas:
            resultados.append({
                "campo": tipo,
                "valor": data["valor"],
                "valida": data["valida"],
                "trecho_origem": linha,
            })
    return resultados


def extrair_datas_classificadas(texto):
    """Classifica datas na mesma linha ou após rótulo isolado."""
    return [
        {"campo": item["campo"], "valor": item["valor"], "valida": item["valida"]}
        for item in _datas_com_origem(texto)
    ]


def _datas_espaciais_pagina(pagina, *, observar_expedicao=None):
    """Classificações geométricas inequívocas, somente para datas já lidas no texto."""
    linhas = pagina.get("linhas_ocr") or []
    if not linhas and observar_expedicao is None:
        return {}
    from ceuma.extracao.associador_espacial import _associar_rotulo_valor

    padrao = r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}"
    categorias = {
        "DATA_NASCIMENTO": ["DATA DE NASCIMENTO", "NASCIMENTO", "DT NASC"],
        "DATA_EXPEDICAO": ["DATA DE EXPEDICAO", "EXPEDICAO", "DATA DE EMISSAO", "EMISSAO"],
        "DATA_VALIDADE": ["DATA DE VALIDADE", "VALIDADE", "VENCIMENTO"],
    }
    propostas = {}
    for campo, rotulos in categorias.items():
        observar = observar_expedicao if campo == "DATA_EXPEDICAO" else None
        resultado = _associar_rotulo_valor(linhas, rotulos, padrao, observar=observar)
        if resultado is None:
            continue
        datas = extrair_datas(resultado["valor"])
        if len(datas) != 1 or not datas[0]["valida"]:
            if observar is not None:
                observar("DATA_INVALIDA")
            continue
        valor = datas[0]["valor"]
        propostas.setdefault(valor, []).append((campo, resultado["trecho_origem"]))
    # Se categorias diferentes apontam para a mesma data, não escolher uma.
    if observar_expedicao is not None:
        for opcoes in propostas.values():
            if any(campo == "DATA_EXPEDICAO" for campo, _ in opcoes):
                if len(opcoes) == 1:
                    observar_expedicao("ASSOCIADO")
                else:
                    observar_expedicao("AMBIGUIDADE_ENTRE_CATEGORIAS",
                                      quantidade=len(opcoes))
    return {valor: opcoes[0] for valor, opcoes in propostas.items() if len(opcoes) == 1}


def extrair_datas_classificadas_documento(documento):
    """Preserva extração textual; complementa apenas datas não classificadas."""
    resultados = []
    for pagina in documento["paginas"]:
        for texto, linhas in _partes_leitura(pagina):
            espaciais = _datas_espaciais_pagina({"linhas_ocr": linhas})
            for data in _datas_com_origem(texto):
                campo = data["campo"]
                trecho = data["trecho_origem"]
                if campo == "DATA_NAO_CLASSIFICADA" and data["valida"]:
                    evidencia = espaciais.get(data["valor"])
                    if evidencia:
                        campo, trecho = evidencia
                resultados.append({
                    "campo": campo,
                    "valor": data["valor"],
                    "valida": data["valida"],
                    "pagina_origem": pagina["numero"],
                    "metodo_leitura": pagina["metodo"],
                    "trecho_origem": trecho,
                })
    return resultados


def extrair_nome_titular(texto):
    """
    Procura o nome do titular usando o rótulo NOME.
    Não confunde automaticamente com NOME DA MÃE
    ou NOME DO PAI.
    """

    linhas = [
        linha.strip()
        for linha in texto.splitlines()
        if linha.strip()
    ]

    rotulos_ignorados = (
        "NOME DA MAE",
        "NOME DO PAI",
        "NOME DOS PAIS",
        "NOME SOCIAL",
        "NOME DO RESPONSAVEL",
    )

    for indice, linha in enumerate(linhas):
        linha_normalizada = normalizar_rotulo(linha)

        if any(
            rotulo in linha_normalizada
            for rotulo in rotulos_ignorados
        ):
            continue

        # Nome na mesma linha do rótulo.
        correspondencia = re.match(
            r"^NOME\s*[:\-]\s*(.+)$",
            linha_normalizada
        )

        if correspondencia:
            nome = correspondencia.group(1).strip()

            if nome and not re.search(r"\d", nome):
                return {
                    "valor": nome,
                    "trecho_origem": linha,
                    "status": "ENCONTRADO"
                }

        # Nome na linha seguinte.
        if re.fullmatch(r"NOME\s*[:\-]?", linha_normalizada):
            if indice + 1 < len(linhas):
                proxima_linha = linhas[indice + 1]
                nome = normalizar_rotulo(proxima_linha).strip()

                if (
                    nome
                    and not re.search(r"\d", nome)
                    and len(nome.split()) >= 2
                ):
                    return {
                        "valor": nome,
                        "trecho_origem": (
                            linha + " | " + proxima_linha
                        ),
                        "status": "ENCONTRADO"
                    }

    return {
        "valor": None,
        "trecho_origem": None,
        "status": "NAO_ENCONTRADO"
    }


def extrair_nome_documento(documento):
    """
    Procura o nome do titular nas páginas do documento
    e registra sua origem.
    """

    for pagina in documento["paginas"]:
        for texto, _ in _partes_leitura(pagina):
            resultado = extrair_nome_titular(texto)

            if resultado["status"] == "ENCONTRADO":
                return {
                    "campo": "NOME",
                    "valor": resultado["valor"],
                    "pagina_origem": pagina["numero"],
                    "metodo_leitura": pagina["metodo"],
                    "trecho_origem": resultado["trecho_origem"],
                    "status": "ENCONTRADO"
                }

    return {
        "campo": "NOME",
        "valor": None,
        "pagina_origem": None,
        "metodo_leitura": None,
        "trecho_origem": None,
        "status": "NAO_ENCONTRADO"
    }


_ROTULOS_RG = ("REGISTRO GERAL", "RG", "R.G.", "R.G")
_ROTULO_RG_TEXTUAL = re.compile(
    r"^(?:REGISTRO GERAL|RG|R\.G\.?)(?!\w)\s*[:\-]?\s*(.*)$"
)
_PADRAO_RG = re.compile(r"(?:\d{1,3}(?:\.\d{3}){1,2}|\d{5,12})-?[0-9Xx]?")
_OUTROS_CAMPOS_RG = re.compile(
    r"\b(?:CPF|C\.P\.F\.?|NOME|FILIACAO|NASCIMENTO|EXPEDICAO|EMISSAO|VALIDADE|VENCIMENTO|"
    r"DATA|DT|MATRICULA|NATURALIDADE|NACIONALIDADE|SEXO|ENDERECO|CEP|CNH|PIS|PASEP|"
    r"DOC(?:UMENTO)?\.?\s+ORIGEM|"
    r"ORGAO (?:EMISSOR|EXPEDIDOR)|ASSINATURA)\b"
)


def _classificar_numero_rg(valor):
    if _PADRAO_CPF.fullmatch(valor) and (not valor.isdecimal() or validar_cpf(valor)):
        return "CPF"
    if _PADRAO_RG.fullmatch(valor):
        return "RG"
    return None


def _resultado_rg(valor=None, trecho=None, status="NAO_ENCONTRADO"):
    return {"valor": valor, "trecho_origem": trecho, "status": status}


def _rgs_textuais(texto, *, permitir_seguinte):
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    candidatos = []
    for indice, linha in enumerate(linhas):
        rotulo = _ROTULO_RG_TEXTUAL.match(normalizar_rotulo(linha))
        if not rotulo:
            continue
        candidato = rotulo.group(1)
        if not candidato and permitir_seguinte and indice + 1 < len(linhas):
            candidato = linhas[indice + 1]
        candidato = normalizar_rotulo(candidato)
        outro_campo = _OUTROS_CAMPOS_RG.search(candidato)
        if outro_campo:
            candidato = candidato[:outro_campo.start()].rstrip(" ,;")
        candidato = candidato.strip()
        if _classificar_numero_rg(candidato) == "RG":
            candidatos.append(_resultado_rg(candidato, linha, "ENCONTRADO"))
        else:
            separados = re.split(r"\s*(?:[,;/]|\bE\b)\s*", candidato)
            if len(separados) > 1 and all(_classificar_numero_rg(n) == "RG" for n in separados):
                candidatos.extend(_resultado_rg(n, linha, "ENCONTRADO") for n in separados)
    return candidatos


def _selecionar_rg(candidatos):
    if any(item["status"] == "AMBIGUO" for item in candidatos):
        return _resultado_rg(status="AMBIGUO")
    if not candidatos:
        return _resultado_rg()
    numeros = {re.sub(r"[.\-]", "", item["valor"].upper()) for item in candidatos}
    return candidatos[0] if len(numeros) == 1 else _resultado_rg(status="AMBIGUO")


def extrair_rg(texto):
    """Extrai somente RG completo com rótulo e sinaliza valores concorrentes."""
    return _selecionar_rg(_rgs_textuais(texto, permitir_seguinte=True))


def _rg_espacial_pagina(linhas, *, observar=None):
    from ceuma.extracao.associador_espacial import (
        _DISTANCIA_HORIZONTAL_MAXIMA, _SOBREPOSICAO_VERTICAL_MINIMA,
        _associar_rotulo_valor, _caixa, _sobreposicao_horizontal, _sobreposicao_vertical,
    )

    caixas_outros_campos = [
        (linha.get("grupo_espacial"), caixa)
        for linha in linhas
        if _OUTROS_CAMPOS_RG.match(normalizar_rotulo(linha.get("texto", "")).strip())
        if (caixa := _caixa(linha))
    ]
    ambiguo = False

    def registrar_decisao(decisao, **metadados):
        nonlocal ambiguo
        if decisao.startswith("AMBIGUIDADE_"):
            ambiguo = True
        if observar is not None:
            observar(decisao, **metadados)

    def aceitar_par(etiqueta, valor, caixa_valor, eixo):
        rotulo_textual = _ROTULO_RG_TEXTUAL.match(etiqueta["normalizado"])
        if rotulo_textual and rotulo_textual.group(1).strip():
            # O restante da própria linha já delimita a busca textual.
            return False
        if _classificar_numero_rg(valor) == "CPF":
            registrar_decisao("CANDIDATO_REJEITADO_COMO_CPF")
            return False
        caixa_rg = etiqueta["caixa"]
        caixas_do_grupo = [
            caixa for grupo, caixa in caixas_outros_campos if grupo == etiqueta.get("grupo_espacial")
        ]
        outro_campo = _OUTROS_CAMPOS_RG.search(etiqueta["normalizado"])
        if eixo == "VERTICAL":
            interposto = any(
                (caixa_rg[1] < outra[1] < caixa_valor[1]
                 and _sobreposicao_horizontal(outra, caixa_valor) >= 0.20)
                or (0 <= caixa_valor[0] - outra[2] <= _DISTANCIA_HORIZONTAL_MAXIMA
                    and _sobreposicao_vertical(outra, caixa_valor) >= _SOBREPOSICAO_VERTICAL_MINIMA)
                for outra in caixas_do_grupo
            )
        else:
            interposto = any(
                caixa_rg[0] < outra[0] < caixa_valor[0]
                and _sobreposicao_vertical(outra, caixa_valor) >= _SOBREPOSICAO_VERTICAL_MINIMA
                for outra in caixas_do_grupo
            )
        if outro_campo or interposto:
            registrar_decisao("CANDIDATO_BLOQUEADO_POR_OUTRO_CAMPO")
            return False
        return True

    resultado = _associar_rotulo_valor(
        linhas, _ROTULOS_RG, rf"(?:{_PADRAO_RG.pattern}|{_PADRAO_CPF.pattern})",
        observar=registrar_decisao, aceitar_par=aceitar_par,
    )
    if resultado is None:
        return _resultado_rg(status="AMBIGUO") if ambiguo else None
    registrar_decisao("ASSOCIADO")
    return _resultado_rg(resultado["valor"], resultado["trecho_origem"], "ENCONTRADO")


def extrair_rg_documento(documento):
    """Cruza as evidências por página e só publica um RG inequívoco."""
    from ceuma.extracao.associador_espacial import _caixa

    candidatos = []
    for pagina in documento["paginas"]:
        for texto, linhas in _partes_leitura(pagina):
            if any(_caixa(linha) for linha in linhas):
                # A ordem linear do OCR não comprova que outra caixa pertence ao RG.
                da_pagina = [item for linha in linhas for item in
                             _rgs_textuais(linha.get("texto", ""), permitir_seguinte=False)]
                espacial = _rg_espacial_pagina(linhas)
                if espacial is not None:
                    da_pagina.append(espacial)
            else:
                da_pagina = _rgs_textuais(texto, permitir_seguinte=True)
            candidatos.extend({
                **item, "pagina_origem": pagina["numero"], "metodo_leitura": pagina["metodo"],
            } for item in da_pagina)
    return {"campo": "RG", "pagina_origem": None, "metodo_leitura": None,
            **_selecionar_rg(candidatos)}
