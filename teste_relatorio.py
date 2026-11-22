
import sqlite3
from datetime import timedelta

from banco.conexao import agora_utc, criar_tabelas
from banco.operacoes import (
    adicionar_documento,
    salvar_campo_extraido
)
from analise.conferidor_banco import conferir_analise
from analise.relatorio import gerar_relatorio


def testar():
    print("=== TESTE RELATORIO CEUMA ===")

    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        criar_tabelas(conexao)
        agora = agora_utc()

        cursor = conexao.execute(
            """
            INSERT INTO analises (
                codigo, data_criacao,
                data_expiracao, status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "AN-TESTE",
                agora.isoformat(),
                (agora + timedelta(hours=24)).isoformat(),
                "EM_ANALISE"
            )
        )

        conexao.commit()
        analise_id = cursor.lastrowid

        doc1 = adicionar_documento(
            conexao, analise_id,
            "identidade_ficticia.pdf",
            "teste/identidade_ficticia.pdf",
            tipo_identificado="DOCUMENTO_IDENTIDADE"
        )

        doc2 = adicionar_documento(
            conexao, analise_id,
            "certidao_ficticia.pdf",
            "teste/certidao_ficticia.pdf",
            tipo_identificado="CERTIDAO_NASCIMENTO"
        )

        dados = [
            (doc1, "NOME", "MARIA EDUARDA SILVA"),
            (doc1, "DATA_NASCIMENTO", "15/08/2005"),
            (doc2, "NOME", "MARIA EDUARDA SOUSA"),
            (doc2, "DATA_NASCIMENTO", "15/08/2005")
        ]

        for documento_id, campo, valor in dados:
            salvar_campo_extraido(
                conexao,
                documento_id,
                campo,
                valor
            )

        conferir_analise(conexao, analise_id)

        relatorio = gerar_relatorio(
            conexao, analise_id
        )

        print("Analise:", relatorio["codigo"])
        print(
            "Documentos:",
            relatorio["total_documentos"]
        )
        print(
            "Conferencias:",
            relatorio["total_conferencias"]
        )
        print(
            "Divergencias:",
            relatorio["total_divergencias"]
        )

        assert relatorio["codigo"] == "AN-TESTE"
        assert relatorio["total_documentos"] == 2
        assert relatorio["total_conferencias"] == 2
        assert relatorio["total_divergencias"] == 1

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
