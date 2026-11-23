"""Associação conservadora de rótulos e valores por coordenadas do OCR.

Coordenadas devem estar na mesma escala. Não valida autenticidade.
"""
import re
import unicodedata
from datetime import datetime


_DISTANCIA_HORIZONTAL_MAXIMA = 32
_SOBREPOSICAO_VERTICAL_MINIMA = 0.50
_TOLERANCIA_SOBREPOSICAO_FRAGMENTOS = 2


def _normalizar(texto):
    texto = unicodedata.normalize('NFD', str(texto).upper())
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn')


def _caixa(linha):
    caixa = linha.get('caixa')
    if caixa is None and linha.get('poligono'):
        pontos = linha['poligono']
        caixa = [min(p[0] for p in pontos), min(p[1] for p in pontos),
                 max(p[0] for p in pontos), max(p[1] for p in pontos)]
    if caixa is None or len(caixa) != 4:
        return None
    try:
        x1, y1, x2, y2 = map(float, caixa)
    except (ValueError, TypeError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def _sobreposicao_horizontal(a, b):
    largura = min(a[2] - a[0], b[2] - b[0])
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / largura


def _sobreposicao_vertical(a, b):
    altura = min(a[3] - a[1], b[3] - b[1])
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1])) / altura


def _unir_caixas(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _observar_par(observar, decisao, etiqueta, indice, caixa, **medidas):
    observar(decisao, ids_rotulo=etiqueta['indices'], id_valor=indice,
             caixa_rotulo=etiqueta['caixa'], caixa_valor=caixa, **medidas)


def _proximos(a, b, distancia_maxima, tolerancia_sobreposicao):
    """Permite fragmentos lado a lado ou empilhados na mesma coluna."""
    altura = max(a[3] - a[1], b[3] - b[1])
    mesma_linha = abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) <= altura * 0.65
    espaco_horizontal = b[0] - a[2]
    if mesma_linha and -altura * 0.3 <= espaco_horizontal <= distancia_maxima:
        return True
    espaco_vertical = b[1] - a[3]
    return (-tolerancia_sobreposicao <= espaco_vertical <= distancia_maxima
            and a[1] < b[1] and a[3] < b[3]
            and _sobreposicao_horizontal(a, b) >= 0.20)


def _corresponde_rotulo(normalizado, alvos):
    return any(re.search(r'(?<!\w)' + re.escape(r) + r'(?!\w)', normalizado) for r in alvos)


def reconstruir_rotulos(linhas_ocr, rotulos, distancia_fragmentos=24, *,
                        tolerancia_sobreposicao_fragmentos=_TOLERANCIA_SOBREPOSICAO_FRAGMENTOS):
    """Reconstrói apenas sequências que formem um rótulo conhecido.

    Não junta palavras arbitrárias: exige proximidade geométrica e ordem.
    """
    return _reconstruir_rotulos(
        linhas_ocr, rotulos, distancia_fragmentos,
        tolerancia_sobreposicao_fragmentos=tolerancia_sobreposicao_fragmentos,
    )


def _reconstruir_rotulos(linhas_ocr, rotulos, distancia_fragmentos=24, *,
                         tolerancia_sobreposicao_fragmentos=_TOLERANCIA_SOBREPOSICAO_FRAGMENTOS,
                         observar=None):
    alvos = {_normalizar(r).strip() for r in rotulos if str(r).strip()}
    itens = []
    for indice, linha in enumerate(linhas_ocr):
        caixa = _caixa(linha)
        texto = str(linha.get('texto', '')).strip()
        if caixa and texto:
            itens.append({'texto': texto, 'normalizado': _normalizar(texto).strip(),
                          'caixa': caixa, 'indices': (indice,),
                          'grupo_espacial': linha.get('grupo_espacial')})
        elif observar is not None and texto and _corresponde_rotulo(_normalizar(texto), alvos):
            observar('CAIXA_INVALIDA', id_linha=indice, tipo='ROTULO')
    encontrados = []
    for item in itens:
        if _corresponde_rotulo(item['normalizado'], alvos):
            encontrados.append(item)
    # A reconstrução só considera fragmentos cuja concatenação seja um
    # prefixo de um rótulo esperado, evitando combinações sem sentido.
    for inicio in itens:
        fila = [(inicio, {inicio['indices'][0]})]
        for _ in range(3):  # no máximo quatro fragmentos por rótulo
            proxima_fila = []
            for atual, usados in fila:
                for seguinte in itens:
                    idx = seguinte['indices'][0]
                    if idx in usados or atual['grupo_espacial'] != seguinte['grupo_espacial']:
                        continue
                    proximos = _proximos(atual['caixa'], seguinte['caixa'], distancia_fragmentos,
                                         tolerancia_sobreposicao_fragmentos)
                    if not proximos and observar is None:
                        continue
                    combinado = atual['normalizado'] + ' ' + seguinte['normalizado']
                    if not any(r == combinado or r.startswith(combinado + ' ') for r in alvos):
                        continue
                    if not proximos:
                        observar('FRAGMENTOS_FORA_DA_PROXIMIDADE',
                                 ids_fragmentos=atual['indices'] + (idx,),
                                 caixa_prefixo=atual['caixa'], caixa_fragmento=seguinte['caixa'],
                                 distancia_horizontal=seguinte['caixa'][0] - atual['caixa'][2],
                                 distancia_vertical=seguinte['caixa'][1] - atual['caixa'][3])
                        continue
                    novo = {'texto': atual['texto'] + ' | ' + seguinte['texto'],
                            'normalizado': combinado,
                            'caixa': _unir_caixas(atual['caixa'], seguinte['caixa']),
                            'indices': atual['indices'] + (idx,),
                            'grupo_espacial': atual['grupo_espacial']}
                    if combinado in alvos:
                        encontrados.append(novo)
                    proxima_fila.append((novo, usados | {idx}))
            fila = proxima_fila
            if not fila:
                break
    unicos = {}
    for item in encontrados:
        chave = tuple(sorted(item['indices']))
        unicos[chave] = item
    return list(unicos.values())


def associar_rotulo_valor(linhas_ocr, rotulos, padrao_valor,
                         distancia_vertical_maxima=100,
                         sobreposicao_minima=0.20, *,
                         tolerancia_sobreposicao_fragmentos=_TOLERANCIA_SOBREPOSICAO_FRAGMENTOS):
    """Associa valor abaixo ou à direita de um rótulo com evidência geométrica."""
    return _associar_rotulo_valor(
        linhas_ocr, rotulos, padrao_valor, distancia_vertical_maxima, sobreposicao_minima,
        tolerancia_sobreposicao_fragmentos=tolerancia_sobreposicao_fragmentos,
    )


def _associar_rotulo_valor(linhas_ocr, rotulos, padrao_valor,
                          distancia_vertical_maxima=100,
                          sobreposicao_minima=0.20, *,
                          tolerancia_sobreposicao_fragmentos=_TOLERANCIA_SOBREPOSICAO_FRAGMENTOS,
                          observar=None, aceitar_par=None):
    etiquetas = _reconstruir_rotulos(
        linhas_ocr, rotulos, tolerancia_sobreposicao_fragmentos=tolerancia_sobreposicao_fragmentos,
        observar=observar,
    )
    padrao = re.compile(padrao_valor)
    valores = []
    for indice, linha in enumerate(linhas_ocr):
        caixa = _caixa(linha)
        texto = str(linha.get('texto', '')).strip()
        if caixa and padrao.fullmatch(texto):
            valores.append((indice, texto, caixa))
        elif observar is not None and not caixa and padrao.fullmatch(texto):
            observar('CAIXA_INVALIDA', id_linha=indice, tipo='CANDIDATO')
    if observar is not None:
        observar('ROTULOS_VALIDOS', quantidade=len(etiquetas))
        observar('CANDIDATOS_VALIDOS', quantidade=len(valores))
        if not etiquetas:
            observar('SEM_ROTULO')
        if not valores:
            observar('SEM_CANDIDATO')
    possibilidades = []
    horizontais = []
    for etiqueta in etiquetas:
        for indice, texto, caixa in valores:
            if etiqueta['grupo_espacial'] != linhas_ocr[indice].get('grupo_espacial'):
                continue
            if indice in etiqueta['indices']:
                if observar is not None:
                    _observar_par(observar, 'VALOR_NA_CAIXA_DO_ROTULO', etiqueta, indice, caixa)
                continue
            caixa_rotulo = etiqueta['caixa']
            distancia = caixa[1] - caixa_rotulo[3]
            espaco_horizontal = caixa[0] - caixa_rotulo[2]
            if espaco_horizontal >= 0 and distancia < 0 and caixa[3] > caixa_rotulo[1]:
                sobreposicao_vertical = _sobreposicao_vertical(caixa_rotulo, caixa)
                if espaco_horizontal > _DISTANCIA_HORIZONTAL_MAXIMA:
                    decisao = 'DISTANCIA_HORIZONTAL_EXCEDIDA'
                elif sobreposicao_vertical < _SOBREPOSICAO_VERTICAL_MINIMA:
                    decisao = 'ALINHAMENTO_VERTICAL_INSUFICIENTE'
                elif aceitar_par is not None and not aceitar_par(etiqueta, texto, caixa, 'HORIZONTAL'):
                    continue
                else:
                    decisao = 'PAR_ELEGIVEL'
                    horizontais.append((espaco_horizontal, -sobreposicao_vertical,
                                        etiqueta, indice, texto, caixa))
                if observar is not None:
                    _observar_par(observar, decisao, etiqueta, indice, caixa, eixo='HORIZONTAL',
                                  distancia_horizontal=espaco_horizontal,
                                  sobreposicao_vertical=sobreposicao_vertical)
                continue
            # Rótulos miúdos impressos rente ao valor saem do OCR com caixas que se cruzam alguns pixels.
            folga = 0.3 * min(caixa_rotulo[3] - caixa_rotulo[1], caixa[3] - caixa[1])
            if -folga <= distancia < 0 and caixa[1] > caixa_rotulo[1]:
                distancia = 0
            if not 0 <= distancia <= distancia_vertical_maxima:
                if observar is not None:
                    if distancia > distancia_vertical_maxima:
                        motivo = 'DISTANCIA_EXCEDIDA'
                    elif caixa[3] <= caixa_rotulo[1]:
                        motivo = 'VALOR_ACIMA'
                    else:
                        motivo = 'SOBREPOSICAO_VERTICAL'
                    _observar_par(observar, motivo, etiqueta, indice, caixa,
                                  distancia_vertical=distancia)
                continue
            sobreposicao = _sobreposicao_horizontal(caixa_rotulo, caixa)
            if (sobreposicao >= sobreposicao_minima and aceitar_par is not None
                    and not aceitar_par(etiqueta, texto, caixa, 'VERTICAL')):
                continue
            if observar is not None:
                decisao = ('SOBREPOSICAO_HORIZONTAL_INSUFICIENTE'
                           if sobreposicao < sobreposicao_minima else 'PAR_ELEGIVEL')
                _observar_par(observar, decisao, etiqueta, indice, caixa,
                              distancia_vertical=distancia, sobreposicao_horizontal=sobreposicao)
            if sobreposicao < sobreposicao_minima:
                continue
            possibilidades.append((distancia, -sobreposicao, etiqueta, indice, texto, caixa))
    if horizontais:
        melhor_horizontal = _selecionar_horizontal(horizontais, possibilidades, observar)
        if melhor_horizontal is None:
            return None
        if not possibilidades:
            return _resultado_associacao(melhor_horizontal, observar, eixo='HORIZONTAL')
    melhor = _selecionar_vertical(possibilidades, observar)
    if melhor is None:
        return None
    return _resultado_associacao(melhor, observar, eixo='VERTICAL')


def _rotulos_concorrentes(a, b):
    ids_a, ids_b = set(a['indices']), set(b['indices'])
    return not (ids_a <= ids_b or ids_b <= ids_a)


def _selecionar_horizontal(horizontais, verticais, observar):
    pares = horizontais + verticais
    ids_valores = tuple(dict.fromkeys(par[3] for par in pares))
    if len(ids_valores) > 1:
        if observar is not None:
            observar('AMBIGUIDADE_ENTRE_VALORES', ids_valores=ids_valores)
        return None

    # Uma versão mais longa pode representar os mesmos fragmentos de um rótulo curto.
    referencia = max(pares, key=lambda par: len(par[2]['indices']))
    concorrentes = tuple(dict.fromkeys(par[2]['indices'] for par in pares
                                     if _rotulos_concorrentes(par[2], referencia[2])))
    if concorrentes:
        if observar is not None:
            observar('AMBIGUIDADE_ENTRE_ROTULOS', id_valor=referencia[3],
                     ids_rotulos=(referencia[2]['indices'], *concorrentes))
        return None
    return max(horizontais, key=lambda par: len(par[2]['indices']))


def _selecionar_vertical(possibilidades, observar):
    if not possibilidades:
        return None
    # Não tratar duas versões do MESMO rótulo como duas disputas.
    melhores_por_valor = {}
    for candidato in possibilidades:
        chave = candidato[3]
        anterior = melhores_por_valor.get(chave)
        if anterior is None or (candidato[0], candidato[1], -len(candidato[2]['normalizado'])) < (anterior[0], anterior[1], -len(anterior[2]['normalizado'])):
            melhores_por_valor[chave] = candidato
    candidatos = sorted(melhores_por_valor.values(), key=lambda p: (p[0], p[1]))
    melhor = candidatos[0]
    if len(candidatos) > 1 and candidatos[1][0] - melhor[0] < 12:
        if observar is not None:
            observar('AMBIGUIDADE_ENTRE_VALORES', ids_valores=(melhor[3], candidatos[1][3]),
                     diferenca_vertical=candidatos[1][0] - melhor[0])
        return None
    # Dois rótulos distintos competindo pelo mesmo valor também são ambíguos.
    concorrentes = [p for p in possibilidades if p[3] == melhor[3]
                    and _rotulos_concorrentes(p[2], melhor[2])
                    and abs(p[0] - melhor[0]) < 12]
    if concorrentes:
        if observar is not None:
            observar('AMBIGUIDADE_ENTRE_ROTULOS', id_valor=melhor[3],
                     ids_rotulos=(melhor[2]['indices'], *(p[2]['indices'] for p in concorrentes)))
        return None
    return melhor


def _resultado_associacao(melhor, observar, *, eixo):
    distancia, sobreposicao_negativa, etiqueta, indice, texto, caixa = melhor
    distancia_vertical = caixa[1] - etiqueta['caixa'][3]
    if observar is not None:
        medidas = ({'distancia_horizontal': distancia,
                    'sobreposicao_vertical': -sobreposicao_negativa}
                   if eixo == 'HORIZONTAL' else {'sobreposicao_horizontal': -sobreposicao_negativa})
        _observar_par(observar, 'PAR_SELECIONADO', etiqueta, indice, caixa,
                      eixo=eixo, distancia_vertical=distancia_vertical, **medidas)
    return {'valor': texto, 'rotulo': etiqueta['texto'],
            'distancia_vertical': distancia_vertical, 'caixa_valor': list(caixa),
            'trecho_origem': etiqueta['texto'] + ' | ' + texto}


def associar_data_nascimento(linhas_ocr):
    resultado = associar_rotulo_valor(
        linhas_ocr, ['DATA DE NASCIMENTO', 'NASCIMENTO', 'DT NASC'],
        r'\d{1,2}[/.-]\d{1,2}[/.-]\d{4}')
    if resultado:
        try:
            datetime.strptime(resultado['valor'].replace('.', '/').replace('-', '/'), '%d/%m/%Y')
        except ValueError:
            return None
    return resultado
