"""Testes fictícios da reconstrução espacial, sem dados pessoais."""
from analise.associador_espacial import reconstruir_rotulos, associar_rotulo_valor, associar_data_nascimento


def item(texto, x1, y1, x2, y2):
    return {'texto': texto, 'caixa': [x1, y1, x2, y2], 'confianca': 0.95}


def testar():
    linhas = [item('DATA DE', 10, 10, 80, 25),
              item('NASCIMENTO', 10, 29, 100, 44),
              item('15/08/2005', 10, 55, 100, 70)]
    assert associar_data_nascimento(linhas)['valor'] == '15/08/2005'
    print('Data com rotulo fragmentado: OK')

    linhas = [item('REGISTRO', 10, 10, 85, 25),
              item('GERAL', 10, 30, 70, 45),
              item('1234567-8', 10, 55, 100, 70)]
    assert associar_rotulo_valor(linhas, ['REGISTRO GERAL'], r'\d{7}-\d')['valor'] == '1234567-8'
    print('RG com rotulo fragmentado: OK')

    linhas = [item('DATA DE', 10, 10, 80, 25),
              item('NASCIMENTO', 300, 30, 400, 45),
              item('15/08/2005', 10, 55, 100, 70)]
    assert associar_data_nascimento(linhas) is None
    print('Fragmentos distantes rejeitados: OK')

    linhas = [item('DATA DE NASCIMENTO', 10, 10, 150, 25),
              item('15/08/2005', 10, 40, 100, 55),
              item('16/08/2005', 10, 45, 100, 60)]
    assert associar_data_nascimento(linhas) is None
    print('Valores ambiguos rejeitados: OK')

    linhas = [item('DATA DE NASCIMENTO', 10, 10, 150, 25),
              item('15/08/2005', 10, 40, 100, 55)]
    assert associar_data_nascimento(linhas)['valor'] == '15/08/2005'
    print('Rotulo completo preservado: OK')
    print('Todos os testes passaram!')


if __name__ == '__main__':
    testar()
