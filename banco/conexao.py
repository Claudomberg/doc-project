
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

from banco.esquema import ativar_chaves_estrangeiras, migrar_esquema, verificar_versao


# =====================================================
# CONFIGURAÇÕES
# =====================================================

PRAZO_RETENCAO_HORAS = 24

PASTA_PROJETO = Path(__file__).resolve().parent.parent
PASTA_DADOS = PASTA_PROJETO / "dados_ceuma"
CAMINHO_BANCO = PASTA_DADOS / "documentos.db"


# =====================================================
# CONEXÃO
# =====================================================

def conectar_banco(caminho=None):
    """
    Abre uma conexão com o banco SQLite.
    Se nenhum caminho for informado, utiliza o banco
    local da aplicação.
    """

    if caminho is None:
        PASTA_DADOS.mkdir(parents=True, exist_ok=True)
        caminho = CAMINHO_BANCO

    conexao = sqlite3.connect(caminho, timeout=30)
    conexao.row_factory = sqlite3.Row

    try:
        ativar_chaves_estrangeiras(conexao)
        verificar_versao(conexao)
    except Exception:
        conexao.close()
        raise
    return conexao


# =====================================================
# CRIAÇÃO DAS TABELAS
# =====================================================

def criar_tabelas(conexao):

    migrar_esquema(conexao, """
        CREATE TABLE IF NOT EXISTS analises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE,
            data_criacao TEXT NOT NULL,
            data_expiracao TEXT NOT NULL,
            responsavel TEXT,
            tipo_procedimento TEXT,
            status TEXT NOT NULL DEFAULT 'EM_ANDAMENTO'
        );

        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analise_id INTEGER NOT NULL,
            nome_arquivo TEXT NOT NULL,
            caminho_origem TEXT NOT NULL,
            tipo_identificado TEXT,
            quantidade_paginas INTEGER,
            status_leitura TEXT NOT NULL DEFAULT 'PENDENTE',

            FOREIGN KEY (analise_id)
                REFERENCES analises(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS campos_extraidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER NOT NULL,
            nome_campo TEXT NOT NULL,
            valor TEXT,
            pagina_origem INTEGER,
            trecho_origem TEXT,
            confianca REAL,
            status TEXT NOT NULL DEFAULT 'ENCONTRADO',

            FOREIGN KEY (documento_id)
                REFERENCES documentos(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS conferencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analise_id INTEGER NOT NULL,
            campo_verificado TEXT NOT NULL,
            resultado TEXT NOT NULL,
            observacao TEXT,

            FOREIGN KEY (analise_id)
                REFERENCES analises(id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_documentos_analise
        ON documentos(analise_id);

        CREATE INDEX IF NOT EXISTS idx_campos_documento
        ON campos_extraidos(documento_id);

        CREATE INDEX IF NOT EXISTS idx_conferencias_analise
        ON conferencias(analise_id);

        CREATE INDEX IF NOT EXISTS idx_analises_expiracao
        ON analises(data_expiracao);
    """)


# =====================================================
# INICIALIZAÇÃO
# =====================================================

def inicializar_banco():
    conexao = conectar_banco()

    try:
        criar_tabelas(conexao)
        limpar_analises_expiradas(conexao)
    finally:
        conexao.close()


def agora_utc():
    return datetime.now(timezone.utc)


# =====================================================
# CRIAÇÃO DE UMA ANÁLISE
# =====================================================

def criar_analise(
    conexao,
    responsavel=None,
    tipo_procedimento=None,
    *,
    modo="INDIVIDUAL"
):
    agora = agora_utc()

    expiracao = agora + timedelta(
        hours=PRAZO_RETENCAO_HORAS
    )

    with transacao_operacao(conexao):
        cursor = conexao.execute("""
            INSERT INTO analises (
                data_criacao,
                data_expiracao,
                responsavel,
                tipo_procedimento,
                status,
                modo
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            agora.isoformat(timespec="seconds"),
            expiracao.isoformat(timespec="seconds"),
            responsavel,
            tipo_procedimento,
            "EM_ANDAMENTO",
            modo
        ))

        identificador = cursor.lastrowid
        codigo = f"AN-{identificador:06d}"

        conexao.execute("""
            UPDATE analises
            SET codigo = ?
            WHERE id = ?
        """, (codigo, identificador))

    return codigo


# =====================================================
# LIMPEZA DE ANÁLISES EXPIRADAS
# =====================================================

def limpar_analises_expiradas(conexao):
    """Exclui análises vencidas em UTC em uma transação própria."""
    if conexao.in_transaction:
        raise sqlite3.ProgrammingError("A limpeza exige uma conexão sem transação ativa.")

    ativar_chaves_estrangeiras(conexao)

    with conexao:
        cursor = conexao.execute("""
            DELETE FROM analises
            WHERE julianday(data_expiracao) <= julianday(?)
        """, (agora_utc().isoformat(timespec="seconds"),))
    return cursor.rowcount


def consultar_expiracao_ativa(conexao, codigo):
    """Retorna o prazo em UTC apenas para uma análise que ainda está disponível."""
    registro = conexao.execute("""
        SELECT data_expiracao
        FROM analises
        WHERE codigo = ? AND julianday(data_expiracao) > julianday(?)
    """, (codigo, agora_utc().isoformat(timespec="seconds"))).fetchone()

    if registro is None:
        return None

    try:
        expiracao = datetime.fromisoformat(registro[0])
    except (TypeError, ValueError):
        return None
    # Os registros legados sem fuso são interpretados em UTC, como no SQLite.
    if expiracao.tzinfo is None:
        expiracao = expiracao.replace(tzinfo=timezone.utc)
    expiracao = expiracao.astimezone(timezone.utc)
    return expiracao if expiracao > agora_utc() else None


@contextmanager
def transacao_operacao(conexao, *, confirmar=True):
    """Isola uma operação de escrita sem confirmar uma transação do chamador."""
    ativar_chaves_estrangeiras(conexao)
    propria = not conexao.in_transaction
    if propria:
        conexao.execute("BEGIN IMMEDIATE")
    try:
        conexao.execute("SAVEPOINT ceuma_operacao")
        try:
            yield
        except BaseException:
            if conexao.in_transaction:
                conexao.execute("ROLLBACK TO ceuma_operacao")
                conexao.execute("RELEASE ceuma_operacao")
            raise
        conexao.execute("RELEASE ceuma_operacao")
        if propria and confirmar:
            conexao.commit()
    except BaseException:
        if propria:
            conexao.rollback()
        raise


class AnaliseIndisponivel(ValueError):
    """A análise solicitada expirou ou deixou de existir."""


def consultar_modo_ativo(conexao, analise_id):
    registro = conexao.execute(
        "SELECT codigo, modo FROM analises WHERE id = ?", (analise_id,),
    ).fetchone()
    if registro is None or consultar_expiracao_ativa(conexao, registro[0]) is None:
        raise AnaliseIndisponivel("Análise expirada ou indisponível.")
    return registro[1]


# =====================================================
# CONSULTA DE ANÁLISES
# =====================================================

def listar_analises(conexao):
    return conexao.execute("""
        SELECT
            id,
            codigo,
            data_criacao,
            data_expiracao,
            responsavel,
            tipo_procedimento,
            status,
            modo
        FROM analises
        ORDER BY id DESC
    """).fetchall()


# =====================================================
# EXECUÇÃO DIRETA PARA TESTE
# =====================================================

if __name__ == "__main__":
    inicializar_banco()
    print("Banco de dados inicializado com sucesso!")
