import csv
import os
import re
import sqlite3
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from paddleocr import PaddleOCR


# =========================================================
# 1. CONFIGURAÇÕES
# =========================================================

COLUNAS_EXPORTACAO = [
    "Arquivo",
    "Tipo_Documento",
    "Nome",
    "CPF",
    "RG",
    "Data_Nascimento",
    "Matricula",
    "Qualquer_CPF_Encontrado",
    "Qualquer_Data_Encontrada",
    "Status",
    "Detalhes"
]

NOME_BANCO = "documentos.db"
NOME_CSV = "resultado_extracao.csv"

REGRAS_POR_DOCUMENTO = {
    "Documento_Identidade": {
        "palavras_chave": [
            "identidade",
            "registro geral",
            "carteira de identidade",
            "república federativa do brasil",
        ]
    },
    "CNH": {
        "palavras_chave": [
            "carteira nacional de habilitação",
            "carteira nacional de habilitacao",
            "departamento nacional de trânsito",
            "departamento estadual de trânsito",
        ]
    },
    "Certidao_Nascimento": {
        "palavras_chave": [
            "certidão de nascimento",
            "certidao de nascimento",
            "registro civil",
        ]
    },
    "Documento_Desconhecido": {"palavras_chave": []},
}


# =========================================================
# 2. BANCO DE DADOS SQLITE
# =========================================================

def conectar_banco(caminho_banco):
    conexao = sqlite3.connect(caminho_banco, timeout=30)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT NOT NULL,
            caminho_arquivo TEXT NOT NULL,
            tipo_documento TEXT,
            nome TEXT,
            cpf TEXT,
            rg TEXT,
            data_nascimento TEXT,
            matricula TEXT,
            qualquer_cpf_encontrado TEXT,
            qualquer_data_encontrada TEXT,
            status TEXT NOT NULL,
            detalhes TEXT,
            processado_em TEXT NOT NULL,
            UNIQUE(caminho_arquivo)
        )
    """)
    conexao.commit()
    return conexao


def salvar_no_banco(conexao, caminho_pdf, dados):
    agora = datetime.now().isoformat(timespec="seconds")

    conexao.execute("""
        INSERT INTO documentos (
            arquivo, caminho_arquivo, tipo_documento, nome, cpf, rg,
            data_nascimento, matricula, qualquer_cpf_encontrado,
            qualquer_data_encontrada, status, detalhes, processado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(caminho_arquivo) DO UPDATE SET
            arquivo=excluded.arquivo,
            tipo_documento=excluded.tipo_documento,
            nome=excluded.nome,
            cpf=excluded.cpf,
            rg=excluded.rg,
            data_nascimento=excluded.data_nascimento,
            matricula=excluded.matricula,
            qualquer_cpf_encontrado=excluded.qualquer_cpf_encontrado,
            qualquer_data_encontrada=excluded.qualquer_data_encontrada,
            status=excluded.status,
            detalhes=excluded.detalhes,
            processado_em=excluded.processado_em
    """, (
        dados.get("Arquivo", ""),
        os.path.abspath(caminho_pdf),
        dados.get("Tipo_Documento", ""),
        dados.get("Nome", ""),
        dados.get("CPF", ""),
        dados.get("RG", ""),
        dados.get("Data_Nascimento", ""),
        dados.get("Matricula", ""),
        dados.get("Qualquer_CPF_Encontrado", ""),
        dados.get("Qualquer_Data_Encontrada", ""),
        dados.get("Status", ""),
        dados.get("Detalhes", ""),
        agora,
    ))
    conexao.commit()


def exportar_csv(conexao, caminho_csv):
    cursor = conexao.execute("""
        SELECT arquivo, tipo_documento, nome, cpf, rg, data_nascimento,
               matricula, qualquer_cpf_encontrado,
               qualquer_data_encontrada, status, detalhes
        FROM documentos
        ORDER BY arquivo COLLATE NOCASE
    """)

    with open(caminho_csv, "w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(COLUNAS_EXPORTACAO)
        escritor.writerows(cursor.fetchall())


def exportar_excel(conexao, caminho_excel):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as erro:
        raise RuntimeError(
            "A biblioteca openpyxl não está instalada. "
            "Execute: python -m pip install openpyxl"
        ) from erro

    cursor = conexao.execute("""
        SELECT arquivo, tipo_documento, nome, cpf, rg, data_nascimento,
               matricula, qualquer_cpf_encontrado,
               qualquer_data_encontrada, status, detalhes
        FROM documentos
        ORDER BY arquivo COLLATE NOCASE
    """)

    wb = Workbook()
    ws = wb.active
    ws.title = "Extrações"
    ws.append(COLUNAS_EXPORTACAO)

    for celula in ws[1]:
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="244062")

    for linha in cursor.fetchall():
        ws.append(list(linha))

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for coluna in ws.columns:
        maior = max(len(str(c.value or "")) for c in coluna)
        ws.column_dimensions[get_column_letter(coluna[0].column)].width = min(
            max(maior + 2, 12), 45
        )

    wb.save(caminho_excel)


# =========================================================
# 3. OCR E LEITURA DOS PDFs
# =========================================================

def inicializar_ocr():
    print("Inicializando PaddleOCR...")
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    print("PaddleOCR inicializado!")
    return ocr


def extrair_texto_imagem(ocr, caminho_imagem):
    resultados = ocr.predict(caminho_imagem)
    textos = []

    for resultado in resultados:
        dados = resultado.json

        # Em algumas versões, .json pode ser um método.
        if callable(dados):
            dados = dados()

        if isinstance(dados, str):
            import json
            dados = json.loads(dados)

        if not isinstance(dados, dict):
            continue

        dados = dados.get("res", dados)
        linhas = dados.get("rec_texts", [])

        textos.extend(
            str(texto).strip()
            for texto in linhas
            if texto and str(texto).strip()
        )

    return "\n".join(textos)


def ler_pdf(caminho_pdf, ocr, log=None):
    textos_paginas = []

    with fitz.open(caminho_pdf) as documento:
        total_paginas = len(documento)

        for numero, pagina in enumerate(documento, 1):
            if log:
                log(f"   Página {numero}/{total_paginas}")

            texto = pagina.get_text("text").strip()

            # OCR é usado quando a página não possui texto digital útil.
            if len(texto) < 15:
                if log:
                    log("   Página sem texto suficiente; executando OCR...")

                pix = pagina.get_pixmap(
                    matrix=fitz.Matrix(2.5, 2.5),
                    alpha=False
                )

                caminho_temp = os.path.join(
                    os.path.dirname(caminho_pdf),
                    f"_ocr_temp_{threading.get_ident()}_{numero}.png"
                )

                try:
                    pix.save(caminho_temp)
                    texto = extrair_texto_imagem(ocr, caminho_temp)
                finally:
                    if os.path.exists(caminho_temp):
                        try:
                            os.remove(caminho_temp)
                        except OSError:
                            pass

            textos_paginas.append(texto)

    return "\n".join(textos_paginas)


# =========================================================
# 4. IDENTIFICAÇÃO E EXTRAÇÃO DOS CAMPOS
# =========================================================

def normalizar_texto(texto):
    return re.sub(r"\s+", " ", texto.lower()).strip()


def identificar_documento(texto):
    texto_minusculo = normalizar_texto(texto)

    for tipo in (
        "CNH",
        "Certidao_Nascimento",
        "Documento_Identidade",
    ):
        palavras = REGRAS_POR_DOCUMENTO[tipo]["palavras_chave"]
        if any(palavra in texto_minusculo for palavra in palavras):
            return tipo

    return "Documento_Desconhecido"


def validar_cpf(cpf):
    numeros = re.sub(r"\D", "", cpf or "")

    if len(numeros) != 11 or numeros == numeros[0] * 11:
        return False

    soma = sum(int(numeros[i]) * (10 - i) for i in range(9))
    digito1 = (soma * 10 % 11) % 10

    soma = sum(int(numeros[i]) * (11 - i) for i in range(10))
    digito2 = (soma * 10 % 11) % 10

    return numeros[-2:] == f"{digito1}{digito2}"


def extrair_nome(texto):
    linhas = [
        linha.strip()
        for linha in texto.splitlines()
        if linha.strip()
    ]

    for indice, linha in enumerate(linhas):
        resultado = re.search(
            r"\bNOME(?: COMPLETO)?\s*[:\-]?\s*(.*)",
            linha,
            re.IGNORECASE,
        )

        if not resultado:
            continue

        nome = resultado.group(1).strip()
        nome = re.split(
            r"\b(?:CPF|RG|DATA|FILIAÇÃO|MATRÍCULA|NASCIMENTO)\b",
            nome,
            flags=re.IGNORECASE,
        )[0].strip(" :-")

        if len(nome) >= 4:
            return nome

        if indice + 1 < len(linhas):
            proxima = linhas[indice + 1]
            if not re.search(
                r"\b(CPF|RG|DATA|FILIAÇÃO|MATRÍCULA)\b",
                proxima,
                re.IGNORECASE,
            ):
                return proxima

    return "NÃO ENCONTRADO"


def normalizar_sem_acento(texto):
    import unicodedata
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def extrair_data_por_rotulo(texto):
    """Prioriza a data junto ao rótulo; aceita rótulo e data em linhas separadas."""
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    for indice, linha in enumerate(linhas):
        linha_normalizada = normalizar_sem_acento(linha).upper()
        if re.search(r"\b(DATA\s+DE\s+NASCIMENTO|NASCIMENTO)\b", linha_normalizada):
            trecho = linha
            if indice + 1 < len(linhas):
                trecho += " " + linhas[indice + 1]
            if indice + 2 < len(linhas):
                trecho += " " + linhas[indice + 2]
            data = re.search(r"\b\d{2}[./-]\d{2}[./-]\d{4}\b", trecho)
            if data:
                return data.group(0).replace(".", "/").replace("-", "/")

    # Fallback para OCR que entrega todo o texto em uma linha.
    texto_normalizado = normalizar_sem_acento(texto)
    rotulo = re.search(
        r"(?:DATA\s+DE\s+NASCIMENTO|NASCIMENTO)\s*[:\-]?\s*(.{0,80})",
        texto_normalizado,
        re.IGNORECASE,
    )
    if rotulo:
        data = re.search(r"\b\d{2}[./-]\d{2}[./-]\d{4}\b", rotulo.group(1))
        if data:
            return data.group(0).replace(".", "/").replace("-", "/")
    return "NÃO ENCONTRADO"


def extrair_rg(texto):
    """Busca o número associado a REGISTRO GERAL ou RG."""
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]

    for indice, linha in enumerate(linhas):
        linha_normalizada = normalizar_sem_acento(linha).upper()
        if re.search(r"\b(REGISTRO\s+GERAL|R\.?\s*G\.?)\b", linha_normalizada):
            trecho = linha
            if indice + 1 < len(linhas):
                trecho += " " + linhas[indice + 1]
            numero = re.search(r"(?<!\d)\d[\d./\- ]{5,20}\d(?!\d)", trecho)
            if numero:
                return re.sub(r"\D", "", numero.group(0))

    # Alternativa para OCR que devolve o conteúdo em uma única linha.
    texto_normalizado = normalizar_sem_acento(texto).upper()
    rotulo = re.search(
        r"(?:REGISTRO\s+GERAL|R\.?\s*G\.?)\s*[:\-]?\s*",
        texto_normalizado,
    )
    if rotulo:
        trecho = texto_normalizado[rotulo.end():rotulo.end() + 45]
        numero = re.search(r"\d[\d./\- ]{5,20}\d", trecho)
        if numero:
            return re.sub(r"\D", "", numero.group(0))

    return "NÃO ENCONTRADO"


def extrair_campos(texto, tipo):
    resultado = {coluna: "" for coluna in COLUNAS_EXPORTACAO}
    resultado["Tipo_Documento"] = tipo

    padrao_cpf = r"\b\d{3}\.?\s?\d{3}\.?\s?\d{3}-?\s?\d{2}\b"
    cpfs = re.findall(padrao_cpf, texto)
    cpf_encontrado = cpfs[0] if cpfs else ""

    padrao_data = r"\b\d{2}[./-]\d{2}[./-]\d{4}\b"
    datas = re.findall(padrao_data, texto)
    data_generica = (
        datas[0].replace(".", "/").replace("-", "/")
        if datas else "NÃO ENCONTRADO"
    )

    if tipo != "Documento_Desconhecido":
        resultado["Nome"] = extrair_nome(texto)

    if cpf_encontrado:
        cpf_limpo = re.sub(r"\D", "", cpf_encontrado)
        resultado["CPF"] = (
            cpf_limpo if validar_cpf(cpf_limpo)
            else "ENCONTRADO - VALIDAR"
        )
    else:
        resultado["CPF"] = "NÃO ENCONTRADO"

    # Não usa a primeira data do documento: exige o rótulo de nascimento.
    resultado["Data_Nascimento"] = (
        "NÃO ENCONTRADO"
        if tipo == "Documento_Desconhecido"
        else extrair_data_por_rotulo(texto)
    )

    if tipo == "Documento_Identidade":
        resultado["RG"] = extrair_rg(texto)

    elif tipo == "Certidao_Nascimento":
        matricula = re.search(
            r"\bMATR[IÍ]CULA\s*[:.]?\s*([0-9.\- ]{10,40})",
            texto,
            re.IGNORECASE,
        )
        resultado["Matricula"] = (
            matricula.group(1).strip()
            if matricula else "NÃO ENCONTRADO"
        )

    elif tipo == "CNH":
        resultado["Matricula"] = "NÃO APLICÁVEL"

    if tipo == "Documento_Desconhecido":
        resultado["Qualquer_CPF_Encontrado"] = (
            cpf_encontrado if cpf_encontrado else "NÃO ENCONTRADO"
        )
        resultado["Qualquer_Data_Encontrada"] = data_generica

    return resultado


def avaliar_resultado(dados):
    """Classifica a extração sem confundir processamento concluído com dados completos."""
    tipo = dados.get("Tipo_Documento", "")
    obrigatorios_por_tipo = {
        "Documento_Identidade": ["Nome", "CPF", "RG", "Data_Nascimento"],
        "CNH": ["Nome", "CPF", "Data_Nascimento"],
        "Certidao_Nascimento": ["Nome", "Data_Nascimento", "Matricula"],
    }
    obrigatorios = obrigatorios_por_tipo.get(tipo)

    if not obrigatorios:
        encontrados = any(
            dados.get(campo) not in ("", "NÃO ENCONTRADO")
            for campo in ("Qualquer_CPF_Encontrado", "Qualquer_Data_Encontrada")
        )
        if encontrados:
            return "REVISAR", "Tipo não identificado; confira os dados genéricos."
        return "INCOMPLETO", "Tipo não identificado e nenhum dado genérico foi reconhecido."

    faltantes = [
        campo for campo in obrigatorios
        if dados.get(campo, "") in ("", "NÃO ENCONTRADO")
    ]
    if faltantes:
        return "INCOMPLETO", "Campos não encontrados: " + ", ".join(faltantes)

    if dados.get("CPF") == "ENCONTRADO - VALIDAR":
        return "REVISAR", "Confira manualmente o CPF reconhecido pelo OCR."

    return "SUCESSO", "Campos obrigatórios encontrados; confira visualmente os dados."


def identificar_e_extrair(caminho_pdf, ocr, log=None):
    texto = ler_pdf(caminho_pdf, ocr, log)

    if not texto.strip():
        raise RuntimeError("Nenhum texto foi reconhecido no PDF.")

    tipo = identificar_documento(texto)
    dados = extrair_campos(texto, tipo)

    # Não armazenamos o texto integral do documento no banco.
    return dados


# =========================================================
# 5. INTERFACE GRÁFICA
# =========================================================

class AplicativoOCR:
    def __init__(self, root):
        self.root = root
        self.root.title("Extrator Inteligente de PDFs")
        self.root.geometry("760x540")
        self.root.minsize(650, 450)

        self.pasta_selecionada = ""
        self.processando = False

        self.criar_interface()

    def criar_interface(self):
        tk.Label(
            self.root,
            text="Extrator Inteligente de PDFs",
            font=("Arial", 17, "bold"),
        ).pack(pady=(15, 5))

        tk.Label(
            self.root,
            text="Extração local com PyMuPDF, PaddleOCR e SQLite",
            fg="gray",
        ).pack(pady=(0, 12))

        frame_pasta = tk.Frame(self.root)
        frame_pasta.pack(fill="x", padx=20, pady=5)

        tk.Button(
            frame_pasta,
            text="Selecionar pasta de PDFs",
            command=self.selecionar_pasta,
            width=23,
        ).pack(side="left")

        self.lbl_pasta = tk.Label(
            frame_pasta,
            text="Nenhuma pasta selecionada",
            fg="gray",
            anchor="w",
        )
        self.lbl_pasta.pack(side="left", padx=10, fill="x", expand=True)

        self.btn_processar = tk.Button(
            self.root,
            text="Iniciar extração",
            command=self.iniciar_processamento,
            bg="#237a3b",
            fg="white",
            font=("Arial", 12, "bold"),
            state=tk.DISABLED,
            width=22,
        )
        self.btn_processar.pack(pady=12)

        tk.Label(self.root, text="Progresso:").pack(
            anchor="w", padx=20
        )

        self.txt_log = scrolledtext.ScrolledText(
            self.root,
            height=16,
            state=tk.DISABLED,
            bg="#f4f4f4",
            font=("Consolas", 9),
        )
        self.txt_log.pack(
            padx=20, pady=7, fill="both", expand=True
        )

        tk.Label(
            self.root,
            text="Os resultados ficam na pasta selecionada: documentos.db e CSV.",
            fg="gray",
        ).pack(pady=(0, 10))

    def log(self, mensagem):
        self.root.after(0, self._atualizar_log, mensagem)

    def _atualizar_log(self, mensagem):
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, mensagem + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def selecionar_pasta(self):
        pasta = filedialog.askdirectory(
            title="Selecione a pasta com os PDFs"
        )

        if pasta:
            self.pasta_selecionada = pasta
            self.lbl_pasta.config(text=pasta, fg="black")
            self.btn_processar.config(state=tk.NORMAL)

    def iniciar_processamento(self):
        if not self.pasta_selecionada or self.processando:
            return

        self.processando = True
        self.btn_processar.config(state=tk.DISABLED)
        self.log("=== INICIANDO PROCESSAMENTO ===")

        thread = threading.Thread(
            target=self.processar_lote,
            daemon=True,
        )
        thread.start()

    def processar_lote(self):
        conexao = None

        try:
            arquivos = sorted(
                arquivo
                for arquivo in os.listdir(self.pasta_selecionada)
                if arquivo.lower().endswith(".pdf")
            )

            if not arquivos:
                self.log("Nenhum PDF encontrado na pasta.")
                return

            caminho_banco = os.path.join(
                self.pasta_selecionada, NOME_BANCO
            )
            caminho_csv = os.path.join(
                self.pasta_selecionada, NOME_CSV
            )

            conexao = conectar_banco(caminho_banco)

            self.log("Carregando PaddleOCR...")
            ocr = inicializar_ocr()

            total = len(arquivos)
            sucessos = 0
            revisoes = 0
            incompletos = 0
            erros = 0

            for indice, nome_arquivo in enumerate(arquivos, 1):
                self.log(f"\n[{indice}/{total}] {nome_arquivo}")

                caminho_pdf = os.path.join(
                    self.pasta_selecionada, nome_arquivo
                )

                try:
                    dados = identificar_e_extrair(
                        caminho_pdf, ocr, self.log
                    )
                    dados["Arquivo"] = nome_arquivo
                    status, detalhes = avaliar_resultado(dados)
                    dados["Status"] = status
                    dados["Detalhes"] = detalhes

                    salvar_no_banco(conexao, caminho_pdf, dados)
                    if status == "SUCESSO":
                        sucessos += 1
                    elif status == "REVISAR":
                        revisoes += 1
                    else:
                        incompletos += 1

                    self.log(
                        "   Tipo identificado: "
                        + dados["Tipo_Documento"]
                    )

                except Exception as erro:
                    erros += 1
                    self.log(f"   ERRO: {erro}")

                    dados = {
                        coluna: ""
                        for coluna in COLUNAS_EXPORTACAO
                    }
                    dados["Arquivo"] = nome_arquivo
                    dados["Tipo_Documento"] = "ERRO_OCR"
                    dados["Nome"] = "NÃO PROCESSADO"
                    dados["Status"] = "ERRO"
                    # Mensagem técnica, sem conteúdo extraído do documento.
                    dados["Detalhes"] = str(erro)[:500]

                    salvar_no_banco(conexao, caminho_pdf, dados)

            exportar_csv(conexao, caminho_csv)

            self.log("\n=== PROCESSAMENTO CONCLUÍDO ===")
            self.log(f"Documentos processados: {total}")
            self.log(f"Sucessos: {sucessos}")
            self.log(f"Precisam de revisão: {revisoes}")
            self.log(f"Incompletos: {incompletos}")
            self.log(f"Erros: {erros}")
            self.log(f"Banco SQLite: {caminho_banco}")
            self.log(f"CSV: {caminho_csv}")

            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    "Processamento concluído",
                    f"Documentos: {total}\n"
                    f"Sucessos: {sucessos}\n"
                    f"Precisam de revisão: {revisoes}\n"
                    f"Incompletos: {incompletos}\n"
                    f"Erros: {erros}\n\n"
                    "O banco SQLite e o CSV foram atualizados.",
                ),
            )

        except Exception as erro:
            mensagem = str(erro)
            self.log(f"ERRO CRÍTICO: {mensagem}")
            self.root.after(
                0,
                lambda msg=mensagem: messagebox.showerror(
                    "Erro no processamento", msg
                ),
            )

        finally:
            if conexao is not None:
                conexao.close()

            self.root.after(0, self.finalizar)

    def finalizar(self):
        self.processando = False
        estado = (
            tk.NORMAL if self.pasta_selecionada else tk.DISABLED
        )
        self.btn_processar.config(state=estado)


# =========================================================
# 6. EXECUÇÃO
# =========================================================

if __name__ == "__main__":
    janela_principal = tk.Tk()
    app = AplicativoOCR(janela_principal)
    janela_principal.mainloop()
