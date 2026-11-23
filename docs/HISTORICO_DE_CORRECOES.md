# Histórico de problemas e correções

Este documento registra **cada problema encontrado** no CEUMA, **como ele foi investigado**, **o que foi mudado** e **qual foi o efeito medido** antes e depois. A ideia não é só listar correções: é mostrar o **método**, para que ele possa ser repetido nos próximos problemas.

> Para entender o funcionamento geral antes de ler este documento, veja [COMO_FUNCIONA.md](COMO_FUNCIONA.md).

---

## Sumário

1. [Ponto de partida](#1-ponto-de-partida)
2. [Metodologia usada](#2-metodologia-usada)
3. [Linha do tempo (commits)](#3-linha-do-tempo-commits)
4. [Parte A: organização do projeto](#parte-a-organização-do-projeto)
5. [Parte B: rodar no macOS](#parte-b-rodar-no-macos)
6. [Parte C: OCR de documentos fotografados](#parte-c-ocr-de-documentos-fotografados)
7. [Parte D: campos que ainda faltavam no RG](#parte-d-campos-que-ainda-faltavam-no-rg)
8. [Resumo da evolução em números](#8-resumo-da-evolução-em-números)
9. [Problemas conhecidos ainda em aberto](#9-problemas-conhecidos-ainda-em-aberto)
10. [Lições aprendidas](#10-lições-aprendidas)

---

## 1. Ponto de partida

**Sintoma relatado:** "o projeto não funciona; ele só consegue ler algumas poucas palavras, não todas, por isso não consegue classificar corretamente". Na interface aparecia **"Nenhum campo recuperado para este documento."**

**Documento de teste:** um RG real **fotografado com celular** e convertido em PDF, com o cartão aberto (frente e verso lado a lado) sobre um fundo vermelho. Os dados pessoais dele são omitidos neste documento, e o arquivo **não** está no repositório. É um caso difícil e muito comum na vida real:

- fundo de segurança verde com tramas e um brasão em marca d'água;
- fundo vermelho do lado de fora do cartão;
- texto pequeno, rótulos miúdos e o número do RG impresso em **tinta vermelha**;
- resolução da foto de ~145 DPI.

**Resultado inicial com esse documento:**

| Medida | Valor |
|---|---|
| Linhas lidas pelo OCR | **2**, ambas lixo (ex.: `Po gro :s e o o MD CGU a`) |
| Tipo identificado | `DOCUMENTO_DESCONHECIDO` / `NAO_IDENTIFICADO` |
| Campos extraídos | **0** |
| Chamadas ao Tesseract | 1 (0,3 s) |

**Testes automatizados no início:** 372 passando e 2 pulados. Os testes usam OCR simulado, por isso passavam mesmo com o OCR real falhando. É uma lição importante: **testes verdes não garantem que o sistema funciona com dados reais** ([seção 10](#10-lições-aprendidas)).

---

## 2. Metodologia usada

As correções seguiram sempre o mesmo ciclo:

1. **Diagnóstico por leitura de código.** Antes de rodar qualquer coisa, percorremos o fluxo (leitura → OCR → identificação → extração) e listamos hipóteses de causa, em ordem de impacto provável.
2. **Linha de base.** Antes de qualquer mudança, rodamos a suíte de testes e anotamos o resultado (372 passando). Toda mudança precisa manter a suíte verde.
3. **Experimento isolado.** Para cada hipótese, um script pequeno, fora do projeto, que muda **uma variável por vez** e mede o efeito: cor, resolução, modo do Tesseract, conversão para cinza. Os números das tabelas abaixo saíram desses experimentos.
4. **Medir com métrica objetiva.** Em vez de "parece melhor", contamos **quantas de 18 palavras-chave do RG** o OCR recuperou (REGISTRO, NOME, FILIAÇÃO, CPF, as datas etc.), quantas linhas úteis saíram e quais campos foram extraídos corretamente.
5. **Implementar a menor mudança que resolve.** Sem reescrever o que funciona.
6. **Teste automatizado para cada regra nova,** sempre com dados fictícios.
7. **Verificar efeitos colaterais.** Rodar de novo a suíte inteira, os PDFs fictícios da demo e o documento real.
8. **Commit por objetivo,** com mensagem explicando o porquê.

E uma regra transversal: **privacidade**. O documento real só foi lido do lugar onde estava. Nenhum dado dele foi para código, testes, commits ou documentação, e as imagens temporárias geradas na análise foram apagadas.

---

## 3. Linha do tempo (commits)

| Commit | Tipo | O que fez |
|---|---|---|
| `51beb7c` | refactor | Reorganiza o projeto no pacote `src/ceuma/` |
| `948214f` | chore | Cria `pyproject.toml`; corrige `.gitignore` que escondia código |
| `4b48f39` | docs | README com a nova estrutura |
| `63880a8` | fix | Roda no macOS: busca do Tesseract e fonte da interface |
| `88dce83` | docs | README com preparação no macOS |
| `c59873e` | fix | OCR de documentos fotografados (cor, resolução, layout, limpeza, identificador, prazos) |
| `c5132fb` | fix | CPF, número do RG e data de expedição na leitura base |

---

## Parte A: organização do projeto

### A1. Estrutura de pastas ilegível

**Problema.**

- Cerca de 55 arquivos `teste_*.py` misturados com o código na raiz.
- A pasta `analise/` misturava extratores de documentos, gravação no banco e relatórios.
- A pasta `documentos/` tinha um arquivo só.
- A pasta `main/` guardava 3 protótipos antigos (~2.400 linhas) que ninguém mais usava.
- Não havia `pyproject.toml` nem lista de dependências.

**Por que isso importa.** Num projeto confuso fica difícil achar onde mexer, e as dependências circulares (`documentos` → `analise` → `documentos`) aumentam o risco de efeitos colaterais.

**Correção** (`51beb7c`, `948214f`):

- Pacote único `src/ceuma/` dividido **por etapa do fluxo**: `leitura`, `extracao`, `documentos`, `conferencia`, `banco`, `relatorios`, `processamento`, `interface`.
- **Cada tipo de documento num módulo só**, com os sinais de identificação e a extração juntos. Isso desfez a confusão entre `analise/` e `documentos/`.
- O adaptador do Tesseract foi separado em `leitura/ocr/tesseract.py`.
- Testes automatizados em `tests/`; scripts manuais em `scripts/manuais/`; `OCR.md` em `docs/`.
- Protótipos de `main/` removidos (continuam recuperáveis no commit `d917af4`).
- `python -m ceuma` passou a ser o ponto de entrada, e a variável `CEUMA_DADOS` define a pasta do banco.
- Arquivos movidos com `git mv`, para **preservar o histórico** de cada um. Imports e alvos de `mock.patch` foram atualizados por script, não à mão, para não esquecer nenhum.

**Por que `src/`?** É o layout recomendado em Python: o código só é importável depois de instalado (`pip install -e .`), o que evita importar sem querer arquivos soltos da raiz e conflitos de nome.

**Resultado:**

| | Antes | Depois |
|---|---|---|
| Arquivos `.py` na raiz | ~57 | 0 |
| Testes | 372 ✓, 2 pulados | 372 ✓, 2 pulados (idêntico) |
| Scripts manuais | mesmo resultado | mesmo resultado |

A reorganização **não mudou nenhuma regra**. É assim que um *refactor* deve ser: mesma saída, código mais claro.

### A2. O `.gitignore` escondia um pacote do código

**Problema.** A regra `relatorios/` (pensada para os relatórios gerados) ignorava **qualquer** pasta com esse nome, inclusive o pacote novo `src/ceuma/relatorios/`. O primeiro commit da reorganização saiu **sem** esse pacote.

**Como foi descoberto.** Conferindo `git ls-files` depois do commit e rodando a suíte num **clone limpo** do repositório. Rodar só na pasta de trabalho não pegaria o erro, porque os arquivos existiam no disco.

**Correção:** ancorar as regras na raiz (`/relatorios/`, `/documentos_pessoais/`).

**Lição:** depois de mexer em estrutura, teste num clone limpo.

---

## Parte B: rodar no macOS

### B1. O Tesseract só era procurado como `tesseract.exe`

**Problema.** `Tesseract.__init__` procurava só `C:\Program Files\Tesseract-OCR\tesseract.exe` ou `tesseract.exe` no `PATH`. Em macOS e Linux o executável se chama `tesseract`, então ele nunca era encontrado e o OCR inteiro falhava em silêncio com `TESSERACT_INDISPONIVEL`.

**Correção** (`63880a8`): nova função `localizar_tesseract()` em `leitura/ocr/tesseract.py`. A ordem de busca é:

1. a variável `CEUMA_TESSERACT`, se definida;
2. a instalação padrão do sistema (`C:\Program Files\...` no Windows; `/opt/homebrew/bin`, `/usr/local/bin` e `/usr/bin` no macOS e Linux);
3. o `PATH`.

Os caminhos do Homebrew entram fixos porque um app aberto pelo Finder **não herda** o `PATH` do terminal.

### B2. Fonte fixa do Windows

A interface usava `"Segoe UI"`, que só existe no Windows. Passou a usar a fonte padrão do sistema (`TkDefaultFont`).

**Resultado:**

| | Antes | Depois |
|---|---|---|
| Testes | 372 ✓, 2 pulados | **378 ✓, 0 pulados** (+4 testes novos; os 2 que precisavam do Tesseract real passaram a rodar) |
| RG fictício escaneado da demo | sem OCR no Mac | `DOCUMENTO_IDENTIDADE` identificado, nome extraído |

---

## Parte C: OCR de documentos fotografados

Esta é a parte central. Com o Tesseract funcionando, o RG fotografado **continuava** saindo com 2 linhas de lixo. Os problemas abaixo estão na ordem em que foram investigados, e cada um foi medido isoladamente.

### C1. Imagem colorida: a causa principal

**Hipótese.** O fundo vermelho e a trama verde atrapalham o Tesseract.

**Experimento:** mesma imagem, Tesseract chamado direto, contando quantas das **18 palavras-chave** do RG aparecem.

| Escala | Cor | psm 3 | psm 6 | psm 11 |
|---|---|---|---|---|
| 2× (144 DPI) | **RGB colorido** | 0/18 | 1/18 | 0/18 |
| 2× (144 DPI) | tons de cinza | 7/18 | 6/18 | 9/18 |
| 3× (216 DPI) | **RGB colorido** | 0/18 | 2/18 | 0/18 |
| 3× (216 DPI) | tons de cinza | 10/18 | 7/18 | 13/18 |

**Por que acontece.** O Tesseract converte a imagem em preto e branco (*binarização*) antes de reconhecer. Com cores saturadas e fundos coloridos essa conversão interna falha e "apaga" o texto.

**Correção** (`c59873e`): `Tesseract._executar` converte a imagem para **tons de cinza** antes de enviar.

### C2. Nem toda conversão para cinza é igual

**Achado inesperado.** A primeira versão usava a fórmula clássica de luminância (`0,299·R + 0,587·G + 0,114·B`). Chamando o Tesseract com a conversão do próprio MuPDF (`csGRAY`), o resultado era bem melhor.

**Experimento** (300 DPI, `psm 3`):

| Conversão para cinza | Data de nascimento | Data de expedição | Título "CARTEIRA DE IDENTIDADE" | Palavras |
|---|---|---|---|---|
| Luminância (NumPy) | ✗ | ✗ | ✗ | 88 |
| **MuPDF (`csGRAY`)** | ✓ | ✓ | ✓ | **122** |

**Por que.** A conversão do MuPDF usa gestão de cor (perfil sRGB) e clareia muito mais as cores saturadas:

| Cor de entrada (255) | Luminância | MuPDF |
|---|---|---|
| Vermelho puro | 76 | **130** |
| Verde puro | 150 | **220** |

Com o fundo vermelho e a trama verde mais claros, o texto **preto** ganha contraste.

**Correção:** `pymupdf.Pixmap(pymupdf.csGRAY, rgb)` dentro do adaptador do Tesseract. A conversão fica dentro do adaptador e não na renderização, para o PaddleOCR continuar recebendo RGB. Medimos a diferença entre converter depois e renderizar direto em cinza: 0,007 por pixel, desprezível.

**Efeito colateral aceito:** tinta **vermelha** também fica mais clara. Esse foi o motivo de o número do RG ainda faltar, resolvido depois em [D3](#d3-número-do-rg-e-data-de-expedição-rótulos-miúdos-e-tinta-colorida).

### C3. Resolução baixa: 144 DPI

**Problema.** `pagina.py` renderizava as regiões a **2 pixels por ponto** (144 DPI). O mesmo número servia como escala das coordenadas usadas pelo extrator. O Tesseract foi feito para ~300 DPI, e letras pequenas a 144 DPI ficam com poucos pixels.

**Experimento** (fluxo completo, já em cinza):

| Escala de renderização | Linhas lidas |
|---|---|
| 2× (144 DPI) | 14 |
| 3× (216 DPI) | 21 |
| **4,17× (300 DPI)** | **42** |

**Correção:** nova constante `_ESCALA_OCR = 300/72`, **separada** de `_ESCALA_COORDENADAS = 2`. O código já dividia as caixas do OCR pela escala de renderização antes de convertê-las, então foi possível mudar a resolução **sem** alterar nenhuma regra de distância do extrator. Três testes que fixavam o tamanho do raster (600×440) passaram a calculá-lo a partir da constante.

### C4. Modo de leitura errado para páginas em pé (`psm 6`)

**Problema.** `_psm()` só usava a análise de layout (`psm 3`) em imagens **mais largas que altas**. Uma página A4 em pé caía em `psm 6` ("um único bloco de texto"), que mistura colunas, fotos e cartões lado a lado.

**Correção:** `psm 3` para páginas e regiões; `psm 7` só para faixas estreitas, que parecem uma linha de texto.

**Resultado após C1 + C2 + C3 + C4:** **54 linhas** lidas. O texto já continha nome, filiação, naturalidade, as duas datas, o CPF e "CARTEIRA DE IDENTIDADE", mas **o documento ainda não era identificado**, e havia muito lixo entre as linhas.

### C5. Lixo da borda e do brasão

**Problema.** O texto vinha cheio de "palavras" como `RSRSRS`, `TESS`, `Ti`, lidas das bordas verdes e do brasão. Uma delas grudava no título ("CARTEIRA DE IDENTIDADE RSRSRS SO gr 0 467…").

**Experimento:** agrupar as palavras pela **confiança** que o próprio Tesseract atribui (0–100).

| Faixa de confiança | Exemplos |
|---|---|
| 0–39 | `RSRS`, `TERRE`, `Ubid`, `SZASDESS`, `Ti`, `TESS`, `CEDER`… (lixo) |
| 50–59 | o número do RG em tinta vermelha (real) |
| 80–99 | REPUBLICA, NOME, FILIAÇÃO, as datas, CARTEIRA DE IDENTIDADE… (real) |

A separação é nítida: texto real ≥ 50, lixo < 40.

**Correção:** descartar palavras com confiança **< 40** (`_CONFIANCA_MINIMA_PALAVRA`).

**Resultado:** de 54 linhas sujas para **24 linhas limpas**.

### C6. Identificador rígido demais para fotos

**Problema 1:** o título ainda chegava como `CARTEIRA DE IDENTIDADE gr 0 0`, e o identificador exigia que o título ocupasse a linha sozinho (ou só com subtítulos conhecidos).

**Problema 2:** nenhuma regra confirmava o RG. Na foto, "REGISTRO GERAL" e o órgão emissor estão em letra miúda e se perdiam, e as regras existentes dependiam deles.

**Correções** (`documentos/identificador.py`):

- `_titulo_encontrado` tolera, depois do título, **fragmentos de até 2 caracteres**, que são ruído típico de OCR. Fragmentos longos continuam invalidando o título (há teste para isso).
- Nova regra para RG: **título + "VALIDA EM TODO O TERRITORIO NACIONAL" + rótulos pessoais** (NOME/FILIAÇÃO), sem o rótulo de CPF da CIN. É uma combinação específica do RG tradicional, e sem os rótulos pessoais ela não basta (há teste para isso).

**Resultado:** `NAO_IDENTIFICADO` → **`DOCUMENTO_IDENTIDADE` / `IDENTIFICADO`**, e o **nome** passou a ser extraído.

### C7. Colunas diferentes unidas numa só linha

**Problema.** O Tesseract juntava numa linha campos de colunas diferentes que estão na mesma altura: `NATURALIDADE … DATA DE NASCIMENTO` e `<cidade> … <data>`. Com uma caixa só cobrindo as duas colunas, o associador espacial não conseguia ligar "DATA DE NASCIMENTO" à data.

**Experimento:** medir o espaço entre palavras consecutivas, em múltiplos da altura da letra.

| Situação | Vão medido |
|---|---|
| Palavras do mesmo campo (inclusive fonte de máquina de escrever) | 0,2 a **1,3** alturas |
| Colunas diferentes | **6,8** a 20 alturas |

**Correção:** `_separar_colunas` quebra a linha quando o vão passa de **3 alturas** (`_VAO_ENTRE_COLUNAS`), um valor com folga dos dois lados.

**Efeito colateral encontrado pelo teste novo:** o filtro de confiança (C5) removia uma palavra-lixo **no meio** de um campo, e o buraco deixado parecia um vão de coluna, partindo o campo em dois. A correção foi medir os vãos **antes** do filtro e só depois descartar as palavras de confiança baixa. Esse é um bom exemplo de por que cada regra nova ganha teste: o problema apareceu no teste antes de aparecer num documento real.

**Resultado:** além do nome, passaram a sair **data de nascimento, filiação e naturalidade**. A data de nascimento extraída foi conferida e é a correta, não a de expedição.

### C8. Prazo de OCR que acabava no meio do lote

**Problema.** O mesmo leitor de OCR atende o lote inteiro, mas o prazo de "lote" (30 s) era definido uma vez e **nunca reiniciado**. Depois de ~30 s de OCR, todos os PDFs seguintes saíam vazios **sem erro**. O prazo por página também era curto (5 s) para a resolução nova.

**Correção:**

- `LeitorAdaptativo.iniciar_documento()` reinicia o prazo a cada PDF e é chamado por `ler_pdf`.
- Os limites padrão passaram a 15 s por página e 60 s por documento.
- A variável `CEUMA_OCR_SEGUNDOS_LOTE` foi renomeada para `CEUMA_OCR_SEGUNDOS_DOCUMENTO`, porque o nome antigo descrevia errado o comportamento.

**Resultado:** o 2º, 3º… documentos do lote recebem o mesmo tempo de OCR que o 1º. Um teste novo verifica que, com o prazo esgotado, `iniciar_documento()` libera o OCR de novo.

### Situação ao fim da Parte C

No fluxo completo (lote → banco → relatório):

| Campo | Status |
|---|---|
| Nome | ✓ ENCONTRADO |
| Data de nascimento | ✓ ENCONTRADO |
| Filiação | ✓ ENCONTRADO |
| Naturalidade | ✓ ENCONTRADO |
| CPF | ⚠ EVIDENCIA_INSUFICIENTE |
| Número do RG | ✗ NAO_ENCONTRADO |
| Data de expedição | ✗ NAO_ENCONTRADO |

A mensagem "Nenhum campo recuperado" deixou de aparecer. Testes: **388 ✓**.

---

## Parte D: campos que ainda faltavam no RG

### D1. Regra de titularidade do CPF no RG

**Problema.** Na CIN, um rótulo "CPF" no contexto de nome ou data de nascimento (e fora da filiação) já era aceito como CPF do titular. No RG tradicional não, e só um rótulo explícito ("CPF DO TITULAR") servia. Mas no RG o campo "CPF" impresso é sempre do titular.

**Correção:** o perfil do RG passou a usar a mesma regra contextual da CIN (`cpf_contextual=True` em `documentos/identidade.py`). Um CPF dentro do bloco de filiação **continua** sem titularidade, e há teste para isso.

### D2. Rótulo colado no valor: 0,96 px de diferença

**Problema.** Mesmo com D1, o CPF continuava não sendo associado. Rastreando as decisões do associador espacial:

- caixa do rótulo "CPF": termina em `y = 468,48`;
- caixa do número: começa em `y = 467,52`.

As caixas se cruzavam em **0,96 px** (9,5% da altura do rótulo). O associador exigia um espaço vertical ≥ 0 e descartava o par com o motivo `SOBREPOSICAO_VERTICAL`. O rótulo é miúdo e está impresso rente ao número, então o OCR desenha as caixas um pouco sobrepostas.

**Correção** (`extracao/associador_espacial.py`): um valor **abaixo** do rótulo é aceito mesmo com cruzamento de até **30%** da altura da caixa menor, desde que o valor comece abaixo do topo do rótulo.

**Decisão consciente sobre um teste existente.** `teste_diagnostico_geometrico.py` fixava de propósito que 1 px de cruzamento (10% num rótulo de 10 px) devia ser recusado. A medição real ficou em 9,5%, logo abaixo dessa fronteira. Ajustar a folga para 9,9% passaria "por sorte" neste documento e falharia no próximo. Optamos por uma folga com margem (30%) e **atualizamos o teste**: 1 px passa a ser aceito, e 4 px (40%) continuam recusados. A fronteira continua testada, só em outro lugar.

**Resultado:** CPF `EVIDENCIA_INSUFICIENTE` → **`ENCONTRADO`, papel `TITULAR`, valor correto**. Também foi verificado que **as duas** mudanças (D1 e D2) são necessárias: só com D2, o CPF sai como `AMBIGUO`.

### D3. Número do RG e data de expedição: rótulos miúdos e tinta colorida

**Problema.**

- O número do RG está em **tinta vermelha**, que a conversão para cinza (C2) clareia.
- Os rótulos "REGISTRO **GERAL**" e "DATA DE **EXPEDIÇÃO**" estão em letra miúda, em duas linhas cada.
- A data de expedição era lida, mas **sem rótulo** o extrator não tem como saber que é a de expedição.

**Pedido:** resolver na leitura base, sem depender da "releitura avançada" com PaddleOCR.

**Experimento:** recortes do cartão × resolução × conversão de cor × modo do Tesseract, verificando 4 itens: "GERAL", "EXPEDI", número do RG e data de expedição.

| Recorte | Modo | GERAL | EXPEDI | Nº RG | Data exp. |
|---|---|---|---|---|---|
| Página inteira, 300 DPI, MuPDF | `psm 3` (layout) | ✗ | ✗ | ✓/✗ (instável) | ✓ |
| Página inteira, 300 DPI, MuPDF | **`psm 11` (esparso)** | ✓ | ✓ | ✓ | ✗ |
| Topo do cartão, qualquer DPI/cor | `psm 11` | ✓ | ✓ | ✓ | ✓ |

**Conclusão:** o fator decisivo não era a cor nem a resolução, mas o **modo de leitura**. A análise de layout (`psm 3`) trata rótulos miúdos e isolados como desenho. A leitura esparsa (`psm 11`) os encontra, mas sozinha perde outras coisas. **Os dois modos se completam.**

**Correção** (`leitura/ocr/adaptativo.py`):

- Depois da leitura `psm 3`, uma **segunda passada `psm 11`**. Entram só as linhas que caem em **áreas vazias**, ou seja, que se sobrepõem menos de 20% a alguma linha da primeira passada, e que têm ≥ 2 caracteres (função `_fora_das_linhas`).
- O limite de chamadas ao Tesseract por página subiu de 4 para 6, para a passada nova não tirar espaço das releituras de correção.
- A passada é um parâmetro (`leitura_esparsa=True` por padrão). Três testes antigos que contam o número exato de chamadas a desligam, porque o que eles verificam (releituras e reaproveitamento da prévia) não mudou. A passada nova tem teste próprio.

**Resultado:**

| Campo | Antes de D | Depois de D |
|---|---|---|
| Nome | ✓ | ✓ |
| CPF | ⚠ evidência insuficiente | ✓ titular, valor correto |
| Número do RG | ✗ | ✓ com dígito verificador correto |
| Data de nascimento | ✓ | ✓ |
| Data de expedição | ✗ | ✓ |
| Filiação | ✓ | ✓ |
| Naturalidade | ✓ | ✓ |
| Pendências de leitura | `CAMPO_REQUER_REVISAO` | **nenhuma** |
| Tempo de leitura | ~0,7 s | ~1,6 s |

Custo aceito: uma chamada a mais do Tesseract por página (~+0,9 s neste documento).

---

## 8. Resumo da evolução em números

**RG fotografado (documento real):**

| Etapa | Linhas OCR | Identificação | Campos corretos |
|---|---|---|---|
| Início | 2 (lixo) | `NAO_IDENTIFICADO` | 0 |
| + tons de cinza (C1) | 14 | `NAO_IDENTIFICADO` | 0 |
| + conversão MuPDF, 300 DPI, `psm 3` (C2–C4) | 54 (com lixo) | `NAO_IDENTIFICADO` | 0 |
| + filtro de confiança (C5) | 24 (limpas) | `NAO_IDENTIFICADO` | 0 |
| + regras do identificador (C6) | 24 | **`IDENTIFICADO` (RG)** | 1 (nome) |
| + separação de colunas (C7) | 28 | `IDENTIFICADO` | 4 |
| + CPF do RG e tolerância do associador (D1, D2) | 28 | `IDENTIFICADO` | 5 |
| + passada esparsa (D3) | mais linhas úteis | `IDENTIFICADO` | **7 de 7, sem pendências** |

**Testes automatizados:**

| Momento | Resultado |
|---|---|
| Início | 372 ✓, 2 pulados |
| Após reorganização | 372 ✓, 2 pulados |
| Após macOS | 378 ✓, 0 pulados |
| Após OCR de fotos | 388 ✓ |
| Após campos do RG | **391 ✓** |

**PDFs fictícios da demo:** os três continuaram com o mesmo resultado em todas as etapas (RG identificado com nome; certidão de casamento identificada; o documento "inconclusivo", feito de propósito para não ser identificado, continuou sem identificação).

---

## 9. Problemas conhecidos ainda em aberto

1. **Reforço automático do Paddle nunca roda.** `CEUMA_OCR_FALLBACK_PADDLE=1` liga o reforço, mas ele exige 35 s livres (`CEUMA_OCR_RESERVA_PADDLE`) e a página tem 15 s. Com os padrões a condição nunca é atendida. Caminhos possíveis: reduzir a reserva quando os modelos já estiverem carregados, ou dar um prazo próprio ao reforço.
2. **Validação com mais documentos.** As regras de C5, C6, C7, D2 e D3 foram calibradas com um documento real e testadas com dados fictícios. Os limiares (confiança 40, vão de 3 alturas, cruzamento de 30%) devem ser conferidos com RGs de outros estados, CIN, CNH, certidões e scans de scanner.
3. **Desempenho no computador de 4 GB.** A leitura ficou mais pesada (300 DPI e duas passadas). É preciso medir na máquina-alvo.
4. **Scripts manuais.** `scripts/manuais/teste_operacoes.py` já estava quebrado antes das mudanças (`no such table: analises`), e 5 scripts precisam de um PDF real em `pdf/jpg2pdf.pdf`.
5. **Dependência `leitura → documentos`.** A leitura consulta o identificador para decidir se a camada digital basta e para validar a orientação pelo texto. Seria mais limpo essa decisão ficar em `processamento/`.

---

## 10. Lições aprendidas

1. **Testes verdes com dublês não provam que o sistema funciona com dados reais.** Os 372 testes passavam com o OCR quebrado, porque o OCR era simulado. Os dublês são ótimos para testar a lógica, mas é preciso também um teste com o motor real (ou pelo menos uma verificação manual com um documento real) antes de dizer que algo funciona.
2. **Meça antes de mudar.** O palpite inicial (resolução) era real, mas não era a causa principal. A causa principal (cor) só apareceu medindo.
3. **Uma variável por vez.** Mudando cor, resolução e modo ao mesmo tempo, não daria para saber o que ajudou nem o que atrapalhou.
4. **Desconfie de "detalhes".** Duas fórmulas de "tons de cinza" deram resultados muito diferentes (C2). Um cruzamento de 0,96 px descartava o CPF (D2).
5. **Regras novas geram efeitos colaterais.** O filtro de confiança (C5) quebrou a separação de colunas (C7) num caso específico. Foi o teste da regra nova que mostrou isso.
6. **Prefira margens a números exatos.** A folga do associador ficou em 30%, não em 9,9%: o limiar precisa funcionar no próximo documento, não só neste.
7. **Ao mudar um teste existente, explique por quê.** Teste que fixa uma fronteira de propósito só deve mudar com uma justificativa medida, registrada no commit e na documentação.
8. **Privacidade é parte do método, não um detalhe.** Documento real só para diagnóstico local; testes e documentação sempre com dados fictícios.
