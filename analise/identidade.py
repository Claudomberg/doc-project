"""Evidências e campos específicos de RG e Carteira de Identidade Nacional."""

import re
from collections import defaultdict

from analise.associador_espacial import (
    _associar_rotulo_valor, _caixa, _sobreposicao_horizontal, _sobreposicao_vertical,
)
from analise.extrator_campos import (
    _PADRAO_CPF, _classificar_numero_rg, _partes_leitura,
    extrair_datas, extrair_nome_titular, normalizar_rotulo, validar_cpf,
)
from documentos.identificador import _ROTULO_REGISTRO_CPF


_ROTULOS = {
    "NOME": ("NOME COMPLETO", "NOME DO TITULAR", "NOME DO PORTADOR", "NOME / NAME", "NOME"),
    "CPF": ("REGISTRO GERAL CPF", "CPF DO TITULAR", "CPF DA TITULAR", "CPF"),
    "RG": ("REGISTRO GERAL", "REGISTRO DE IDENTIDADE", "NUMERO DO RG", "R.G.", "R.G", "RG"),
    "DATA_NASCIMENTO": ("DATA DE NASCIMENTO", "DATA NASCIMENTO", "DT. NASC.", "DT NASC", "NASCIMENTO"),
    "DATA_EXPEDICAO": ("DATA DE EXPEDICAO", "DATA DA EXPEDICAO", "DATA DE EMISSAO",
                       "DATA DA EMISSAO", "EXPEDICAO", "EMISSAO"),
    "DATA_VALIDADE": ("DATA DE VALIDADE", "VALIDADE", "VENCIMENTO"),
    "ORGAO_EMISSOR": ("ORGAO EMISSOR / UF", "ORGAO EXPEDIDOR / UF", "ORGAO EMISSOR/UF",
                      "ORGAO EXPEDIDOR/UF", "ORGAO EMISSOR", "ORGAO EXPEDIDOR", "ORGAO DE EXPEDICAO"),
    "UF_EMISSAO": ("UF DE EMISSAO", "UF DE EXPEDICAO", "UF DO ORGAO EMISSOR", "UF EXPEDIDORA"),
    "NOME_MAE": ("NOME DA MAE", "MAE", "GENITORA"),
    "NOME_PAI": ("NOME DO PAI", "PAI", "GENITOR"),
    "FILIACAO": ("FILIACAO / PARENTAGE", "FILIACAO", "NOME DOS PAIS"),
    "NATURALIDADE": ("NATURALIDADE", "LOCAL DE NASCIMENTO"),
    "NACIONALIDADE": ("NACIONALIDADE",),
}
_OUTROS_ROTULOS = (
    "CPF DO RESPONSAVEL", "CPF DA RESPONSAVEL", "CPF DO PAI", "CPF DA MAE", "CPF DE TERCEIRO",
    "CPF DO DECLARANTE", "NOME DO RESPONSAVEL", "NOME SOCIAL", "ASSINATURA",
    "NUMERO DE CONTROLE", "CONTROLE", "NUMERO DE REGISTRO", "REGISTRO", "NUMERO DO DOCUMENTO",
    "DOCUMENTO DE ORIGEM", "DOC. ORIGEM", "OBSERVACOES", "SEXO", "ENDERECO", "UF",
    "CARTEIRA DE IDENTIDADE NACIONAL", "CARTEIRA DE IDENTIDADE", "REPUBLICA FEDERATIVA DO BRASIL",
)
class PerfilDocumental:
    def __init__(self, rotulos, *, outros=(), orgaos=None, normalizar=None, validadores=None,
                 decompor=None, complementar=None, cpf_contextual=False, nao_aplicaveis=(),
                 orgao_avulso=None, reservar=()):
        self.rotulos = rotulos
        self.campo_por_rotulo = {
            **dict.fromkeys((*_OUTROS_ROTULOS, *outros)),
            **{rotulo: campo for campo, aliases in rotulos.items() for rotulo in aliases},
        }
        alternativas = "|".join(
            r" +".join(map(re.escape, rotulo.split()))
            for rotulo in sorted(self.campo_por_rotulo, key=len, reverse=True)
        )
        self.regex_rotulo = re.compile(r"(?<!\w)(?:" + alternativas + r")(?!\w)")
        self.orgaos = orgaos or {}
        self.normalizar = normalizar
        self.validadores = validadores or {}
        self.decompor = decompor
        self.complementar = complementar
        self.cpf_contextual = cpf_contextual
        self.nao_aplicaveis = frozenset(nao_aplicaveis)
        self.orgao_avulso = orgao_avulso
        self.reservar = reservar


_UF = r"(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)"
_SIGLA = r"(?:SSP|SSPDS|SSPDF|SSDS|SDS|SESP|SEJUSP|SEPOL|PC|POLITEC|IFP|IIRGD|DIC|DGPC|IGP|DETRAN|DENATRAN|SENATRAN|SJS)"
_SIGLA_UF = re.compile(rf"({_SIGLA})(?:\s*[/.-]\s*|\s+)({_UF})")
_DATA = r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}"
_NOMES = {"NOME", "NOME_MAE", "NOME_PAI", "FILIACAO"}
_PAPEIS_FILIACAO = {"NOME_MAE", "NOME_PAI", "FILIACAO"}
_NOME_TITULAR_EXPLICITO = {"NOME DO TITULAR", "NOME DO PORTADOR"}


def _normalizar(texto, perfil):
    texto = normalizar_rotulo(texto).strip()
    return perfil.normalizar(texto) if perfil.normalizar is not None else texto


def _segmentos(linha, perfil):
    """Reconhece rótulos completos e delimita mais de um campo na mesma linha."""
    marcadores = [
        m for m in perfil.regex_rotulo.finditer(linha)
        if (
            m.group() not in {"GENITOR", "GENITORA"}
            or not linha[m.end():].strip()
            or linha[m.end():].lstrip().startswith((":", "-", "–", "—"))
        )
        and (m.start() == 0 or linha[:m.start()].rstrip().endswith((";", "|"))
             or linha[m.start() - 2:m.start()] == "  "
             or linha[m.end():].lstrip().startswith(":"))
    ]
    return [
        (" ".join(m.group().split()),
         linha[m.end(): marcadores[i + 1].start() if i + 1 < len(marcadores) else len(linha)]
         .strip(" :;-|–—"))
        for i, m in enumerate(marcadores)
    ]


def _numero_rg(texto):
    # Mantém o formato lido. O prefixo estadual não participa da validação
    # do núcleo, inclusive da exclusão de CPFs válidos.
    nucleo = re.sub(rf"^{_UF}\s*[-./]\s*", "", texto)
    if re.fullmatch(r"\d{1,3}(?: \d{3}){1,2}(?:-[0-9X])?", nucleo):
        nucleo = nucleo.replace(" ", ".")
    return texto if _classificar_numero_rg(nucleo) == "RG" else None


def _valor(campo, texto, perfil):
    texto = " ".join(texto.split()).strip(" :;|")
    if campo in perfil.validadores:
        return perfil.validadores[campo](texto)
    if campo == "CPF":
        if _PADRAO_CPF.fullmatch(texto):
            return re.sub(r"\D", "", texto), validar_cpf(texto)
    elif campo == "RG":
        return _numero_rg(texto), True
    elif campo.startswith("DATA_"):
        if re.fullmatch(_DATA, texto):
            data, = extrair_datas(texto)
            return data["valor"], data["valida"]
    elif campo in _NOMES:
        palavras = texto.split()
        if (2 <= len(palavras) <= 12 and re.fullmatch(r"[A-Z][A-Z '\-.]*", texto)
                and not _segmentos(texto, perfil) and not re.match(r"^(?:ESTADO DE|SECRETARIA|INSTITUTO)\b", texto)):
            nome = extrair_nome_titular(f"NOME: {texto}")
            return nome["valor"], nome["status"] == "ENCONTRADO"
    elif campo in perfil.orgaos.values():
        return (texto if re.fullmatch(_UF, texto) else None), True
    elif campo in perfil.orgaos:
        if (re.fullmatch(rf"{_SIGLA}(?:\s*[/.-]\s*{_UF})?", texto)
                or _SIGLA_UF.fullmatch(texto)
                or re.fullmatch(r"(?:SECRETARIA|INSTITUTO|POLICIA|DEPARTAMENTO) [A-Z /.-]{3,85}", texto)):
            return texto, True
    elif campo in {"NATURALIDADE", "NACIONALIDADE"}:
        if re.fullmatch(r"[A-Z][A-Z '\-/.,]{2,80}", texto) and not _segmentos(texto, perfil):
            return texto, True
    return None, False


def _registro(campo, status, pagina=None, valor=None):
    return {
        "campo": campo, "status": status, "valor": valor,
        "pagina_origem": pagina["numero"] if pagina is not None else None,
        "metodo_leitura": pagina["metodo"] if pagina is not None else None,
        # Persiste o valor autorizado, sem copiar linhas inteiras com outros campos.
        "trecho_origem": None,
    }


def _cpf_do_titular(rotulo, perfil, contexto_pessoal, filiacao):
    if rotulo in {"CPF DO TITULAR", "CPF DA TITULAR"}:
        return True
    return perfil.cpf_contextual and (
        rotulo == "REGISTRO GERAL CPF" or (rotulo == "CPF" and contexto_pessoal and not filiacao)
    )


def _campo_contextual(rotulo, filiacao, perfil):
    campo = perfil.campo_por_rotulo[rotulo]
    if campo == "NOME" and filiacao and rotulo not in _NOME_TITULAR_EXPLICITO:
        return "FILIACAO"
    return campo


def _continua_filiacao(campo, anterior):
    return campo in _PAPEIS_FILIACAO or (campo in {None, "CPF"} and anterior)


def _textuais(texto, com_geometria, perfil, contexto_pessoal, adicionar):
    linhas = [l for linha in texto.splitlines() if (l := _normalizar(linha, perfil))]
    filiacao = False
    for indice, linha in enumerate(linhas):
        segmentos = _segmentos(linha, perfil)
        if perfil.orgao_avulso and not segmentos and _SIGLA_UF.fullmatch(linha):
            adicionar(perfil.orgao_avulso, linha)
        uf_do_orgao = None
        for rotulo, valor in segmentos:
            campo = _campo_contextual(rotulo, filiacao, perfil)
            if rotulo == "UF" and uf_do_orgao:
                campo = uf_do_orgao
            uf_do_orgao = perfil.orgaos.get(campo)
            if campo is None or campo in perfil.nao_aplicaveis:
                continue
            filiacao = _continua_filiacao(campo, filiacao)
            titular = campo == "CPF" and _cpf_do_titular(rotulo, perfil, contexto_pessoal, filiacao)
            if len(segmentos) == 1 and not com_geometria:
                seguintes = linhas[indice + 1: indice + (3 if campo == "FILIACAO" else 2)]
                if not valor and seguintes and not _segmentos(seguintes[0], perfil):
                    valor = seguintes[0]
                if campo == "FILIACAO" and valor and seguintes and not _segmentos(seguintes[0], perfil):
                    nomes = [valor]
                    nomes.extend(l for l in seguintes if l not in nomes and not _segmentos(l, perfil))
                    valor = "; ".join(nomes[:2])
            if campo == "FILIACAO" and valor:
                for nome in re.split(r";|\s+/\s+", valor):
                    adicionar(campo, nome.strip())
            else:
                adicionar(campo, valor, titular=titular)


def _na_trajetoria(origem, destino, outra, eixo):
    if eixo == "HORIZONTAL":
        return origem[2] <= outra[0] < destino[0] and _sobreposicao_vertical(outra, destino) >= 0.50
    return origem[3] <= outra[1] < destino[1] and _sobreposicao_horizontal(outra, destino) >= 0.20


def _espaciais(linhas, perfil, contexto_pessoal, adicionar, ambiguos):
    """Usa o associador comum e mantém a competição entre tipos de campo."""
    linhas = [{**linha, "texto": _normalizar(linha["texto"], perfil)} for linha in linhas]
    segmentos = [_segmentos(linha["texto"], perfil) for linha in linhas]
    for linha, partes in zip(linhas, segmentos):
        if len(partes) == 1 and not partes[0][1]:
            linha["texto"] = partes[0][0]
    caixas_rotulos = [(i, _caixa(linha)) for i, linha in enumerate(linhas) if segmentos[i]]
    caixas_orgao = [
        (i, caixa, perfil.orgaos[perfil.campo_por_rotulo[r]]) for i, caixa in caixas_rotulos
        for r, _ in segmentos[i] if caixa and perfil.campo_por_rotulo[r] in perfil.orgaos
    ]
    uf_contextual = {}
    for i, caixa in caixas_rotulos:
        if not caixa or segmentos[i][0][0] != "UF":
            continue
        possiveis = {
            campo_uf for j, orgao, campo_uf in caixas_orgao
            if linhas[i].get("grupo_espacial") == linhas[j].get("grupo_espacial")
            and 0 <= caixa[0] - orgao[2] <= 90 and _sobreposicao_vertical(caixa, orgao) >= 0.50
        }
        if len(possiveis) == 1:
            uf_contextual[i], = possiveis
            adicionar(uf_contextual[i], segmentos[i][0][1])
    reservados = set()
    if perfil.reservar:
        def observar_reserva(decisao, **medidas):
            if decisao == "PAR_ELEGIVEL":
                reservados.add(tuple(medidas["caixa_valor"]))

        _associar_rotulo_valor(
            linhas, perfil.reservar, r"[0-9][0-9 ./-]{4,24}",
            observar=observar_reserva,
            aceitar_par=lambda etiqueta, *_: all(
                len(segmentos[i]) == 1 and not segmentos[i][0][1]
                and segmentos[i][0][0] in perfil.reservar for i in etiqueta["indices"]
            ),
        )
    propostas = defaultdict(list)
    filiacao_ativa = False
    contexto_cpf = {}
    nomes_em_filiacao = set()
    for indice, partes in enumerate(segmentos):
        for rotulo, _ in partes:
            campo = _campo_contextual(rotulo, filiacao_ativa, perfil)
            filiacao_ativa = _continua_filiacao(campo, filiacao_ativa)
            if campo == "FILIACAO" and perfil.campo_por_rotulo[rotulo] == "NOME":
                nomes_em_filiacao.add(indice)
            if campo == "CPF":
                contexto_cpf[indice] = _cpf_do_titular(rotulo, perfil, contexto_pessoal, filiacao_ativa)

    for campo, rotulos in perfil.rotulos.items():
        if campo in perfil.nao_aplicaveis:
            continue
        if campo in uf_contextual.values():
            rotulos += ("UF",)
        if campo == "FILIACAO" and nomes_em_filiacao:
            rotulos += tuple(r for r in perfil.rotulos["NOME"] if r not in _NOME_TITULAR_EXPLICITO)
        selecionado = None

        def observar(decisao, **metadados):
            nonlocal selecionado
            if decisao == "PAR_SELECIONADO":
                selecionado = metadados
            elif decisao.startswith("AMBIGUIDADE_"):
                ambiguos.add(campo)

        def aceitar(etiqueta, candidato, caixa, eixo):
            if tuple(caixa) in reservados or _valor(campo, candidato, perfil)[0] is None:
                return False
            indices = etiqueta["indices"]
            if campo == "NOME" and any(i in nomes_em_filiacao for i in indices):
                return False
            if campo == "FILIACAO" and any(
                segmentos[i] and perfil.campo_por_rotulo[segmentos[i][0][0]] == "NOME"
                and i not in nomes_em_filiacao
                for i in indices
            ):
                return False
            if campo in perfil.orgaos.values() and any(
                segmentos[i] and segmentos[i][0][0] == "UF" and uf_contextual.get(i) != campo
                for i in indices
            ):
                return False
            if any(
                segmentos[i] and (
                    len(segmentos[i]) != 1 or segmentos[i][0][1]
                    or (len(indices) == 1 and segmentos[i][0][0] not in rotulos)
                )
                for i in indices
            ):
                return False
            if _segmentos(candidato, perfil):
                return False
            return not any(
                outra and i not in indices and linhas[i].get("grupo_espacial") == etiqueta["grupo_espacial"]
                and _na_trajetoria(etiqueta["caixa"], caixa, outra, eixo)
                for i, outra in caixas_rotulos
            )

        achado = _associar_rotulo_valor(linhas, rotulos, r".+", aceitar_par=aceitar, observar=observar)
        if achado is not None:
            ids = selecionado["ids_rotulo"]
            rotulo = _normalizar(achado["rotulo"].replace(" | ", " "), perfil)
            titular = campo == "CPF" and (
                _cpf_do_titular(rotulo, perfil, contexto_pessoal, True)
                or all(contexto_cpf.get(i, False) for i in ids)
            )
            propostas[selecionado["id_valor"]].append((campo, achado["valor"], titular))
            if perfil.complementar is not None:
                for i, outro, valor in perfil.complementar(campo, rotulo, linhas, selecionado["id_valor"]):
                    propostas[i].append((outro, valor, False))
            if campo == "FILIACAO" and selecionado["eixo"] == "VERTICAL":
                primeiro = selecionado["id_valor"]
                caixa_primeiro = _caixa(linhas[primeiro])
                for i in range(primeiro + 1, len(linhas)):
                    caixa = _caixa(linhas[i])
                    if (segmentos[i] or caixa is None
                            or linhas[i].get("grupo_espacial") != linhas[primeiro].get("grupo_espacial")):
                        break
                    if (0 <= caixa[1] - caixa_primeiro[3] <= 42
                            and _sobreposicao_horizontal(caixa_primeiro, caixa) >= 0.35
                            and _valor(campo, linhas[i]["texto"], perfil)[0]):
                        propostas[i].append((campo, linhas[i]["texto"], False))
                    break
            if campo in perfil.orgaos and rotulo.endswith("UF") and not _SIGLA_UF.fullmatch(achado["valor"]):
                primeiro = selecionado["id_valor"]
                caixa_orgao = _caixa(linhas[primeiro])
                for i, linha in enumerate(linhas):
                    caixa = _caixa(linha)
                    if (caixa and re.fullmatch(_UF, linha["texto"])
                            and linha.get("grupo_espacial") == linhas[primeiro].get("grupo_espacial")
                            and 0 <= caixa[0] - caixa_orgao[2] <= 32
                            and _sobreposicao_vertical(caixa_orgao, caixa) >= 0.50):
                        propostas[i].append((perfil.orgaos[campo], linha["texto"], False))

    for propostas_do_valor in propostas.values():
        if len({campo for campo, _, _ in propostas_do_valor}) > 1:
            ambiguos.update(campo for campo, _, _ in propostas_do_valor)
        else:
            campo, valor, titular = propostas_do_valor[0]
            adicionar(campo, valor, titular=titular)


def extrair_campos_perfil(documento, perfil, rg_compartilhado=None, *, narrar=None):
    """Aplica um perfil sem cruzar os referenciais de leitura de uma página."""
    vistos = {}
    candidatos = defaultdict(list)
    ambiguos = set()
    sem_validade = set()

    for pagina in documento["paginas"]:
        def adicionar(campo, texto, *, titular=False):
            vistos.setdefault(campo, pagina)
            if campo == "DATA_VALIDADE" and texto in {"INDETERMINADA", "SEM VALIDADE", "SEM PRAZO DE VALIDADE"}:
                sem_validade.add(campo)
                return
            valor, valido = _valor(campo, texto, perfil)
            if valor is None:
                if ((campo in {"RG", "CPF", "RG_ORIGEM", "REGISTRO_CNH"}
                     or (campo.startswith("DATA_") and campo not in perfil.validadores))
                        and re.search(r"\d", texto)):
                    candidatos[campo].append(_registro(campo, "INVALIDO", pagina))
                return
            if valido == "AMBIGUO":
                ambiguos.add(campo)
                return
            if perfil.decompor is not None:
                valor, extras = perfil.decompor(campo, valor)
                for outro, complemento in extras:
                    adicionar(outro, complemento)
            if campo in perfil.orgaos:
                par = _SIGLA_UF.fullmatch(valor) or re.fullmatch(rf"(.+?)\s*[/.-]\s*({_UF})", valor)
                if par:
                    valor = par[1]
                    adicionar(perfil.orgaos[campo], par[2])
            situacao = "ENCONTRADO" if valido else "INVALIDO"
            if campo == "CPF" and valido and not titular:
                situacao = "EVIDENCIA_INSUFICIENTE"
            candidatos[campo].append(_registro(campo, situacao, pagina, valor))

        for texto, linhas in _partes_leitura(pagina):
            if not linhas and not pagina.get("grupos_leitura"):
                linhas = pagina.get("linhas") or []
            geometria = any(_caixa(linha) for linha in linhas)
            contexto = bool(re.search(
                r"(?m)^\s*(?:NOME\b|DATA DE NASCIMENTO\b|CARTEIRA DE IDENTIDADE NACIONAL\b)",
                normalizar_rotulo(texto),
            ))
            if perfil.cpf_contextual and not contexto:
                contexto = any(
                    perfil.campo_por_rotulo[rotulo] in {"NOME", "DATA_NASCIMENTO"}
                    for linha in texto.splitlines()
                    for rotulo, _ in _segmentos(_normalizar(linha, perfil), perfil)
                )
            _textuais(texto, geometria, perfil, contexto, adicionar)
            if geometria:
                _espaciais(linhas, perfil, contexto, adicionar, ambiguos)
            if narrar is not None:
                for campo, valor in narrar(texto):
                    adicionar(campo, valor, titular=campo == "CPF")

    # O candidato tradicional mantém os formatos e os bloqueios já testados.
    rg = rg_compartilhado
    if rg is not None and rg["status"] == "AMBIGUO":
        ambiguos.add("RG")
    elif rg is not None and rg["status"] == "ENCONTRADO" and not candidatos["RG"] and "RG" not in vistos:
        candidatos["RG"].append({**rg, "trecho_origem": None})

    resultado = {}
    for campo in perfil.rotulos:
        alternativas = candidatos[campo]
        validas = {item["valor"]: item for item in alternativas if item["status"] == "ENCONTRADO"}
        if campo in {"RG", "RG_ORIGEM"}:
            validas = {re.sub(r"[. -]", "", item["valor"]): item for item in validas.values()}
        if campo == "FILIACAO":
            expressas = {item["valor"] for outro in ("NOME_MAE", "NOME_PAI")
                         for item in candidatos[outro] if item["status"] == "ENCONTRADO"}
            validas = {valor: item for valor, item in validas.items() if valor not in expressas}
        if campo in perfil.nao_aplicaveis:
            resultado[campo] = _registro(campo, "NAO_APLICAVEL")
        elif (campo in ambiguos or (campo in sem_validade and validas)
              or len(validas) > (2 if campo == "FILIACAO" else 1)):
            resultado[campo] = _registro(campo, "AMBIGUO", vistos.get(campo))
        elif validas:
            escolhido = next(iter(validas.values()))
            resultado[campo] = {**escolhido, "valor": "; ".join(validas)} if campo == "FILIACAO" else escolhido
        elif campo in sem_validade:
            resultado[campo] = _registro(campo, "NAO_APLICAVEL", vistos.get(campo))
        elif alternativas and campo != "FILIACAO":
            valores = {item["valor"] for item in alternativas}
            resultado[campo] = alternativas[0] if len(valores) == 1 else _registro(campo, "AMBIGUO", vistos.get(campo))
        else:
            estado = "EVIDENCIA_INSUFICIENTE" if campo in vistos else "NAO_ENCONTRADO"
            resultado[campo] = _registro(campo, estado, vistos.get(campo))
    return resultado


# Traduções impressas junto ao rótulo. Palavras em inglês isoladas não
# estabelecem vínculo com um valor nem mudam a classificação do documento.
_ROTULOS_BILINGUES_CIN = re.compile(
    r"(?<!\w)(?:"
    r"(NOME)\s*/\s*(?:FULL NAME|NAME)|"
    r"(REGISTRO GERAL CPF|CPF)\s*/\s*(?:PERSONAL NUMBER|PERSONAL NO\.?)|"
    r"(DATA DE NASCIMENTO|NASCIMENTO)\s*/\s*(?:DATE OF BIRTH|BIRTH DATE)|"
    r"(DATA DE EXPEDICAO|DATA DE EMISSAO|EXPEDICAO|EMISSAO)\s*/\s*(?:DATE OF ISSUE|ISSUE DATE)|"
    r"(DATA DE VALIDADE|VALIDADE)\s*/\s*(?:DATE OF EXPIRY|EXPIRY DATE|EXPIRATION DATE|EXPIRY)|"
    r"(FILIACAO)\s*/\s*PARENTAGE|"
    r"(NATURALIDADE)\s*/\s*PLACE OF BIRTH|"
    r"(NACIONALIDADE)\s*/\s*NATIONALITY|"
    r"(ORGAO EMISSOR|ORGAO EXPEDIDOR)\s*/\s*(?:ISSUING AUTHORITY|CARD ISSUER|ISSUER)|"
    r"(UF DE EMISSAO|UF DE EXPEDICAO)\s*/\s*(?:STATE OF ISSUE|ISSUING STATE)"
    r")(?!\w)"
)


def _normalizar_cin(texto):
    texto = _ROTULO_REGISTRO_CPF.sub("REGISTRO GERAL CPF", texto)
    return _ROTULOS_BILINGUES_CIN.sub(lambda m: next(r for r in m.groups() if r is not None), texto)


_PERFIS_IDENTIDADE = {
    tipo: PerfilDocumental(
        {**_ROTULOS, "CPF": tuple(r for r in _ROTULOS["CPF"] if cin or r != "REGISTRO GERAL CPF")},
        outros=("REGISTRO GERAL CPF",), orgaos={"ORGAO_EMISSOR": "UF_EMISSAO"},
        normalizar=(_normalizar_cin if cin else
                    lambda texto: _ROTULO_REGISTRO_CPF.sub("REGISTRO GERAL CPF", texto)),
        cpf_contextual=cin, nao_aplicaveis=("RG",) if cin else (),
        orgao_avulso="ORGAO_EMISSOR",
    )
    for tipo, cin in (("DOCUMENTO_IDENTIDADE", False), ("CIN", True))
}
CAMPOS_IDENTIDADE = tuple(_ROTULOS)


def extrair_identidade(documento, tipo, rg_compartilhado=None):
    return extrair_campos_perfil(documento, _PERFIS_IDENTIDADE[tipo], rg_compartilhado)
