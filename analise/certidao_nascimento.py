"""Extração estruturada e narrativa, com isolamento de averbações."""

import re
from datetime import datetime
from itertools import combinations

from analise.datas_extenso import PADRAO_DATA, interpretar_data
from analise.extrator_campos import _PADRAO_CPF, _partes_leitura, normalizar_rotulo
from analise.identidade import PerfilDocumental, _ROTULOS, _UF, _registro, extrair_campos_perfil


_ROTULOS_CERTIDAO = {
    **{campo: _ROTULOS[campo] for campo in (
        "NOME", "NOME_MAE", "NOME_PAI", "FILIACAO", "NATURALIDADE", "DATA_NASCIMENTO",
    )},
    "CPF": ("CPF DO TITULAR", "CPF DA TITULAR", "CPF"),
    "DATA_REGISTRO": ("DATA DO REGISTRO", "DATA DE REGISTRO", "DATA DA LAVRATURA",
                      "DATA DE LAVRATURA", "REGISTRO EM"),
    "DATA_EMISSAO": ("DATA DE EMISSAO DA CERTIDAO", "DATA DE EMISSAO", "DATA DA EMISSAO",
                     "DATA DE EXPEDICAO", "EMISSAO", "EXPEDICAO"),
    "MATRICULA": ("MATRICULA DA CERTIDAO", "NUMERO DA MATRICULA", "MATRICULA"),
    "CARTORIO": ("CARTORIO DE REGISTRO", "CARTORIO RESPONSAVEL", "NOME DO CARTORIO", "CARTORIO", "SERVENTIA"),
    "LIVRO": ("NUMERO DO LIVRO", "LIVRO"),
    "FOLHA": ("NUMERO DA FOLHA", "FOLHAS", "FOLHA"),
    "TERMO": ("NUMERO DO TERMO", "TERMO"),
    "MUNICIPIO_REGISTRO": ("MUNICIPIO DE REGISTRO / UF", "MUNICIPIO DO REGISTRO / UF",
                           "MUNICIPIO DO REGISTRO", "MUNICIPIO DE REGISTRO", "CIDADE DO REGISTRO"),
    "UF_REGISTRO": ("UF DO REGISTRO", "UF DE REGISTRO", "ESTADO DO REGISTRO"),
}
_ROTULOS_CERTIDAO["NATURALIDADE"] += ("MUNICIPIO DE NASCIMENTO",)
_TERCEIROS = (
    "DECLARANTE", "NOME DO DECLARANTE", "NOME DA DECLARANTE", "CPF DO DECLARANTE", "CPF DA DECLARANTE",
    "TESTEMUNHA", "NOME DA TESTEMUNHA", "CPF DA TESTEMUNHA", "RESPONSAVEL", "NOME DE TERCEIRO",
    "DATA DE TERCEIRO", "NATURALIDADE DE TERCEIRO",
    "AVOS", "AVOS PATERNOS", "AVOS MATERNOS", "AVO", "AVO MATERNA", "AVO MATERNO", "AVO PATERNA",
    "AVO PATERNO", "OFICIAL", "OFICIAL DO CARTORIO", "ESCREVENTE",
)
_OUTROS = (*_TERCEIROS, "SELO", "SELO DIGITAL", "CODIGO DE CONTROLE", "DADOS DO REGISTRADO",
           "REGISTRADO", "REGISTRADA", "HORA DE NASCIMENTO", "LOCAL DO PARTO", "CERTIDAO DE NASCIMENTO")
_MARCADORES = re.compile(r"^(?:AVERBACOES(?: E ANOTACOES| ANOTACOES A ACRESCER)?|AVERBACAO(?: +[0-9]+)?|ANOTACOES)(?=$|[: ./-])")
_SEM_AVERBACAO = re.compile(r"^(?:NADA CONSTA|SEM AVERBACOES|NENHUMA AVERBACAO)[. ]*$")
_FIM_AVERBACAO = re.compile(r"^(?:FIM DAS AVERBACOES|DADOS ORIGINAIS DO REGISTRO)[:. ]*$")
_PARES_REGISTRO = re.compile(rf"(.+?)\s*[/.-]\s*({_UF})")
_DATAS = ("DATA_NASCIMENTO", "DATA_REGISTRO", "DATA_EMISSAO")
CAMPOS_CERTIDAO = (*_ROTULOS_CERTIDAO, "AVERBACAO", "REVISAO_AVERBACAO")


def _normalizar_rotulos(texto):
    texto = re.sub(r"^NOME D[OA] (?:REGISTRAD[OA]|CRIANCA|NASCID[OA])\b", "NOME DO TITULAR", texto)
    return re.sub(r"^CPF D[OA] (?:REGISTRAD[OA]|CRIANCA|NASCID[OA])\b", "CPF DO TITULAR", texto)


def _matricula(texto):
    if not re.fullmatch(r"[0-9]+(?:[ ./-][0-9]+)*", texto):
        return None, False
    digitos = re.sub(r"[^0-9]", "", texto)
    # Limite conservador para distinguir candidatos de tamanho de CPF, sem
    # validar composição, comprimento oficial ou dígito verificador.
    return (digitos, True) if len(digitos) > 11 else (None, False)


def _codigo_registro(texto):
    if re.fullmatch(r"(?:[A-Z][- ]?)?[0-9]{1,12}(?:[-./ ]?[A-Z0-9]{1,5})?", texto):
        return texto, True
    return None, False


def _municipio(texto):
    par = _PARES_REGISTRO.fullmatch(texto)
    nome = par[1] if par else texto
    if re.fullmatch(r"[A-Z][A-Z '\-]{2,70}", nome) and not re.match(r"^(?:SELO|UF|TERMO|LIVRO|FOLHA)\b", nome):
        return texto, True
    return None, False


def _uf_registro(texto):
    return (texto, True) if re.fullmatch(_UF, texto) else (None, False)


def _cartorio(texto):
    if (re.fullmatch(r"[A-Z0-9º°ª][A-Z0-9º°ª .,'/-]{3,110}", texto)
            and re.search(r"\b(?:OFICIO|REGISTRO CIVIL|CARTORIO|SERVENTIA)\b", texto)):
        return texto, True
    return None, False


def _decompor(campo, texto):
    par = _PARES_REGISTRO.fullmatch(texto) if campo == "MUNICIPIO_REGISTRO" else None
    return (par[1], (("UF_REGISTRO", par[2]),)) if par else (texto, ())


_PERFIL = PerfilDocumental(
    _ROTULOS_CERTIDAO, outros=_OUTROS, normalizar=_normalizar_rotulos,
    validadores={
        **dict.fromkeys(_DATAS, interpretar_data),
        "MATRICULA": _matricula, "LIVRO": _codigo_registro, "FOLHA": _codigo_registro,
        "TERMO": _codigo_registro, "MUNICIPIO_REGISTRO": _municipio,
        "UF_REGISTRO": _uf_registro, "CARTORIO": _cartorio,
    },
    decompor=_decompor, reservar=("SELO", "SELO DIGITAL", "CODIGO DE CONTROLE"),
)


def _preparar(documento):
    originais, averbados = [], []
    inicio = None
    em_averbacao = False
    for pagina in documento["paginas"]:
        grupos_originais, grupos_averbados = [], []
        for texto, linhas in _partes_leitura(pagina):
            if not linhas and not pagina.get("grupos_leitura"):
                linhas = pagina.get("linhas") or [{"texto": l} for l in texto.splitlines()]
            grupos = grupos_averbados if em_averbacao else grupos_originais
            atual = []
            terceiros = False
            registrado = False
            for linha in linhas:
                lido = normalizar_rotulo(linha["texto"]).strip()
                if _FIM_AVERBACAO.fullmatch(lido):
                    if atual:
                        grupos.append(atual)
                    grupos, atual, em_averbacao = grupos_originais, [], False
                    terceiros = registrado = False
                    continue
                if marcador := _MARCADORES.match(lido):
                    if atual:
                        grupos.append(atual)
                    grupos, atual, em_averbacao = grupos_averbados, [], True
                    terceiros = registrado = False
                    if inicio is None:
                        inicio = pagina
                    lido = lido[marcador.end():].lstrip(" :.-/")
                    linha = {**linha, "texto": lido}
                if em_averbacao and _SEM_AVERBACAO.fullmatch(lido):
                    if atual:
                        grupos.append(atual)
                    grupos, atual, em_averbacao = grupos_originais, [], False
                    terceiros = registrado = False
                    continue
                if not lido:
                    continue
                if re.match(r"^(?:DADOS D[OA] REGISTRAD[OA]|(?:NOME|CPF) D[OA] REGISTRAD[OA]|NOME DO TITULAR)\b", lido):
                    terceiros, registrado = False, True
                elif re.match(r"^(?:DECLARANTE|TESTEMUNHA|RESPONSAVEL|AVOS?|OFICIAL|ESCREVENTE)\b", lido):
                    terceiros, registrado = True, False
                elif re.match(r"^(?:FILIACAO|(?:NOME|CPF) D[AO] (?:MAE|PAI|RESPONSAVEL)|CARTORIO|SERVENTIA)\b", lido):
                    registrado = False
                if terceiros:
                    seguro = re.sub(r"^(NOME|CPF)(?=\s*[:\-]|$)", r"\1 DE TERCEIRO", lido)
                    seguro = re.sub(r"^(?:DATA DE NASCIMENTO|NASCIMENTO)(?=\s*[:\-]|$)",
                                    "DATA DE TERCEIRO", seguro)
                    seguro = re.sub(r"^NATURALIDADE(?=\s*[:\-]|$)", "NATURALIDADE DE TERCEIRO", seguro)
                    if seguro != lido:
                        linha = {**linha, "texto": seguro}
                elif registrado:
                    explicito = re.sub(r"^CPF(?=\s*[:\-]|$)", "CPF DO TITULAR", lido)
                    if explicito != lido:
                        linha = {**linha, "texto": explicito}
                atual.append(linha)
            if atual:
                grupos.append(atual)
        for destino, grupos in ((originais, grupos_originais), (averbados, grupos_averbados)):
            if grupos:
                destino.append({**pagina, "grupos_leitura": grupos})
    return originais, averbados, inicio


_DATAS_NO_TEXTO = re.compile(rf"(?<!\w){PADRAO_DATA}")
_EVENTOS = {
    "DATA_NASCIMENTO": r"(?:NASCEU|NASCID[OA])",
    "DATA_REGISTRO": r"(?:FOI REGISTRAD[OA](?: O NASCIMENTO)?|REGISTROU-SE(?: O NASCIMENTO)?|"
                     r"REGISTRAD[OA]|LAVRAD[OA](?: O TERMO)?|FOI LAVRADO O TERMO)",
    "DATA_EMISSAO": r"(?:EMITID[OA]|EXPEDID[OA])",
}
_NOME_APROX = r"[A-Z][A-Z '\-]{3,100}?"
_FIM_NOME = r"(?=\s*[,;.]| +(?:NASCID[OA]|FILH[OA]|INSCRIT[OA]|NATURAL|CPF)\b|$)"
_IDENTIFICACAO_NARRATIVA = re.compile(
    rf"\b(?:NASCEU|(?:FOI REGISTRAD[OA]|REGISTROU-SE) O NASCIMENTO DE) "
    rf"(?:(?:O|A) (?:REGISTRAD[OA]|CRIANCA|MENIN[OA]) )?(?P<nome>{_NOME_APROX}){_FIM_NOME}"
)
_CPF_NARRATIVO = re.compile(
    rf"^(?:\s*,\s*| +)INSCRIT[OA] NO CPF(?: SOB)?(?: O)?(?: N[Oº°.]*)?\s*[:.-]?\s*({_PADRAO_CPF.pattern})"
)
_INICIO_TERCEIRO = re.compile(r"^(?:O |A )?(?:NO |EM |AOS? |UM |UMA |PAI |MAE |DECLARANTE |TESTEMUNHA )")


def _narrativa(texto, *, averbacao=False):
    texto = " ".join(normalizar_rotulo(texto).split())
    identidades = [m for m in _IDENTIFICACAO_NARRATIVA.finditer(texto) if not _INICIO_TERCEIRO.match(m["nome"])]
    for ident in identidades:
        yield "NOME", ident["nome"].strip()
        if cpf := _CPF_NARRATIVO.match(texto[ident.end():]):
            yield "CPF", cpf[1]
    for trecho in re.finditer(
        rf"\b[OA] REGISTRAD[OA] (?:ESTA )?INSCRIT[OA] NO CPF(?: SOB)?(?: O)?(?: N[Oº°.]*)? *[:.-]? *({_PADRAO_CPF.pattern})",
        texto,
    ):
        yield "CPF", trecho[1]
    if averbacao:
        for m in re.finditer(rf"\b(?:PASSA A SE CHAMAR|PASSOU A SE CHAMAR) ({_NOME_APROX}){_FIM_NOME}", texto):
            yield "NOME", m[1].strip()

    for data in _DATAS_NO_TEXTO.finditer(texto):
        valor = data.group()
        if interpretar_data(valor)[0] is None:
            continue
        antes = texto[max(0, data.start() - 115):data.start()]
        depois = texto[data.end():data.end() + 115]
        anteriores, posteriores = set(), set()
        for campo, evento in _EVENTOS.items():
            m = re.search(rf"\b({evento})\s*[:,-]?\s*(?:EM |NO DIA |AOS? )?$", antes)
            if m and not (
                campo == "DATA_NASCIMENTO"
                and re.search(r"\b(?:MAE|PAI|AVO|DECLARANTE|TESTEMUNHA)\b", re.split(r"[;.]", antes[:m.start()])[-1])
            ):
                anteriores.add(campo)
            if re.match(rf"\s*[,;:]?\s*(?:FOI )?(?:{evento})\b", depois):
                posteriores.add(campo)
        # Um conector antes da data liga-a ao evento anterior, mesmo que logo
        # depois venha outra oração sobre o registro.
        campos = anteriores or posteriores
        if len(campos) == 1:
            yield next(iter(campos)), valor

    for m in re.finditer(
        r"\b(LIVRO|FOLHAS?|TERMO)\s+(?:N[Oº°.]?\s*)?([A-Z]?[- ]?[0-9]+(?:[-./]?[A-Z0-9]+)?)(?!\w)",
        texto,
    ):
        yield "FOLHA" if m[1] == "FOLHAS" else m[1], m[2].strip()
    for m in re.finditer(
        r"\b(?:LAVRAD[OA]|REGISTRAD[OA]|REGISTRO EFETUADO) (?:NO|NA) "
        r"(?P<cartorio>(?:CARTORIO|SERVENTIA|[0-9]+[º°]? OFICIO)[A-Z0-9º° '\-/]{3,100}?)(?=,|[;.]|$)",
        texto,
    ):
        yield "CARTORIO", m["cartorio"].strip()
    for m in re.finditer(
        rf"\b(?:LAVRAD[OA]|REGISTRAD[OA]|REGISTRO EFETUADO) NO MUNICIPIO DE "
        rf"({_NOME_APROX}(?: +[/.-] +{_UF})?)(?=,|[;.]|$)",
        texto,
    ):
        yield "MUNICIPIO_REGISTRO", m[1].strip()
    # Relações narrativas só são lidas em orações ligadas a um nome registrado.
    for ident in identidades:
        contexto = re.split(r"[;.]", texto[ident.end():], maxsplit=1)[0]
        if m := re.search(rf"\bNATURAL DE ({_NOME_APROX})(?=,| +FILH[OA]\b|$)", contexto):
            yield "NATURALIDADE", m[1].strip()
        if m := re.search(rf"\bFILH[OA] DE ({_NOME_APROX})(?=,| +(?:NET[OA]|DECLARANTE|REGISTRO)\b|$)", contexto):
            for nome in re.split(r" E DE ", m[1]):
                nome = nome.strip()
                if nome:
                    yield "FILIACAO", nome
        for m in re.finditer(
            rf"\b(?:TENDO (?:POR|COMO)|SENDO) (MAE|PAI) ({_NOME_APROX})(?= +E +(?:POR |COMO )?(?:MAE|PAI)\b|$)",
            contexto,
        ):
            yield "NOME_MAE" if m[1] == "MAE" else "NOME_PAI", m[2].strip()


def extrair_certidao_nascimento(documento):
    originais, averbados, inicio = _preparar(documento)
    campos = extrair_campos_perfil({"paginas": originais}, _PERFIL, narrar=_narrativa)
    campos["AVERBACAO"] = _registro("AVERBACAO", "ENCONTRADO" if averbados else "NAO_ENCONTRADO",
                                    inicio if averbados else None, "PRESENTE" if averbados else None)
    campos["REVISAO_AVERBACAO"] = _registro("REVISAO_AVERBACAO", "NAO_ENCONTRADO")
    if averbados:
        posteriores = extrair_campos_perfil(
            {"paginas": averbados}, _PERFIL, narrar=lambda texto: _narrativa(texto, averbacao=True),
        )
        conflitos = []
        for nome, posterior in posteriores.items():
            anterior = campos[nome]
            if posterior["status"] == "AMBIGUO" or (
                posterior["status"] == "ENCONTRADO" and anterior["status"] == "ENCONTRADO"
                and posterior["valor"] != anterior["valor"]
            ):
                conflitos.append(nome)
                campos[nome] = _registro(nome, "AMBIGUO", inicio)
        estado = "AMBIGUO" if conflitos else "EVIDENCIA_INSUFICIENTE"
        campos["REVISAO_AVERBACAO"] = {**_registro("REVISAO_AVERBACAO", estado, inicio), "campos": conflitos}

    datas = {nome: datetime.strptime(campos[nome]["valor"], "%d/%m/%Y")
             for nome in _DATAS if campos[nome]["status"] == "ENCONTRADO"}
    pares = [(a, b) for a, b in combinations(datas, 2) if datas[a] > datas[b]]
    if pares:
        campos["CRONOLOGIA_CERTIDAO"] = {
            **_registro("CRONOLOGIA_CERTIDAO", "INCONSISTENCIA_CRONOLOGICA"), "pares": pares,
        }
    return campos
