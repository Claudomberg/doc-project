
from ceuma.banco.conexao import (
    conectar_banco,
    criar_analise
)


def testar_banco():
    conexao = conectar_banco()
    codigo = None

    try:
        print("=== TESTE DO BANCO CEUMA ===")

        # 1. Criar uma análise fictícia
        codigo = criar_analise(
            conexao,
            responsavel="Funcionario Teste",
            tipo_procedimento="MATRICULA"
        )

        print(f"\nAnálise criada: {codigo}")

        # 2. Recuperar o ID da análise
        analise = conexao.execute("""
            SELECT id FROM analises
            WHERE codigo = ?
        """, (codigo,)).fetchone()

        analise_id = analise["id"]

        # 3. Inserir um documento fictício
        cursor = conexao.execute("""
            INSERT INTO documentos (
                analise_id,
                nome_arquivo,
                caminho_origem,
                tipo_identificado,
                quantidade_paginas,
                status_leitura
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            analise_id,
            "documento_teste.pdf",
            "C:/documentos/documento_teste.pdf",
            "Documento_Identidade",
            2,
            "CONCLUIDO"
        ))

        documento_id = cursor.lastrowid

        # 4. Inserir um campo fictício
        conexao.execute("""
            INSERT INTO campos_extraidos (
                documento_id,
                nome_campo,
                valor,
                pagina_origem,
                trecho_origem,
                confianca,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            documento_id,
            "Nome",
            "ALUNO TESTE",
            1,
            "Nome: ALUNO TESTE",
            0.98,
            "ENCONTRADO"
        ))

        conexao.commit()

        # 5. Consultar os dados relacionados
        resultado = conexao.execute("""
            SELECT
                a.codigo,
                d.nome_arquivo,
                c.nome_campo,
                c.valor
            FROM analises a
            JOIN documentos d
                ON d.analise_id = a.id
            JOIN campos_extraidos c
                ON c.documento_id = d.id
            WHERE a.codigo = ?
        """, (codigo,)).fetchone()

        print("\n=== DADOS RELACIONADOS ===")
        print(f"Análise: {resultado['codigo']}")
        print(f"Documento: {resultado['nome_arquivo']}")
        print(f"Campo: {resultado['nome_campo']}")
        print(f"Valor: {resultado['valor']}")

        print("\nRelacionamentos funcionando!")

        # 6. Testar exclusão em cascata
        conexao.execute("""
            DELETE FROM analises
            WHERE codigo = ?
        """, (codigo,))

        conexao.commit()

        quantidade = conexao.execute("""
            SELECT COUNT(*)
            FROM documentos
            WHERE analise_id = ?
        """, (analise_id,)).fetchone()[0]

        print(
            "\nDocumentos restantes após excluir a análise:",
            quantidade
        )

        if quantidade == 0:
            print("Exclusão em cascata funcionando!")

    except Exception as erro:
        conexao.rollback()
        print(f"\nErro no teste: {erro}")

    finally:
        conexao.close()


if __name__ == "__main__":
    testar_banco()
