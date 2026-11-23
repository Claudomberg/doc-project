
import sqlite3

from ceuma.banco.conexao import criar_tabelas
from ceuma.documentos.analisador import analisar_documento
from ceuma.banco.persistencia import salvar_resultado_analise


def testar():
    print("=== TESTE PERSISTENCIA CEUMA ===")

    # Banco SQLite em memória: não altera o banco real.
    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        criar_tabelas(conexao)

        conexao.execute("""
            INSERT INTO analises (
                codigo,
                data_criacao,
                data_expiracao,
                status
            )
            VALUES (?, ?, ?, ?)
        """, (
            "AN-TESTE",
            "2026-11-20T00:00:00",
            "2026-11-21T00:00:00",
            "EM_ANALISE"
        ))

        conexao.commit()

        analise_id = conexao.execute(
            "SELECT id FROM analises WHERE codigo = ?",
            ("AN-TESTE",)
        ).fetchone()[0]

        documento = {
            "nome_arquivo": "identidade_ficticia.pdf",
            "caminho_origem": "teste/identidade_ficticia.pdf",
            "quantidade_paginas": 1,
            "paginas": [
                {
                    "numero": 1,
                    "metodo": "DIGITAL",
                    "texto": (
                        "CARTEIRA DE IDENTIDADE\n"
                        "NOME: MARIA EDUARDA SILVA\n"
                        "CPF: 529.982.247-25\n"
                        "RG: 12.345.678-9\n"
                        "NASCIMENTO: 15/08/2005"
                    )
                }
            ]
        }

        resultado = analisar_documento(documento)

        persistencia = salvar_resultado_analise(
            conexao,
            analise_id,
            resultado
        )

        quantidade_documentos = conexao.execute(
            "SELECT COUNT(*) FROM documentos"
        ).fetchone()[0]

        quantidade_campos = conexao.execute(
            "SELECT COUNT(*) FROM campos_extraidos"
        ).fetchone()[0]

        print("Documento ID:", persistencia["documento_id"])
        print("Documentos salvos:", quantidade_documentos)
        print("Campos salvos:", quantidade_campos)

        assert quantidade_documentos == 1
        assert quantidade_campos == 4
        assert persistencia["campos_salvos"] == 4

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
