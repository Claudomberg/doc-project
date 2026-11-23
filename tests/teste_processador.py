
import sqlite3
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ceuma.banco.conexao import agora_utc, criar_tabelas
from ceuma.processamento.lote import processar_pasta


def testar():
    print("=== TESTE PROCESSADOR CEUMA ===")

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

        with TemporaryDirectory() as pasta:
            caminho = Path(pasta)

            (caminho / "identidade.pdf").write_bytes(b"fixture de identidade")
            (caminho / "certidao.pdf").write_bytes(b"fixture de certidao")

            def leitura_ficticia(arquivo, *, leitor_ocr):
                nome = Path(arquivo).name

                if nome == "identidade.pdf":
                    texto = (
                        "CARTEIRA DE IDENTIDADE\n"
                        "NOME: MARIA EDUARDA SILVA\n"
                        "DATA DE NASCIMENTO: 15/08/2005"
                    )
                else:
                    texto = (
                        "CERTIDAO DE NASCIMENTO\n"
                        "NOME: MARIA EDUARDA SOUSA\n"
                        "DATA DE NASCIMENTO: 15/08/2005"
                    )

                return {
                    "nome_arquivo": nome,
                    "caminho_origem": str(arquivo),
                    "quantidade_paginas": 1,
                    "paginas": [{
                        "numero": 1,
                        "texto": texto,
                        "metodo": "DIGITAL",
                        "linhas_ocr": []
                    }]
                }

            with patch(
                "ceuma.processamento.lote.ler_pdf",
                side_effect=leitura_ficticia
            ):
                resultado = processar_pasta(
                    conexao,
                    analise_id,
                    caminho
                )

        relatorio = resultado["relatorio"]

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

        assert relatorio["total_documentos"] == 2
        assert relatorio["total_conferencias"] == 2
        assert relatorio["total_divergencias"] == 1

        print("\nTodos os testes passaram!")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar()
