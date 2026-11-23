
import sqlite3

from ceuma.banco.conexao import criar_analise, criar_tabelas
from ceuma.banco.operacoes import (
    adicionar_documento,
    salvar_campo_extraido
)
from ceuma.conferencia.automatica import conferir_analise


def testar():
    print("=== TESTE CONFERENCIAS DUPLICADAS CEUMA ===")

    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        criar_tabelas(conexao)

        codigo = criar_analise(conexao)
        analise_id = conexao.execute(
            "SELECT id FROM analises WHERE codigo = ?", (codigo,),
        ).fetchone()[0]

        doc1 = adicionar_documento(
            conexao, analise_id,
            "identidade_ficticia.pdf",
            "teste/identidade_ficticia.pdf"
        )

        doc2 = adicionar_documento(
            conexao, analise_id,
            "certidao_ficticia.pdf",
            "teste/certidao_ficticia.pdf"
        )

        salvar_campo_extraido(
            conexao, doc1,
            "NOME", "MARIA EDUARDA SILVA"
        )

        salvar_campo_extraido(
            conexao, doc2,
            "NOME", "MARIA EDUARDA SOUSA"
        )

        conferir_analise(
            conexao, analise_id,
            confirmar=False
        )

        primeira = conexao.execute(
            """
            SELECT COUNT(*) FROM conferencias
            WHERE analise_id = ?
            """,
            (analise_id,)
        ).fetchone()[0]

        conferir_analise(
            conexao, analise_id,
            confirmar=False
        )

        segunda = conexao.execute(
            """
            SELECT COUNT(*) FROM conferencias
            WHERE analise_id = ?
            """,
            (analise_id,)
        ).fetchone()[0]

        assert primeira == 1
        assert segunda == 1

        conexao.commit()

        print("Primeira conferencia:", primeira)
        print("Segunda conferencia:", segunda)
        print("Duplicacoes encontradas: 0")

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
