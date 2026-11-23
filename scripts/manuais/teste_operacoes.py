
from ceuma.banco.conexao import (
    conectar_banco,
    criar_analise
)

from ceuma.banco.operacoes import (
    adicionar_documento,
    salvar_campo_extraido,
    registrar_conferencia
)


def testar():
    conexao = conectar_banco()

    try:
        print("=== TESTE DE OPERACOES CEUMA ===")

        # 1. Criar uma análise
        codigo = criar_analise(
            conexao,
            responsavel="Funcionario Teste",
            tipo_procedimento="MATRICULA"
        )

        analise = conexao.execute(
            "SELECT id FROM analises WHERE codigo = ?",
            (codigo,)
        ).fetchone()

        analise_id = analise["id"]
        print(f"\nAnalise criada: {codigo}")

        # 2. Adicionar um documento fictício
        documento_id = adicionar_documento(
            conexao,
            analise_id=analise_id,
            nome_arquivo="identidade_teste.pdf",
            caminho_origem="C:/TESTE/identidade_teste.pdf",
            tipo_identificado="RG",
            quantidade_paginas=1,
            status_leitura="CONCLUIDO"
        )

        print(f"Documento registrado: {documento_id}")

        # 3. Salvar um campo fictício
        campo_id = salvar_campo_extraido(
            conexao,
            documento_id=documento_id,
            nome_campo="Nome",
            valor="ALUNO TESTE",
            pagina_origem=1,
            trecho_origem="NOME: ALUNO TESTE",
            confianca=0.98
        )

        print(f"Campo registrado: {campo_id}")

        # 4. Registrar uma conferência
        conferencia_id = registrar_conferencia(
            conexao,
            analise_id=analise_id,
            campo_verificado="Nome",
            resultado="CONFERIDO",
            observacao="Teste com dados ficticios"
        )

        print(f"Conferencia registrada: {conferencia_id}")

        # 5. Verificar os registros
        documento = conexao.execute(
            "SELECT * FROM documentos WHERE id = ?",
            (documento_id,)
        ).fetchone()

        campo = conexao.execute(
            "SELECT * FROM campos_extraidos WHERE id = ?",
            (campo_id,)
        ).fetchone()

        conferencia = conexao.execute(
            "SELECT * FROM conferencias WHERE id = ?",
            (conferencia_id,)
        ).fetchone()

        assert documento is not None
        assert campo is not None
        assert conferencia is not None

        print("\nTodas as operacoes funcionaram!")

    finally:
        # Remover somente a análise criada neste teste
        if "analise_id" in locals():
            conexao.execute(
                "DELETE FROM analises WHERE id = ?",
                (analise_id,)
            )
            conexao.commit()
            print("Dados de teste removidos.")

        conexao.close()


if __name__ == "__main__":
    testar()
