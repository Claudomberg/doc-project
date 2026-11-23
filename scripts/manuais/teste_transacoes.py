
import sqlite3

from ceuma.banco.conexao import criar_tabelas
from ceuma.banco.operacoes import adicionar_documento


def testar():
    print("=== TESTE TRANSACOES CEUMA ===")

    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        criar_tabelas(conexao)

        cursor = conexao.execute(
            """
            INSERT INTO analises (
                codigo,
                data_criacao,
                data_expiracao,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "AN-TESTE",
                "2026-11-20T00:00:00",
                "2026-11-21T00:00:00",
                "EM_ANALISE"
            )
        )

        conexao.commit()
        analise_id = cursor.lastrowid

        # Inicia uma transação.
        conexao.execute("BEGIN")

        adicionar_documento(
            conexao=conexao,
            analise_id=analise_id,
            nome_arquivo="documento_teste.pdf",
            caminho_origem="teste/documento_teste.pdf",
            confirmar=False
        )

        quantidade_antes = conexao.execute(
            "SELECT COUNT(*) FROM documentos"
        ).fetchone()[0]

        assert quantidade_antes == 1

        # Simula uma falha e desfaz a operação.
        conexao.rollback()

        quantidade_depois = conexao.execute(
            "SELECT COUNT(*) FROM documentos"
        ).fetchone()[0]

        assert quantidade_depois == 0

        print("Documento inserido na transacao: OK")
        print("Rollback executado: OK")
        print("Nenhum documento permaneceu salvo: OK")

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
