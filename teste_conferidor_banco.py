
import sqlite3

from banco.conexao import criar_analise, criar_tabelas
from banco.operacoes import (
    adicionar_documento,
    salvar_campo_extraido
)
from analise.conferidor_banco import conferir_analise


def testar():
    print("=== TESTE CONFERIDOR BANCO CEUMA ===")

    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        criar_tabelas(conexao)

        codigo = criar_analise(conexao)
        analise_id = conexao.execute(
            "SELECT id FROM analises WHERE codigo = ?", (codigo,),
        ).fetchone()[0]

        documento_1 = adicionar_documento(
            conexao,
            analise_id,
            "identidade_ficticia.pdf",
            "teste/identidade_ficticia.pdf"
        )

        documento_2 = adicionar_documento(
            conexao,
            analise_id,
            "certidao_ficticia.pdf",
            "teste/certidao_ficticia.pdf"
        )

        dados = [
            (documento_1, "NOME", "MARIA EDUARDA SILVA"),
            (documento_1, "DATA_NASCIMENTO", "15/08/2005"),
            (documento_2, "NOME", "MARIA EDUARDA SOUSA"),
            (documento_2, "DATA_NASCIMENTO", "15/08/2005")
        ]

        for documento_id, campo, valor in dados:
            salvar_campo_extraido(
                conexao=conexao,
                documento_id=documento_id,
                nome_campo=campo,
                valor=valor
            )

        resultados = conferir_analise(
            conexao,
            analise_id
        )

        for resultado in resultados:
            print(
                resultado["campo"],
                "|",
                resultado["resultado"]
            )

        assert len(resultados) == 2

        por_campo = {
            item["campo"]: item["resultado"]
            for item in resultados
        }

        assert por_campo["NOME"] == "DIVERGENTE"
        assert (
            por_campo["DATA_NASCIMENTO"]
            == "COMPATIVEL"
        )

        quantidade = conexao.execute(
            """
            SELECT COUNT(*)
            FROM conferencias
            WHERE analise_id = ?
            """,
            (analise_id,)
        ).fetchone()[0]

        assert quantidade == 2

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
