
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from banco.conexao import criar_analise, criar_tabelas
from processamento.processador import ErroProcessamento, processar_pasta


def testar():
    print("=== TESTE ROLLBACK PROCESSADOR CEUMA ===")

    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        criar_tabelas(conexao)

        codigo = criar_analise(conexao)
        analise_id = conexao.execute(
            "SELECT id FROM analises WHERE codigo = ?", (codigo,),
        ).fetchone()[0]

        with TemporaryDirectory() as pasta:
            caminho = Path(pasta)

            (caminho / "documento_1.pdf").write_bytes(b"fixture de leitura valida")
            (caminho / "documento_2.pdf").write_bytes(b"fixture de falha de leitura")

            def leitura_simulada(arquivo, *, leitor_ocr):
                nome = Path(arquivo).name

                if nome == "documento_2.pdf":
                    raise RuntimeError(
                        "Falha simulada na leitura"
                    )

                return {
                    "nome_arquivo": nome,
                    "caminho_origem": str(arquivo),
                    "quantidade_paginas": 1,
                    "paginas": [{
                        "numero": 1,
                        "texto": (
                            "CARTEIRA DE IDENTIDADE\n"
                            "NOME: MARIA EDUARDA SILVA\n"
                            "DATA DE NASCIMENTO: 15/08/2005"
                        ),
                        "metodo": "DIGITAL",
                        "linhas_ocr": []
                    }]
                }

            with patch(
                "processamento.processador.ler_pdf",
                side_effect=leitura_simulada
            ):
                try:
                    processar_pasta(
                        conexao,
                        analise_id,
                        caminho
                    )
                except ErroProcessamento as erro:
                    assert "documento #2 do lote" in str(erro)
                    assert "Falha simulada na leitura" not in str(erro)
                    print("Falha simulada detectada: OK")
                else:
                    raise AssertionError(
                        "O processador deveria ter falhado."
                    )

        documentos = conexao.execute(
            """
            SELECT COUNT(*)
            FROM documentos
            WHERE analise_id = ?
            """,
            (analise_id,)
        ).fetchone()[0]

        campos = conexao.execute(
            """
            SELECT COUNT(*)
            FROM campos_extraidos c
            JOIN documentos d
                ON d.id = c.documento_id
            WHERE d.analise_id = ?
            """,
            (analise_id,)
        ).fetchone()[0]

        conferencias = conexao.execute(
            """
            SELECT COUNT(*)
            FROM conferencias
            WHERE analise_id = ?
            """,
            (analise_id,)
        ).fetchone()[0]

        assert documentos == 0
        assert campos == 0
        assert conferencias == 0

        print("Documentos restantes:", documentos)
        print("Campos restantes:", campos)
        print("Conferencias restantes:", conferencias)

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()

