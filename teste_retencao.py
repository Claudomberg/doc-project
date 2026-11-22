
import sqlite3

from datetime import datetime, timedelta

from banco.conexao import criar_tabelas
from banco.operacoes import adicionar_documento
from banco.retencao import executar_limpeza


def testar():
    print("=== TESTE RETENCAO CEUMA ===")

    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        criar_tabelas(conexao)

        agora = datetime.now()

        analises = [
            (
                "AN-EXPIRADA",
                (agora - timedelta(hours=25)).isoformat(),
                (agora - timedelta(hours=1)).isoformat(),
                "EM_ANALISE"
            ),
            (
                "AN-ATIVA",
                agora.isoformat(),
                (agora + timedelta(hours=24)).isoformat(),
                "EM_ANALISE"
            )
        ]

        conexao.executemany(
            """
            INSERT INTO analises (
                codigo,
                data_criacao,
                data_expiracao,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            analises
        )

        conexao.commit()

        id_expirada = conexao.execute(
            """
            SELECT id FROM analises
            WHERE codigo = 'AN-EXPIRADA'
            """
        ).fetchone()[0]

        adicionar_documento(
            conexao,
            id_expirada,
            "documento_ficticio.pdf",
            "teste/documento_ficticio.pdf"
        )

        removidas = executar_limpeza(conexao)

        restantes = conexao.execute(
            "SELECT codigo FROM analises"
        ).fetchall()

        documentos = conexao.execute(
            "SELECT COUNT(*) FROM documentos"
        ).fetchone()[0]

        assert removidas == 1
        assert restantes == [("AN-ATIVA",)]
        assert documentos == 0

        print("Analises expiradas removidas:", removidas)
        print("Analises ativas preservadas: 1")
        print("Documentos vinculados removidos: OK")

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
