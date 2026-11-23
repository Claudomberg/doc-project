
import sqlite3

from ceuma.banco.conexao import criar_analise, criar_tabelas
from ceuma.banco.operacoes import (
    adicionar_documento,
    salvar_campo_extraido,
    registrar_conferencia
)
from ceuma.conferencia.automatica import conferir_analise


def testar():
    print("=== TESTE CONFERENCIA MANUAL CEUMA ===")

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

        # Simula uma conferência humana.
        registrar_conferencia(
            conexao=conexao,
            analise_id=analise_id,
            campo_verificado="DOCUMENTACAO",
            resultado="REVISADO",
            observacao="MANUAL: Revisão realizada"
        )

        # Executa duas conferências automáticas.
        conferir_analise(conexao, analise_id)
        conferir_analise(conexao, analise_id)

        registros = conexao.execute(
            """
            SELECT resultado, observacao
            FROM conferencias
            WHERE analise_id = ?
            ORDER BY id
            """,
            (analise_id,)
        ).fetchall()

        manuais = [
            r for r in registros
            if r[1].startswith("MANUAL:")
        ]

        automaticos = [
            r for r in registros
            if r[1].startswith("AUTO:")
        ]

        assert len(manuais) == 1
        assert len(automaticos) == 1
        assert len(registros) == 2

        print("Conferencias manuais:", len(manuais))
        print("Conferencias automaticas:", len(automaticos))
        print("Total de registros:", len(registros))

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
