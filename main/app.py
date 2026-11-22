
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import threading
import os
import re
import csv
import fitz

from paddleocr import PaddleOCR


# ==========================================
# 1. CONFIGURAÇÕES
# ==========================================

COLUNAS_PLANILHA = [
    "Arquivo",
    "Tipo_Documento",
    "Nome",
    "CPF",
    "RG",
    "Data_Nascimento",
    "Matricula",
    "Qualquer_CPF_Encontrado",
    "Qualquer_Data_Encontrada"
]


# ==========================================
# 2. REGRAS DE IDENTIFICAÇÃO
# ==========================================

REGRAS_POR_DOCUMENTO = {

    "Documento_Identidade": {
        "palavras_chave": [
            "identidade",
            "registro geral",
            "carteira de identidade",
            "república federativa do brasil"
        ]
    },

    "CNH": {
        "palavras_chave": [
            "carteira nacional de habilitação",
            "carteira nacional de habilitacao",
            "departamento nacional de trânsito",
            "departamento estadual de trânsito"
        ]
    },

    "Certidao_Nascimento": {
        "palavras_chave": [
            "certidão de nascimento",
            "certidao de nascimento",
            "registro civil",
            "matrícula",
            "matricula"
        ]
    },

    "Documento_Desconhecido": {
        "palavras_chave": []
    }
}


# ==========================================
# 3. INICIALIZAÇÃO DO OCR
# ==========================================

def inicializar_ocr():

    print("Inicializando PaddleOCR...")

    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False
    )

    print("PaddleOCR inicializado!")

    return ocr


# ==========================================
# 4. EXTRAÇÃO COM PADDLEOCR
# ==========================================

def extrair_texto_imagem(ocr, caminho_imagem):

    resultados = ocr.predict(caminho_imagem)

    textos = []

    for resultado in resultados:

        dados = resultado.json

        if "res" in dados:
            dados = dados["res"]

        linhas = dados.get("rec_texts", [])

        textos.extend(
            texto for texto in linhas
            if texto and texto.strip()
        )

    return "\n".join(textos)


# ==========================================
# 5. LEITURA COMPLETA DO PDF
# ==========================================

def ler_pdf(caminho_pdf, ocr, log=None):

    textos_paginas = []

    with fitz.open(caminho_pdf) as documento:

        for numero, pagina in enumerate(documento, 1):

            if log:
                log(
                    f"   Página {numero}/{len(documento)}"
                )

            # Primeiro tenta extrair o texto diretamente
            texto = pagina.get_text("text").strip()

            # Se não houver texto suficiente,
            # utiliza o PaddleOCR
            if len(texto) < 15:

                if log:
                    log("   Utilizando PaddleOCR...")

                pix = pagina.get_pixmap(
                    matrix=fitz.Matrix(2.5, 2.5),
                    alpha=False
                )

                # Salva temporariamente a página
                caminho_temp = os.path.join(
                    os.path.dirname(caminho_pdf),
                    f"_ocr_temp_{threading.get_ident()}_{numero}.png"
                )

                try:

                    pix.save(caminho_temp)

                    texto = extrair_texto_imagem(
                        ocr,
                        caminho_temp
                    )

                finally:

                    if os.path.exists(caminho_temp):
                        os.remove(caminho_temp)

            textos_paginas.append(texto)

    return "\n".join(textos_paginas)


# ==========================================
# 6. IDENTIFICAÇÃO DO DOCUMENTO
# ==========================================

def identificar_documento(texto):

    texto_minusculo = texto.lower()

    # Verifica primeiro os tipos mais específicos
    for tipo in [
        "CNH",
        "Certidao_Nascimento",
        "Documento_Identidade"
    ]:

        palavras = REGRAS_POR_DOCUMENTO[
            tipo
        ]["palavras_chave"]

        if any(
            palavra in texto_minusculo
            for palavra in palavras
        ):

            return tipo

    return "Documento_Desconhecido"


# ==========================================
# 7. EXTRAÇÃO DO NOME
# ==========================================

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
            re.IGNORECASE
        )

        if resultado:

            nome = resultado.group(1).strip()

            # Remove campos que possam aparecer
            # depois do nome na mesma linha
            nome = re.split(
                r"\b(?:CPF|RG|DATA|FILIAÇÃO|MATRÍCULA)\b",
                nome,
                flags=re.IGNORECASE
            )[0].strip(" :-")

            if len(nome) >= 4:
                return nome

            # Caso o nome esteja na próxima linha
            if indice + 1 < len(linhas):

                proxima_linha = linhas[
                    indice + 1
                ]

                if not re.search(
                    r"\b(CPF|RG|DATA|FILIAÇÃO)\b",
                    proxima_linha,
                    re.IGNORECASE
                ):

                    return proxima_linha

    return "NÃO ENCONTRADO"


# ==========================================
# 8. EXTRAÇÃO DOS CAMPOS
# ==========================================

def extrair_campos(texto, tipo):

    resultado = {
        coluna: ""
        for coluna in COLUNAS_PLANILHA
    }

    resultado["Tipo_Documento"] = tipo

    # CPF
    padrao_cpf = (
        r"\b\d{3}\.?\s?\d{3}\.?\s?\d{3}-?\s?\d{2}\b"
    )

    cpf = re.search(
        padrao_cpf,
        texto
    )

    # Data de nascimento
    padrao_data = (
        r"\b\d{2}/\d{2}/\d{4}\b"
    )

    data = re.search(
        padrao_data,
        texto
    )

    # Nome
    if tipo != "Documento_Desconhecido":

        resultado["Nome"] = extrair_nome(texto)

    # CPF e data para documentos conhecidos
    if cpf:

        resultado["CPF"] = re.sub(
            r"\D",
            "",
            cpf.group()
        )

    else:

        resultado["CPF"] = "NÃO ENCONTRADO"

    if data:

        resultado["Data_Nascimento"] = data.group()

    else:

        resultado["Data_Nascimento"] = "NÃO ENCONTRADO"

    # RG
    if tipo == "Documento_Identidade":

        rg = re.search(
            r"\bRG\s*[:.]?\s*([0-9A-Za-z.\-/]{5,20})",
            texto,
            re.IGNORECASE
        )

        if rg:

            resultado["RG"] = re.sub(
                r"[^0-9A-Za-z]",
                "",
                rg.group(1)
            )

        else:

            resultado["RG"] = "NÃO ENCONTRADO"

    # Matrícula
    if tipo == "Certidao_Nascimento":

        matricula = re.search(
            r"\bMATR[IÍ]CULA\s*[:.]?\s*([0-9.\- ]{10,40})",
            texto,
            re.IGNORECASE
        )

        if matricula:

            resultado["Matricula"] = (
                matricula.group(1).strip()
            )

        else:

            resultado["Matricula"] = "NÃO ENCONTRADO"

    # CNH
    if tipo == "CNH":

        resultado["Matricula"] = "NÃO APLICÁVEL"

    # Documentos desconhecidos
    if tipo == "Documento_Desconhecido":

        resultado["Qualquer_CPF_Encontrado"] = (
            cpf.group() if cpf else "NÃO ENCONTRADO"
        )

        resultado["Qualquer_Data_Encontrada"] = (
            data.group() if data else "NÃO ENCONTRADO"
        )

    return resultado


# ==========================================
# 9. MOTOR PRINCIPAL
# ==========================================

def identificar_e_extrair(caminho_pdf, ocr, log=None):

    texto = ler_pdf(
        caminho_pdf,
        ocr,
        log
    )

    if not texto.strip():

        raise Exception(
            "Nenhum texto foi reconhecido no PDF."
        )

    tipo = identificar_documento(texto)

    dados = extrair_campos(
        texto,
        tipo
    )

    return dados


# ==========================================
# 10. INTERFACE GRÁFICA
# ==========================================

class AplicativoOCR:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "Extrator de PDFs Automático"
        )

        self.root.geometry("600x450")

        self.pasta_selecionada = ""

        self.criar_interface()

    def criar_interface(self):

        tk.Label(
            self.root,
            text="Extrator Inteligente de PDFs",
            font=("Arial", 16, "bold")
        ).pack(pady=10)

        frame_pasta = tk.Frame(self.root)

        frame_pasta.pack(
            pady=10,
            fill="x",
            padx=20
        )

        tk.Button(
            frame_pasta,
            text="1. Selecionar Pasta de PDFs",
            command=self.selecionar_pasta,
            width=25
        ).pack(side="left")

        self.lbl_pasta = tk.Label(
            frame_pasta,
            text="Nenhuma pasta selecionada",
            fg="gray"
        )

        self.lbl_pasta.pack(
            side="left",
            padx=10
        )

        self.btn_processar = tk.Button(
            self.root,
            text="2. Iniciar Extração",
            command=self.iniciar_processamento,
            bg="green",
            fg="white",
            font=("Arial", 12, "bold"),
            state=tk.DISABLED
        )

        self.btn_processar.pack(pady=10)

        tk.Label(
            self.root,
            text="Progresso:"
        ).pack(
            anchor="w",
            padx=20
        )

        self.txt_log = scrolledtext.ScrolledText(
            self.root,
            width=70,
            height=12,
            state=tk.DISABLED,
            bg="#f0f0f0"
        )

        self.txt_log.pack(
            padx=20,
            pady=5
        )

    # ======================================
    # LOG SEGURO PARA THREADS
    # ======================================

    def log(self, mensagem):

        self.root.after(
            0,
            lambda: self._atualizar_log(mensagem)
        )

    def _atualizar_log(self, mensagem):

        self.txt_log.config(
            state=tk.NORMAL
        )

        self.txt_log.insert(
            tk.END,
            mensagem + "\n"
        )

        self.txt_log.see(
            tk.END
        )

        self.txt_log.config(
            state=tk.DISABLED
        )

    # ======================================
    # SELECIONAR PASTA
    # ======================================

    def selecionar_pasta(self):

        pasta = filedialog.askdirectory(
            title="Selecione a pasta com os PDFs"
        )

        if pasta:

            self.pasta_selecionada = pasta

            self.lbl_pasta.config(
                text=pasta,
                fg="black"
            )

            self.btn_processar.config(
                state=tk.NORMAL
            )

    # ======================================
    # INICIAR PROCESSAMENTO
    # ======================================

    def iniciar_processamento(self):

        if not self.pasta_selecionada:
            return

        self.btn_processar.config(
            state=tk.DISABLED
        )

        self.log(
            "=== INICIANDO PROCESSAMENTO ==="
        )

        thread = threading.Thread(
            target=self.processar_lote,
            daemon=True
        )

        thread.start()

    # ======================================
    # PROCESSAMENTO EM LOTE
    # ======================================

    def processar_lote(self):

        arquivos = sorted([
            arquivo
            for arquivo in os.listdir(
                self.pasta_selecionada
            )
            if arquivo.lower().endswith(".pdf")
        ])

        if not arquivos:

            self.log(
                "Nenhum PDF encontrado!"
            )

            self.finalizar()

            return

        arquivo_csv = os.path.join(
            self.pasta_selecionada,
            "resultado_extracao.csv"
        )

        try:

            self.log(
                "Carregando motor PaddleOCR..."
            )

            # Inicializa apenas uma vez
            ocr = inicializar_ocr()

            with open(
                arquivo_csv,
                "w",
                newline="",
                encoding="utf-8-sig"
            ) as arquivo:

                escritor = csv.DictWriter(
                    arquivo,
                    fieldnames=COLUNAS_PLANILHA
                )

                escritor.writeheader()

                for indice, nome_arquivo in enumerate(
                    arquivos,
                    1
                ):

                    self.log(
                        f"\nLendo [{indice}/{len(arquivos)}]: "
                        f"{nome_arquivo}"
                    )

                    caminho = os.path.join(
                        self.pasta_selecionada,
                        nome_arquivo
                    )

                    try:

                        dados = identificar_e_extrair(
                            caminho,
                            ocr,
                            self.log
                        )

                        dados["Arquivo"] = nome_arquivo

                        escritor.writerow(dados)

                        arquivo.flush()

                        self.log(
                            "   -> Tipo identificado: "
                            + dados["Tipo_Documento"]
                        )

                    except Exception as erro:

                        self.log(
                            f"   -> ERRO: {erro}"
                        )

                        dados = {
                            coluna: ""
                            for coluna in COLUNAS_PLANILHA
                        }

                        dados["Arquivo"] = nome_arquivo
                        dados["Tipo_Documento"] = "ERRO_OCR"
                        dados["Nome"] = str(erro)

                        escritor.writerow(dados)

                        arquivo.flush()

            self.log(
                "\n=== PROCESSAMENTO CONCLUÍDO ==="
            )

            self.log(
                f"CSV salvo em:\n{arquivo_csv}"
            )

            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    "Sucesso",
                    "Processamento concluído!"
                )
            )

        except Exception as erro:

            mensagem = str(erro)

            self.log(
                f"ERRO CRÍTICO: {mensagem}"
            )

            self.root.after(
                0,
                lambda msg=mensagem: messagebox.showerror(
                    "Erro",
                    msg
                )
            )

        finally:

            self.finalizar()

    # ======================================
    # FINALIZAR
    # ======================================

    def finalizar(self):

        self.root.after(
            0,
            lambda: self.btn_processar.config(
                state=tk.NORMAL
                if self.pasta_selecionada
                else tk.DISABLED
            )
        )


# ==========================================
# 11. EXECUÇÃO
# ==========================================

if __name__ == "__main__":

    janela_principal = tk.Tk()

    app = AplicativoOCR(
        janela_principal
    )

    janela_principal.mainloop()
