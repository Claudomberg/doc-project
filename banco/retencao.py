
from banco.conexao import limpar_analises_expiradas


def executar_limpeza(conexao):
    """Aplica a mesma limpeza em UTC usada pela conexão e pela interface."""
    return limpar_analises_expiradas(conexao)
