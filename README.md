# Projeto CEUMA

Aplicativo Windows para leitura e conferência local de documentos em PDF. O CEUMA extrai campos, indica divergências e reúne resultados para revisão humana. A análise exige conferência humana e não autentica documentos nem decide elegibilidade.

## Funcionalidades

- Leitura híbrida que prioriza texto digital e aplica OCR a páginas ou regiões digitalizadas.
- Identificação e extração de campos de RG, CIN, CNH, certidões de nascimento e casamento e documentos trabalhistas compatíveis.
- Análise individual e familiar, consulta dos campos e páginas de origem, revisão de pendências, recuperação de análises válidas e exportação CSV.
- SQLite local com retenção de 24 horas. A limpeza ocorre enquanto a interface está aberta e na inicialização seguinte. CSVs exportados ficam sob responsabilidade de quem os salva.
- Releitura explícita com PaddleOCR local para documentos elegíveis. Uma releitura bem-sucedida cria uma análise separada e preserva a anterior até sua expiração.

## Preparação no Windows

Use Python 3.12 com Tkinter. A configuração de referência usa Python 3.12.10. O SQLite é fornecido com o Python. Em PowerShell, na raiz do projeto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Em uma máquina offline, prepare previamente um diretório de wheels compatíveis com a versão e a arquitetura do Python. Instale a partir dele com `--no-index --find-links`. Use o mesmo procedimento para as dependências transitivas.

### OCR local

Instale previamente o Tesseract para Windows com `por.traineddata`. O CEUMA procura o executável na instalação padrão do Windows ou no `PATH`. Verifique a lista de idiomas com `tesseract --list-langs`. O idioma `osd` é necessário apenas ao selecionar o modo experimental de orientação do Tesseract.

O modo padrão é adaptativo. Sem os modelos Paddle locais, é possível usar apenas a orientação declarada no PDF:

```powershell
$env:CEUMA_OCR_ORIENTACAO = 'pdf'
```

Para habilitar a releitura avançada, instale também `paddlepaddle` e `paddleocr` na mesma venv (`pip install -e .[paddle]`). Prepare os modelos de detecção `PP-OCRv6_medium_det` e reconhecimento `PP-OCRv6_medium_rec` no cache local. Para orientação automática com Paddle, prepare também `PP-LCNet_x1_0_doc_ori`. Use os diretórios de modelo sob `official_models` no cache PaddleX, definido por `PADDLE_PDX_CACHE_HOME` quando configurado. Cada modelo deve conter `inference.json`, `inference.pdiparams` e `inference.yml`. Consulte [docs/OCR.md](docs/OCR.md) para as opções de execução.

## Executar

```powershell
.\.venv\Scripts\python.exe -B -m ceuma
```

Para experimentar com PDFs fictícios e banco temporário:

```powershell
.\.venv\Scripts\python.exe -B -m ceuma.demo
```

O banco fica em `dados_ceuma/` na raiz do projeto. Para usar outra pasta, defina `CEUMA_DADOS`.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pytest
```

Os testes automatizados ficam em `tests/`. Os scripts de conferência manual ficam em `scripts/manuais/`; alguns exigem um PDF real em `pdf/jpg2pdf.pdf`.

## Estrutura

```
src/ceuma/
├── leitura/          PDF → linhas de texto (camada digital + OCR)
│   └── ocr/          Tesseract, PaddleOCR, leitor adaptativo e orientação
├── extracao/         ferramentas genéricas: CPF, datas, nomes, associação espacial
├── documentos/       identificador de tipo, analisador e um módulo por tipo de documento
├── conferencia/      comparação de campos entre documentos
├── banco/            SQLite: conexão, esquema, operações, persistência e retenção
├── relatorios/       montagem do relatório e exportação CSV
├── processamento/    orquestração do lote (ler → analisar → gravar → conferir)
├── interface/        janela Tkinter
└── demo.py           demonstração com PDFs fictícios
tests/                testes automatizados (pytest/unittest)
scripts/              benchmark e scripts de conferência manual
docs/                 documentação técnica
```

## Limitações

- Digitalizações difíceis, páginas rotacionadas e camadas invisíveis podem exigir releitura e conferência do original. A releitura avançada depende dos modelos locais e pode ser mais lenta e consumir mais memória.
- A interface familiar permite consultar vínculos existentes. A criação de pessoas e a edição de atribuições ainda dependem das APIs internas.
- A exportação disponível na interface é CSV. Não coloque documentos reais, bancos, credenciais, relatórios ou modelos no Git.
