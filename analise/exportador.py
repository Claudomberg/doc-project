import csv
import os
import stat
import unicodedata
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import mkdtemp

from analise.relatorio import RelatorioIndisponivel, gerar_relatorio, recuperar_relatorio
from banco.conexao import consultar_expiracao_ativa
from banco.itens_documentais import projetar_itens_analise


def _caminho_csv(caminho_destino):
    destino = Path(caminho_destino).absolute()
    if destino.suffix.lower() != ".csv":
        raise ValueError("O arquivo de destino deve ser CSV.")
    return destino


def identificar_destino_csv(caminho_destino):
    """Captura os metadados do arquivo que o operador pode confirmar."""
    destino = _caminho_csv(caminho_destino)
    try:
        metadados = destino.lstat()
    except FileNotFoundError:
        return None

    atributos = getattr(metadados, "st_file_attributes", 0)
    if not stat.S_ISREG(metadados.st_mode) or atributos & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("O destino CSV deve ser um arquivo regular.")

    return (
        destino, metadados.st_dev, metadados.st_ino, metadados.st_size,
        metadados.st_mtime_ns, metadados.st_ctime_ns,
    )


def _conferir_destino(destino, destino_confirmado):
    if identificar_destino_csv(destino) != destino_confirmado:
        raise FileExistsError(
            "O destino já existe ou mudou. Selecione-o novamente para confirmar a substituição."
        )


def _publicar_sem_sobrescrever(temporario, destino):
    try:
        if os.name == "nt":
            temporario.rename(destino)
        else:
            # Em POSIX, rename poderia substituir o destino.
            os.link(temporario, destino)
    except FileExistsError as erro:
        raise FileExistsError(
            "Outro arquivo foi criado nesse destino. Selecione-o novamente."
        ) from erro


def exportar_analise_csv(
    conexao, caminho_destino, *, codigo=None, analise_id=None, destino_confirmado=None
):
    """Recupera uma análise ativa e valida sua disponibilidade antes de publicar."""
    relatorio, _ = recuperar_relatorio(conexao, codigo, analise_id=analise_id)

    return exportar_relatorio_csv(
        relatorio, caminho_destino, destino_confirmado=destino_confirmado, conexao=conexao,
    )


@contextmanager
def _validar_e_reservar_publicacao(conexao, relatorio):
    if conexao is None:
        yield
        return
    if conexao.in_transaction:
        raise ValueError("Use uma conexão sem transação ativa para exportar.")

    # Reserva a fronteira entre a última consulta e a publicação, sem bloquear
    # gravações no banco enquanto o CSV temporário estiver sendo preparado.
    conexao.execute("BEGIN IMMEDIATE")
    try:
        codigo = relatorio["codigo"]
        analise = conexao.execute("SELECT id FROM analises WHERE codigo = ?", (codigo,)).fetchone()
        if analise is None or consultar_expiracao_ativa(conexao, codigo) is None:
            raise RelatorioIndisponivel("A análise expirou ou não está mais disponível.")
        if relatorio.get("modo") == "FAMILIAR":
            mudou = gerar_relatorio(conexao, analise[0]) != relatorio
        else:
            mudou = projetar_itens_analise(conexao, analise[0]) != relatorio.get("itens_documentais", [])
        if mudou:
            raise RelatorioIndisponivel("A análise mudou durante a exportação. Recupere o relatório.")
        yield
    finally:
        conexao.rollback()


def exportar_relatorio_csv(
    relatorio, caminho_destino, *, destino_confirmado=None, validar_publicacao=None, conexao=None,
):
    """Publica um CSV completo e exige confirmação para substituir um arquivo."""
    if conexao is None:
        if relatorio.get("itens_documentais"):
            raise ValueError("A exportação de itens documentais requer uma conexão para validação.")
        if relatorio.get("modo") == "FAMILIAR" and validar_publicacao is None:
            raise ValueError("A exportação familiar requer validação antes da publicação.")
    destino = _caminho_csv(caminho_destino)
    _conferir_destino(destino, destino_confirmado)
    destino.parent.mkdir(parents=True, exist_ok=True)

    pasta = Path(mkdtemp(prefix=".ceuma-exportacao-", dir=destino.parent))
    temporario = pasta / "relatorio.csv"
    criado = False
    try:
        with temporario.open("x", newline="", encoding="utf-8-sig") as arquivo:
            criado = True
            _gravar_relatorio(relatorio, arquivo)
            arquivo.flush()
            os.fsync(arquivo.fileno())

        if validar_publicacao is not None:
            validar_publicacao()
        with _validar_e_reservar_publicacao(conexao, relatorio):
            if destino_confirmado is not None:
                _conferir_destino(destino, destino_confirmado)
            if conexao is not None and consultar_expiracao_ativa(conexao, relatorio["codigo"]) is None:
                raise RelatorioIndisponivel("A análise expirou ou não está mais disponível.")
            if destino_confirmado is None:
                _publicar_sem_sobrescrever(temporario, destino)
            else:
                temporario.replace(destino)
    finally:
        if criado:
            with suppress(OSError):
                temporario.unlink(missing_ok=True)
        with suppress(OSError):
            pasta.rmdir()

    return str(Path(caminho_destino))


def _neutralizar_celula(valor):
    if not isinstance(valor, str):
        return valor

    for caractere in valor:
        categoria = unicodedata.category(caractere)
        if categoria in {"Cc", "Cf"}:
            return "'" + valor
        if not caractere.isspace():
            prefixo = unicodedata.normalize("NFKC", caractere)
            if prefixo.startswith(("=", "+", "-", "@", "−")):
                return "'" + valor
            break
    return valor


def _gravar_relatorio(relatorio, arquivo):
    escritor = csv.writer(arquivo, delimiter=";", quoting=csv.QUOTE_ALL)

    def escrever(linha):
        escritor.writerow(_neutralizar_celula(valor) for valor in linha)

    escrever([
        "RELATORIO DE CONFERENCIA CEUMA"
    ])

    escrever([
        "Codigo da analise",
        relatorio["codigo"]
    ])

    escrever([
        "Data de criacao",
        relatorio["data_criacao"]
    ])

    escrever([
        "Status",
        relatorio["status"]
    ])

    escrever([])

    escrever(["DOCUMENTOS"])

    escrever([
        "ID",
        "Arquivo",
        "Tipo identificado"
    ])

    for documento in relatorio["documentos"]:
        escrever([
            documento["id"],
            documento["arquivo"],
            documento["tipo"]
        ])

    escrever([])

    escrever(["CAMPOS EXTRAIDOS"])

    escrever([
        "Arquivo", "Campo", "Valor", "Pagina", "Situacao", "Trecho de origem"
    ])

    for campo in relatorio.get("campos_extraidos", []):
        escrever([
            campo["arquivo"],
            campo["campo"],
            campo["valor"],
            campo["pagina"],
            campo["status"],
            campo["trecho_origem"]
        ])

    if estados := relatorio.get("estados_campos"):
        escrever([])
        escrever(["ESTADOS DOS CAMPOS"])
        escrever(["ID do documento", "Campo", "Situacao"])
        for item in estados:
            escrever([item["documento_id"], item["campo"], item["status"]])

    escrever([])

    escrever(["CONFERENCIAS"])

    escrever([
        "Campo",
        "Resultado",
        "Observacao"
    ])

    for conferencia in relatorio["conferencias"]:
        escrever([
            conferencia["campo"],
            conferencia["resultado"],
            conferencia["observacao"]
        ])

    if "modo" in relatorio:
        escrever([])
        escrever(["CONTEXTO DA CONFERENCIA"])
        escrever(["Modo", relatorio["modo"]])
        escrever(["Estado", relatorio["estado_conferencia"]])
        escrever(["Total de pares elegiveis", relatorio["total_pares_elegiveis"]])
        escrever(["Conferencia parcial", int(relatorio["conferencia_parcial"])])
        escrever(["Aguardando atribuicao", int(relatorio["aguardando_atribuicao"])])
        escrever(["Conferencias historicas sem atribuicao",
                  relatorio["total_conferencias_historicas"]])

        escrever([])
        escrever(["PESSOAS E DOCUMENTOS ATRIBUIDOS"])
        escrever(["ID da pessoa", "Papel", "ID do documento"])
        for pessoa in relatorio["pessoas"]:
            for documento_id in pessoa["documentos_ids"] or (None,):
                escrever([pessoa["pessoa_id"], pessoa["papel"], documento_id])
        escrever(["ID do documento", "Escopo"])
        for documento_id in relatorio["documentos_compartilhados"]:
            escrever([documento_id, "COMPARTILHADO"])
        for documento_id in relatorio["documentos_inconclusivos"]:
            escrever([documento_id, "INCONCLUSIVO"])

        escrever([])
        escrever(["CONFERENCIAS ESTRUTURADAS"])
        escrever(["ID da conferencia", "ID da pessoa", "ID do primeiro documento",
                  "ID do segundo documento", "Campo", "Resultado"])
        for item in relatorio["conferencias_estruturadas"]:
            escrever([item["conferencia_id"], item["pessoa_id"], item["documento_1_id"],
                      item["documento_2_id"], item["campo"], item["resultado"]])

    if pendentes := relatorio.get("leituras_pendentes"):
        escrever([])
        escrever(["LEITURAS PENDENTES DE REVISAO"])
        escrever(["ID do documento"])
        for documento_id in pendentes:
            escrever([documento_id])

    if itens := relatorio.get("itens_documentais"):
        escrever([])
        escrever(["ITENS DOCUMENTAIS"])
        escrever(["ID do item", "ID do documento", "Tipo", "Ordem", "ID da pessoa", "Revisao"])
        for item in itens:
            escrever([item["item_id"], item["documento_id"], item["tipo"], item["ordem"],
                      item["pessoa_id"], item["estado_revisao"]])

        escrever([])
        escrever(["CAMPOS DOS ITENS DOCUMENTAIS"])
        escrever(["ID do item", "Campo", "Valor", "Situacao", "Pagina", "Trecho de origem"])
        for item in itens:
            for campo in item["campos"]:
                escrever([item["item_id"], campo["nome_campo"], campo["valor"],
                          campo["status"], campo["pagina_origem"], campo["trecho_origem"]])

    escrever([])

    escrever([
        "Total de documentos",
        relatorio["total_documentos"]
    ])

    escrever([
        "Total de conferencias",
        relatorio["total_conferencias"]
    ])

    escrever([
        "Total de divergencias",
        relatorio["total_divergencias"]
    ])
