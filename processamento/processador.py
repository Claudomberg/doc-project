
import hashlib
import sqlite3
from pathlib import Path

from leitor.leitor_pdf import ler_pdf
from leitor.leitor_ocr import LeitorOCR, LeitorOCRIsolado
from leitor.leitor_adaptativo import criar_leitor_ocr, modo_ocr
from analise.analisador_documento import analisar_documento
from analise.persistencia import salvar_resultado_analise
from analise.conferidor_banco import conferir_analise
from analise.relatorio import RelatorioIndisponivel, recuperar_relatorio
from banco.conexao import AnaliseIndisponivel, consultar_modo_ativo
from banco.esquema import ativar_chaves_estrangeiras


class ErroProcessamento(RuntimeError):
    """Falha na leitura ou análise de um PDF."""

    def __init__(self, *, numero_documento=None):
        if numero_documento is None:
            mensagem = "Não foi possível listar os PDFs da pasta selecionada."
        else:
            mensagem = f"Não foi possível ler ou analisar o documento #{numero_documento} do lote."
        super().__init__(mensagem)


class ErroReleitura(ErroProcessamento):
    def __init__(self, mensagem):
        RuntimeError.__init__(self, mensagem)


class ErroPersistencia(RuntimeError):
    """Falha ao consultar, gravar ou confirmar os resultados no SQLite."""


class ErroEstadoAnalise(ValueError):
    """A análise não está disponível para receber um novo lote."""


def processar_lote(
    conexao, analise_id, caminho_pasta=None, *, orientador=None, arquivos=None, progresso=None,
    releitura_sha256=None,
):
    """
    Processa PDFs de uma pasta ou uma seleção explícita, salva os resultados
    e realiza a conferência entre documentos.

    Todas as alterações são confirmadas juntas.
    """
    if conexao.in_transaction:
        raise ErroPersistencia("A conexão já possui uma transação em andamento.")

    if arquivos is None:
        pasta = Path(caminho_pasta)
        try:
            if not pasta.is_dir():
                raise ValueError("A pasta informada não existe.")
            arquivos = sorted(
                arquivo for arquivo in pasta.iterdir()
                if arquivo.is_file() and arquivo.suffix.lower() == ".pdf"
            )
        except OSError:
            raise ErroProcessamento() from None
    else:
        arquivos = list(dict.fromkeys(map(Path, arquivos)))
        if any(arquivo.suffix.lower() != ".pdf" for arquivo in arquivos):
            raise ValueError("A seleção deve conter somente PDFs.")
    if not arquivos:
        raise ValueError("Nenhum PDF encontrado.")

    if releitura_sha256 is not None and len(arquivos) != 1:
        raise ValueError("A releitura avançada aceita um documento por vez.")

    documentos_processados = []
    origens_releitura = {}
    duplicatas = []
    arquivos_por_hash = {}
    if releitura_sha256 is not None:
        leitor_ocr = LeitorOCRIsolado()
    elif modo_ocr() == "paddle":
        leitor_ocr = LeitorOCR()
    else:
        leitor_ocr = (criar_leitor_ocr() if orientador is None
                      else criar_leitor_ocr(orientador=orientador))

    try:
        ativar_chaves_estrangeiras(conexao)
        # Reserva a escrita antes de verificar o estado. Outra conexão só toma
        # essa decisão depois que o lote atual confirmar ou reverter.
        conexao.execute("BEGIN IMMEDIATE")
        registro = conexao.execute("""
            SELECT a.status, EXISTS (
                SELECT 1 FROM documentos d WHERE d.analise_id = a.id
            )
            FROM analises a
            WHERE a.id = ?
        """, (analise_id,)).fetchone()
        if registro is None:
            raise ErroEstadoAnalise("Análise não encontrada.")
        status, tem_documentos = registro
        if status == "CONCLUIDA":
            raise ErroEstadoAnalise("Esta análise já foi concluída. Use Recuperar relatório.")
        if tem_documentos:
            raise ErroEstadoAnalise("Esta análise já possui documentos cadastrados.")

        consultar_modo_ativo(conexao, analise_id)

        for numero, arquivo in enumerate(arquivos, 1):
            if progresso is not None:
                progresso(numero, len(arquivos))
            try:
                with arquivo.open("rb") as pdf:
                    resumo = hashlib.file_digest(pdf, "sha256").digest()
                if releitura_sha256 is not None and resumo != releitura_sha256:
                    raise ErroReleitura("O PDF original mudou desde a análise. Selecione-o para iniciar uma nova análise.")
                if resumo in arquivos_por_hash:
                    numero_original, original = arquivos_por_hash[resumo]
                    mensagem = (
                        f"Cópia ignorada: documento #{numero} tem conteúdo idêntico "
                        f"ao documento #{numero_original} do lote."
                    )
                    duplicatas.append({
                        "arquivo": arquivo.name, "duplicado_de": original, "mensagem": mensagem,
                    })
                    continue
                arquivos_por_hash[resumo] = numero, arquivo.name

                leitura = ler_pdf(str(arquivo), leitor_ocr=leitor_ocr)
                resultado = analisar_documento(leitura)
            except ErroReleitura:
                raise
            except Exception:
                raise ErroProcessamento(numero_documento=numero) from None

            salvamento = salvar_resultado_analise(
                conexao=conexao,
                analise_id=analise_id,
                resultado=resultado,
                confirmar=False
            )

            documento_id = salvamento["documento_id"]
            if releitura_sha256 is not None:
                # Uma tentativa sem campos confirmados não gera uma nova análise concluída.
                tem_campos = conexao.execute("""
                    SELECT 1 FROM campos_extraidos
                    WHERE documento_id = ? AND status IN ('ENCONTRADO', 'TITULAR_CONFIRMADO')
                      AND length(trim(valor)) > 0
                    UNION ALL
                    SELECT 1 FROM campos_item c JOIN itens_documentais i ON i.id = c.item_id
                    WHERE i.documento_id = ? AND c.status = 'ENCONTRADO' AND length(trim(c.valor)) > 0
                    LIMIT 1
                """, (documento_id, documento_id)).fetchone()
                if not tem_campos or resultado["identificacao"]["status"] != "IDENTIFICADO":
                    raise ErroReleitura("O OCR avançado não produziu uma identificação com campos confirmados.")
            else:
                diagnosticos = [pagina.get("diagnostico_ocr", {}) for pagina in leitura["paginas"]]
                revisao = set(resultado.get("pendencias_leitura", ()))
                ocr_insuficiente = revisao.intersection({
                    "CLASSIFICACAO_INCONCLUSIVA", "NOME_NAO_CONFIRMADO",
                    "CAMPO_REQUER_REVISAO", "SEM_TEXTO_UTIL", "CPF_CIN_NAO_CONFIRMADO",
                })
                if any(
                    (dia.get("metricas", {}).get("tesseract") and (dia.get("pendencias") or ocr_insuficiente))
                    or "ORCAMENTO_ESGOTADO" in dia.get("pendencias", ())
                    for dia in diagnosticos
                ):
                    origens_releitura[documento_id] = (arquivo.resolve(), resumo)

            documentos_processados.append({
                "arquivo": arquivo.name,
                "documento_id": salvamento["documento_id"],
                "campos_salvos": salvamento["campos_salvos"]
            })

        conferencias = conferir_analise(
            conexao=conexao,
            analise_id=analise_id,
            confirmar=False
        )

        # Marca a análise como concluída somente
        # depois de processar todos os documentos.
        conexao.execute(
            """
            UPDATE analises
            SET status = ?
            WHERE id = ?
            """,
            ("CONCLUIDA", analise_id)
        )

        # Confirma documentos, campos, conferências
        # e status na mesma transação.
        conexao.commit()


    except (ErroProcessamento, ErroEstadoAnalise, AnaliseIndisponivel):
        conexao.rollback()
        raise
    except Exception as erro:
        conexao.rollback()
        codigo_sqlite = getattr(erro, "sqlite_errorcode", 0) or 0
        if (codigo_sqlite & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise ErroPersistencia("O banco está ocupado. Tente novamente após o lote em andamento.") from None
        raise ErroPersistencia("Não foi possível gravar ou confirmar o lote no banco local.") from None
    finally:
        if releitura_sha256 is not None:
            leitor_ocr.fechar()

    return {
        "documentos": documentos_processados,
        "conferencias": conferencias,
        "duplicatas": duplicatas,
        "origens_releitura": origens_releitura,
    }


def processar_pasta(conexao, analise_id, caminho_pasta):
    """Processa a pasta e monta o relatório depois de confirmar o lote."""
    try:
        resultado = processar_lote(conexao, analise_id, caminho_pasta)
    except AnaliseIndisponivel as erro:
        raise RelatorioIndisponivel(str(erro)) from None
    resultado["relatorio"], _ = recuperar_relatorio(conexao, analise_id=analise_id)
    return resultado
