
import os
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from contextlib import closing
from pathlib import Path
from threading import Lock, Thread
from queue import Queue, Empty


INTERVALO_RETENCAO_MS = 60_000

_ERRO_APRESENTACAO = "Não foi possível preparar ou exibir o relatório da análise."
_ERRO_PERSISTENCIA = "Não foi possível concluir a operação no banco local."


def _rotulo(valor):
    if not valor:
        return ""
    return {
        "RG": "RG", "CPF": "CPF", "CNH": "CNH", "CIN": "CIN",
        "DOCUMENTO_IDENTIDADE": "Documento de identidade",
        "DOCUMENTO_DESCONHECIDO": "Documento não identificado",
        "CERTIDAO_CASAMENTO": "Certidão de casamento",
        "NAO_ENCONTRADO": "Não encontrado", "NAO_APLICAVEL": "Não aplicável",
        "AMBIGUO": "Ambíguo · revisar", "INVALIDO": "Inválido · revisar",
        "EVIDENCIA_INSUFICIENTE": "Evidência insuficiente",
        "TITULAR_CONFIRMADO": "Encontrado · titular confirmado",
        "SEM_PARES_ELEGIVEIS": "Sem pares elegíveis", "AGUARDANDO_ATRIBUICAO": "Aguardando atribuição",
    }.get(valor, valor.replace("_", " ").capitalize())


class JanelaPrincipal:
    def __init__(self, root, *, pasta_inicial=None, demonstracao=False):
        self.root = root
        self.pasta_inicial = pasta_inicial
        self.demonstracao = demonstracao
        self.root.title("Projeto CEUMA - Demonstração sintética" if demonstracao else "Projeto CEUMA - Análise Documental")
        self.root.geometry("1080x720")
        self.root.minsize(650, 480)

        self.arquivos_selecionados = ()
        self._fechando = False
        self.pasta_selecionada = None
        self.processando = False
        self.relatorio_atual = None
        self.expiracao_relatorio = None
        self.codigo_recuperacao = None
        self.fila = Queue()
        self._trava_banco = Lock()
        self._limpando = False
        self._exportando = False
        self._origens_lote = (None, {})
        self._releitura_origem = None

        self._orientador = None
        self.criar_interface()
        if os.environ.get("CEUMA_OCR_PREAQUECER") == "1":
            from ceuma.leitura.ocr.adaptativo import modo_ocr, modo_orientacao

            if modo_ocr() != "paddle" and modo_orientacao() == "paddle":
                self._preparar_orientacao()
                self.root.bind("<Destroy>", self._encerrar_orientacao, add="+")

        # Verifica mensagens da thread sem travar o Tkinter.
        self._evento_fila = self.root.after(150, self.verificar_fila)
        self._evento_retencao = self.root.after(0, self.verificar_retencao)

    def _preparar_orientacao(self):
        from ceuma.leitura.ocr.orientacao import OrientacaoPreparada

        if self._orientador is None:
            self._orientador = OrientacaoPreparada()

    def _encerrar_orientacao(self, evento):
        if evento.widget is self.root and self._orientador is not None:
            self._orientador.fechar()

    def criar_interface(self):
        painel = ttk.Frame(self.root, padding=16)
        painel.pack(fill="both", expand=True)
        titulo = "CEUMA | Demonstração sintética" if self.demonstracao else "CEUMA | Análise Documental"
        ttk.Label(painel, text=titulo, font=("Segoe UI", 20, "bold")).pack(anchor="w")

        acoes = ttk.Frame(painel)
        acoes.pack(fill="x", pady=10)
        ttk.Label(acoes, text="Modo").pack(side="left")
        self.modo = ttk.Combobox(acoes, values=("Individual", "Familiar"), state="readonly", width=12)
        self.modo.current(0)
        self.modo.pack(side="left", padx=8)
        self.botao_pasta = ttk.Button(acoes, text="Selecionar PDFs", command=self.selecionar_pdfs)
        self.botao_pasta.pack(side="left")
        self.botao_processar = ttk.Button(acoes, text="Iniciar análise", command=self.iniciar_analise, state="disabled")
        self.botao_processar.pack(side="left", padx=8)
        self.label_pasta = ttk.Label(painel, text="Nenhum PDF selecionado")
        self.label_pasta.pack(anchor="w")
        self.status = ttk.Label(painel, text="Status: aguardando documentos")
        self.status.pack(anchor="w", pady=(2, 8))

        centro = ttk.Panedwindow(painel, orient="horizontal")
        centro.pack(fill="both", expand=True)
        documentos = ttk.LabelFrame(centro, text="Documentos")
        campos = ttk.LabelFrame(centro, text="Campos do documento")
        centro.add(documentos, weight=2)
        centro.add(campos, weight=3)
        self.arvore_documentos = self._criar_tabela(documentos, (
            ("arquivo", "Arquivo", 150), ("tipo", "Tipo", 125), ("estado", "Estado", 110),
        ))
        self.arvore_documentos.bind("<<TreeviewSelect>>", self._mostrar_campos)
        self.atribuicao = ttk.Label(documentos, wraplength=300)
        self.atribuicao.pack(fill="x", padx=4, pady=4)
        self.aviso_campos = ttk.Label(campos, wraplength=420)
        self.aviso_campos.pack(fill="x", padx=4, pady=4)
        detalhe = ttk.Frame(campos)
        detalhe.pack(side="bottom", fill="x", padx=4, pady=4)
        ttk.Label(detalhe, text="Valor selecionado").pack(side="left")
        self.valor_campo = tk.StringVar(master=self.root)
        ttk.Entry(detalhe, textvariable=self.valor_campo, state="readonly", exportselection=False).pack(
            side="left", fill="x", expand=True, padx=(6, 0),
        )
        self.arvore_campos = self._criar_tabela(campos, (
            ("campo", "Campo", 135), ("valor", "Valor", 125),
            ("pagina", "Página", 52), ("estado", "Estado", 105),
        ))

        self.arvore_campos.bind("<<TreeviewSelect>>", self._mostrar_valor_campo)

        rodape = ttk.LabelFrame(painel, text="Conferência")
        rodape.pack(fill="x", pady=(8, 0))
        self.resumo = ttk.Label(rodape, text="Nenhuma análise em exibição", wraplength=600)
        self.resumo.pack(anchor="w", padx=6, pady=4)
        botoes = ttk.Frame(rodape)
        botoes.pack(fill="x", padx=6)
        self.botao_recuperar = ttk.Button(botoes, text="Recuperar relatório", command=self.recuperar_relatorio)
        self.botao_recuperar.pack(side="left")
        self.botao_exportar = ttk.Button(
            botoes, text="Exportar CSV", command=self.exportar_relatorio, state="disabled",
        )
        self.botao_exportar.pack(side="left", padx=8)
        self.botao_ocr_avancado = ttk.Button(
            botoes, text="Reprocessar com OCR avançado", command=self.reprocessar_com_ocr_avancado,
            state="disabled",
        )
        self.botao_ocr_avancado.pack(side="left")
        self.log = scrolledtext.ScrolledText(rodape, height=5, state="disabled", font=("Segoe UI", 10))
        self.log.pack(fill="x", padx=6, pady=6)
        self.root.protocol("WM_DELETE_WINDOW", self._solicitar_fechamento)

    @staticmethod
    def _criar_tabela(quadro, colunas):
        pagina = ttk.Frame(quadro)
        pagina.pack(fill="both", expand=True)
        arvore = ttk.Treeview(pagina, columns=[coluna[0] for coluna in colunas], show="headings", selectmode="browse")
        barra = ttk.Scrollbar(pagina, orient="vertical", command=arvore.yview)
        arvore.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        arvore.pack(fill="both", expand=True)
        for chave, titulo, largura in colunas:
            arvore.heading(chave, text=titulo)
            arvore.column(chave, width=largura, minwidth=40)
        return arvore

    def selecionar_pdfs(self):
        if self.processando or self._exportando or self._fechando:
            return
        escolhidos = filedialog.askopenfilenames(
            parent=self.root, title="Selecionar PDFs", initialdir=self.pasta_inicial,
            filetypes=[("Documentos PDF", "*.pdf")],
        )
        if not escolhidos:
            return
        arquivos = tuple(dict.fromkeys(Path(caminho) for caminho in escolhidos))
        try:
            validos = all(arquivo.suffix.lower() == ".pdf" and arquivo.is_file() for arquivo in arquivos)
        except OSError:
            validos = False
        if not validos:
            self.status.config(text="Selecione arquivos PDF existentes.")
            return
        self.pasta_selecionada = None
        self._descartar_relatorio()
        self.arquivos_selecionados = arquivos
        self._mostrar_selecao()
        self.label_pasta.config(text=f"{len(arquivos)} PDF(s) selecionado(s)")
        self.status.config(text="Status: pronto para análise")
        self.botao_processar.config(state="normal")

    def _mostrar_selecao(self):
        self.arvore_documentos.delete(*self.arvore_documentos.get_children())
        for arquivo in self.arquivos_selecionados:
            self.arvore_documentos.insert("", "end", values=(arquivo.name, "", "Selecionado"))

    def _mostrar_resultado(self, relatorio):
        self.arvore_documentos.delete(*self.arvore_documentos.get_children())
        pendentes = set(relatorio.get("leituras_pendentes", ()))
        self.resumo.config(text=(
            f"{relatorio['codigo']}  |  {relatorio.get('modo', '').capitalize()}  |  "
            f"{relatorio['total_documentos']} documento(s)  |  {relatorio['total_conferencias']} conferência(s)  |  "
            f"{relatorio['total_divergencias']} divergência(s)\n"
            f"{_rotulo(relatorio.get('estado_conferencia', ''))}  |  "
            f"{len(pendentes)} leitura(s) para revisar  |  "
            f"{len(relatorio.get('documentos_inconclusivos', ()))} atribuição(ões) pendente(s)"
        ))
        for doc in relatorio["documentos"]:
            estado = (
                "Revisar leitura" if doc["id"] in pendentes else
                "Não identificado" if doc["tipo"] in {"DOCUMENTO_DESCONHECIDO", "INCONCLUSIVO"} else "Processado"
            )
            self.arvore_documentos.insert("", "end", iid=str(doc["id"]),
                values=(doc["arquivo"], _rotulo(doc["tipo"]), estado))
        for divergencia in relatorio.get("conferencias_estruturadas", ()):
            if divergencia["resultado"] == "DIVERGENTE":
                self.registrar_log(
                    f"Divergência em {_rotulo(divergencia['campo'])} entre documentos "
                    f"#{divergencia['documento_1_id']} e #{divergencia['documento_2_id']}."
                )
        filhos = self.arvore_documentos.get_children()
        if filhos:
            self.arvore_documentos.selection_set(filhos[0])
            self._mostrar_campos()

    def _mostrar_valor_campo(self, evento=None):
        self.valor_campo.set("")
        if self.relatorio_atual is None or self._verificar_prazo_relatorio():
            return
        selecao = self.arvore_campos.selection()
        if selecao:
            self.valor_campo.set(self.arvore_campos.item(selecao[0], "values")[1])

    def _mostrar_campos(self, evento=None):
        self.valor_campo.set("")
        self.arvore_campos.delete(*self.arvore_campos.get_children())
        self.atribuicao.config(text="")
        self.aviso_campos.config(text="")
        self._atualizar_acao_releitura()
        selecao = self.arvore_documentos.selection()
        if not selecao or self.relatorio_atual is None or self._verificar_prazo_relatorio():
            return
        doc = int(selecao[0])
        relatorio = self.relatorio_atual
        campos_visiveis = set()
        for item in relatorio["campos_extraidos"]:
            if item["documento_id"] == doc:
                campos_visiveis.add(item["campo"])
                self.arvore_campos.insert("", "end", values=(
                    _rotulo(item["campo"]), item["valor"] or "", item["pagina"] or "", _rotulo(item["status"]),
                ))
        for item in relatorio["estados_campos"]:
            if item["documento_id"] == doc and item["campo"] not in campos_visiveis:
                self.arvore_campos.insert("", "end", values=(_rotulo(item["campo"]), "", "", _rotulo(item["status"])))
        for item in relatorio.get("itens_documentais", ()):
            if item["documento_id"] == doc:
                for campo in item["campos"]:
                    self.arvore_campos.insert("", "end", values=(
                        f"Item {item['ordem']} · {_rotulo(campo['nome_campo'])}", campo["valor"] or "",
                        campo["pagina_origem"] or "", _rotulo(campo["status"]),
                    ))
        if not self.arvore_campos.get_children():
            aviso = "Nenhum campo recuperado para este documento."
            if doc in relatorio.get("leituras_pendentes", ()):
                aviso += " Confira a leitura e o documento original."
            self.aviso_campos.config(text=aviso)
        if relatorio.get("modo") == "FAMILIAR":
            pessoas = [
                f"Pessoa #{p['pessoa_id']} ({_rotulo(p['papel'])})"
                for p in relatorio["pessoas"] if doc in p["documentos_ids"]
            ]
            if doc in relatorio["documentos_compartilhados"]:
                pessoas.insert(0, "Compartilhado")
            self.atribuicao.config(text=" · ".join(pessoas) or "Atribuição pendente")

    def _origem_selecionada(self):
        if self.processando or self._exportando or self._fechando or self.relatorio_atual is None:
            return None
        codigo, origens = self._origens_lote
        selecionados = self.arvore_documentos.selection()
        if selecionados and codigo == self.relatorio_atual["codigo"]:
            return origens.get(int(selecionados[0]))
        return None

    def _atualizar_acao_releitura(self):
        if hasattr(self, "botao_ocr_avancado"):
            self.botao_ocr_avancado.config(state="normal" if self._origem_selecionada() else "disabled")

    def reprocessar_com_ocr_avancado(self):
        if self._verificar_prazo_relatorio() or not (origem := self._origem_selecionada()):
            return
        relatorio = self.relatorio_atual
        if not messagebox.askyesno(
            "Reprocessar com OCR avançado",
            "A releitura local deste PDF pode demorar. Se houver resultado, será criada "
            "uma análise separada para este documento. A análise anterior continuará disponível "
            "até o fim do seu prazo. Deseja iniciar?",
            parent=self.root, default="no",
        ):
            return
        if self._verificar_prazo_relatorio() or self.relatorio_atual is not relatorio or origem != self._origem_selecionada():
            return
        self._releitura_origem = relatorio["codigo"]
        self._ocupar_interface("Status: reprocessando com OCR avançado…", preservar_relatorio=True)
        arquivo, resumo = origem
        Thread(
            target=self.executar_processamento, daemon=True,
            args=(None, (arquivo,), relatorio["modo"]), kwargs={"releitura_sha256": resumo},
        ).start()

    def _solicitar_fechamento(self):
        self._fechando = True
        self._atualizar_acao_releitura()
        if self.processando or self._exportando or self._limpando:
            for botao in (self.botao_pasta, self.botao_processar, self.botao_recuperar, self.botao_exportar):
                botao.config(state="disabled")
            self.status.config(text="Aguardando a operação atual para fechar…")
        else:
            self._fechar_janela()

    def _fechar_janela(self):
        for evento in (self._evento_fila, self._evento_retencao):
            if evento is not None:
                self.root.after_cancel(evento)
        self.root.destroy()

    def registrar_log(self, mensagem):
        self.log.config(state="normal")
        self.log.insert("end", mensagem + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _descartar_relatorio(self):
        self.relatorio_atual = None
        self.expiracao_relatorio = None
        self.botao_exportar.config(state="disabled")
        self._atualizar_acao_releitura()
        if hasattr(self, "arvore_documentos"):
            self.arvore_documentos.delete(*self.arvore_documentos.get_children())
            self.arvore_campos.delete(*self.arvore_campos.get_children())
            self.valor_campo.set("")
            self.atribuicao.config(text="")
            self.aviso_campos.config(text="")
            self.resumo.config(text="Nenhuma análise em exibição")
            self.log.config(state="normal")
            self.log.delete("1.0", "end")
            self.log.config(state="disabled")

    def _invalidar_relatorio(self, codigo):
        if self.relatorio_atual is None or self.relatorio_atual["codigo"] != codigo:
            return
        if codigo == self._origens_lote[0]:
            self._origens_lote = (None, {})
        self._descartar_relatorio()
        self.status.config(text="Status: relatório expirado ou indisponível")
        self.registrar_log("O relatório expirou ou não está mais disponível.")

    def _verificar_prazo_relatorio(self):
        if self.relatorio_atual is None:
            return False
        from ceuma.banco.conexao import agora_utc

        if self.expiracao_relatorio is not None and self.expiracao_relatorio > agora_utc():
            return False
        self._invalidar_relatorio(self.relatorio_atual["codigo"])
        return True

    def verificar_retencao(self):
        """Programa a manutenção no início e enquanto a janela estiver aberta."""
        self._verificar_prazo_relatorio()
        if not self._limpando and not self._fechando and not self.processando and not self._exportando:
            self._limpando = True
            codigo = self.relatorio_atual["codigo"] if self.relatorio_atual else None
            Thread(target=self._executar_retencao, args=(codigo,), daemon=True).start()
        self._evento_retencao = self.root.after(INTERVALO_RETENCAO_MS, self.verificar_retencao)

    def _executar_retencao(self, codigo):
        try:
            from ceuma.banco.conexao import (
                conectar_banco, consultar_expiracao_ativa, criar_tabelas, limpar_analises_expiradas,
            )

            with self._trava_banco, closing(conectar_banco()) as conexao:
                criar_tabelas(conexao)
                limpar_analises_expiradas(conexao)
                indisponivel = codigo is not None and consultar_expiracao_ativa(conexao, codigo) is None
        except Exception:
            self.fila.put(("erro_retencao", None))
        else:
            self.fila.put(("retencao", codigo if indisponivel else None))

    def selecionar_pasta(self):
        if self.processando or self._exportando or self._fechando:
            return

        pasta = filedialog.askdirectory(
            title="Selecione a pasta com os PDFs"
        )

        if not pasta:
            return

        caminho = Path(pasta)

        try:
            arquivos = sorted(
                arquivo
                for arquivo in caminho.iterdir()
                if arquivo.is_file()
                and arquivo.suffix.lower() == ".pdf"
            )
        except OSError:
            messagebox.showerror("Erro de processamento", "Não foi possível acessar a pasta selecionada.")
            return

        if not arquivos:
            self.pasta_selecionada = None
            self._descartar_relatorio()
            self.botao_processar.config(state="disabled")
            self.label_pasta.config(
                text="Nenhum PDF encontrado na pasta."
            )
            return

        self.arquivos_selecionados = ()
        self.pasta_selecionada = caminho
        self._descartar_relatorio()

        self.label_pasta.config(
            text=f"Pasta: {caminho}\nPDFs encontrados: {len(arquivos)}"
        )

        self.botao_processar.config(state="normal")
        self.status.config(text="Status: pronto para análise")

        self.registrar_log(
            f"Pasta selecionada: {len(arquivos)} PDF(s)"
        )

    def iniciar_analise(self):
        if self.processando or self._exportando or self._fechando or not (self.pasta_selecionada or self.arquivos_selecionados):
            return

        self._ocupar_interface("Status: processando documentos...")
        self.registrar_log("Iniciando análise...")

        if self.arquivos_selecionados:
            self._mostrar_selecao()
            modo = "FAMILIAR" if self.modo.get() == "Familiar" else "INDIVIDUAL"
            args = (None, self.arquivos_selecionados, modo)
        else:
            args = (str(self.pasta_selecionada),)
        thread = Thread(
            target=self.executar_processamento,
            args=args,
            daemon=True
        )
        thread.start()

    def _ocupar_interface(self, status, *, preservar_relatorio=False):
        self.processando = True
        if preservar_relatorio:
            self.botao_exportar.config(state="disabled")
            self._atualizar_acao_releitura()
        else:
            self._descartar_relatorio()
        self.botao_processar.config(state="disabled")
        self.botao_pasta.config(state="disabled")
        self.botao_recuperar.config(state="disabled")
        self.status.config(text=status)
        if hasattr(self, "modo"):
            self.modo.config(state="disabled")

    def recuperar_relatorio(self):
        if self.processando or self._exportando or self._fechando:
            return
        codigo = simpledialog.askstring(
            "Recuperar relatório",
            "Informe o código da análise ou deixe em branco para recuperar a mais recente disponível.",
            initialvalue=self.codigo_recuperacao or "",
            parent=self.root,
        )
        if codigo is None:
            return
        self._ocupar_interface("Status: recuperando relatório...")
        Thread(target=self._executar_recuperacao, args=(codigo.strip().upper() or None,), daemon=True).start()

    def _executar_recuperacao(self, codigo):
        with self._trava_banco:
            self._carregar_relatorio(codigo)

    def _carregar_relatorio(self, codigo):
        from ceuma.relatorios.relatorio import ErroApresentacao, RelatorioIndisponivel, recuperar_relatorio
        from ceuma.banco.conexao import conectar_banco, criar_tabelas

        try:
            with closing(conectar_banco()) as conexao:
                criar_tabelas(conexao)
                resultado = recuperar_relatorio(conexao, codigo)
        except RelatorioIndisponivel:
            self.fila.put(("relatorio_indisponivel", "Nenhuma análise concluída e ativa foi encontrada."))
        except ErroApresentacao as erro:
            self.fila.put(("erro", (_ERRO_APRESENTACAO, "apresentacao", erro.codigo)))
        except Exception:
            self.fila.put(("erro", (_ERRO_PERSISTENCIA, "persistencia", None)))
        else:
            self.fila.put(("sucesso", resultado))

    def executar_processamento(self, pasta, arquivos=None, modo="INDIVIDUAL", *, releitura_sha256=None):
        """
        Executado em uma thread separada.
        Não acessa diretamente componentes Tkinter.
        """
        with self._trava_banco:
            try:
                if releitura_sha256 is not None:
                    from ceuma.leitura.ocr.paddle import modelos_reconhecimento_locais

                    try:
                        modelos_reconhecimento_locais()
                    except FileNotFoundError:
                        self.fila.put(("erro", ("Os modelos locais do OCR avançado estão indisponíveis.", "processamento", None)))
                        return
                    codigo = self._processar_lote(pasta, arquivos, modo, releitura_sha256=releitura_sha256)
                elif arquivos is not None:
                    codigo = self._processar_lote(pasta, arquivos, modo)
                else:
                    codigo = self._processar_lote(pasta)
            except Exception:
                self.fila.put(("erro", (_ERRO_PERSISTENCIA, "persistencia", None)))
                return
            if codigo is not None:
                self._carregar_relatorio(codigo)

    def _processar_lote(self, pasta, arquivos=None, modo="INDIVIDUAL", *, releitura_sha256=None):
        conexao = None
        codigo = None
        etapa = "persistencia"
        mensagem = _ERRO_PERSISTENCIA

        try:
            from ceuma.banco.conexao import (
                conectar_banco,
                criar_tabelas,
                criar_analise,
                limpar_analises_expiradas
            )
            conexao = conectar_banco()
            criar_tabelas(conexao)
            limpar_analises_expiradas(conexao)

            codigo = criar_analise(
                conexao,
                tipo_procedimento="ANALISE_DOCUMENTAL", modo=modo,
            )

            registro = conexao.execute(
                "SELECT id FROM analises WHERE codigo = ?",
                (codigo,)
            ).fetchone()

            if registro is None:
                raise RuntimeError(
                    "Não foi possível localizar a análise criada."
                )

            analise_id = registro["id"]

            self.fila.put(("log", f"Análise local #{analise_id} iniciada."))

            etapa = "processamento"
            from ceuma.processamento.lote import ErroProcessamento, processar_lote
            try:
                orientador = getattr(self, "_orientador", None)
                if arquivos is not None:
                    resultado = processar_lote(
                        conexao, analise_id, arquivos=arquivos, orientador=orientador,
                        progresso=lambda numero, total: self.fila.put(("progresso", (numero, total))),
                        releitura_sha256=releitura_sha256,
                    )
                elif orientador is None:
                    resultado = processar_lote(conexao, analise_id, pasta)
                else:
                    resultado = processar_lote(conexao, analise_id, pasta, orientador=orientador)
            except ErroProcessamento as erro:
                mensagem = str(erro)  # A classe constrói a mensagem com o número local.
                raise
            except ValueError:
                mensagem = "O lote não pôde ser iniciado. Confira a seleção de PDFs e o estado da análise."
                raise
            except Exception:
                etapa = "persistencia"
                raise

        except Exception:
            if conexao is not None:
                try:
                    conexao.rollback()
                    if codigo is not None:
                        with conexao:
                            conexao.execute("""
                                DELETE FROM analises
                                WHERE codigo = ? AND status = 'EM_ANDAMENTO'
                                  AND NOT EXISTS (
                                      SELECT 1 FROM documentos WHERE analise_id = analises.id
                                  )
                            """, (codigo,))
                except Exception:
                    self.fila.put(("log", "Não foi possível remover a análise vazia após a falha."))
            self.fila.put(("erro", (mensagem, etapa, None)))
            return None

        else:
            for duplicata in resultado["duplicatas"]:
                self.fila.put(("log", duplicata["mensagem"]))
            if releitura_sha256 is None:
                self.fila.put(("origens_releitura", (codigo, resultado.get("origens_releitura", {}))))
            return codigo

        finally:
            if conexao is not None:
                conexao.close()

    def verificar_fila(self):
        try:
            while True:
                tipo, dados = self.fila.get_nowait()

                if tipo == "log":
                    self.registrar_log(dados)

                elif tipo == "progresso":
                    numero, total = dados
                    if not self._fechando:
                        mensagem = ("OCR avançado" if self._releitura_origem else "Processando")
                        self.status.config(text=f"{mensagem}: documento {numero} de {total}…")
                elif tipo == "origens_releitura":
                    self._origens_lote = dados
                elif tipo == "sucesso":
                    relatorio, expiracao = dados
                    anterior = self.relatorio_atual, self.expiracao_relatorio, self.codigo_recuperacao
                    if self._releitura_origem:
                        from ceuma.banco.conexao import agora_utc

                        if expiracao <= agora_utc():
                            self._encerrar_releitura_com_falha("O novo relatório já está indisponível.")
                            continue
                    try:
                        self.finalizar_sucesso(relatorio, expiracao)
                    except Exception:
                        if self._releitura_origem:
                            self.relatorio_atual, self.expiracao_relatorio, self.codigo_recuperacao = anterior
                            if self.relatorio_atual is not None and not self._verificar_prazo_relatorio():
                                try:
                                    self._mostrar_resultado(self.relatorio_atual)
                                except Exception:
                                    self.registrar_log("Não foi possível redesenhar o relatório anterior.")
                        self.finalizar_erro(_ERRO_APRESENTACAO, "apresentacao", relatorio.get("codigo"))
                    else:
                        origem = self._releitura_origem
                        self._releitura_origem = None
                        if origem and self.relatorio_atual is not None:
                            self.registrar_log(
                                f"Releitura concluída em análise separada: {relatorio['codigo']}. "
                                f"Análise anterior: {origem}."
                            )

                elif tipo == "erro":
                    self.finalizar_erro(*dados)
                elif tipo == "relatorio_indisponivel":
                    self._finalizar_relatorio_indisponivel(dados)
                elif tipo == "retencao":
                    self._limpando = False
                    if dados is not None:
                        self._invalidar_relatorio(dados)
                elif tipo == "erro_retencao":
                    self._limpando = False
                    self.registrar_log("Não foi possível limpar análises expiradas. O sistema tentará novamente.")
                elif tipo == "exportacao":
                    self._finalizar_exportacao(*dados)

        except Empty:
            self._verificar_prazo_relatorio()
        finally:
            if self._fechando and not (self.processando or self._exportando or self._limpando):
                self._fechar_janela()
            else:
                self._evento_fila = self.root.after(150, self.verificar_fila)

    def finalizar_sucesso(self, relatorio, expiracao):
        self._liberar_interface()
        self.relatorio_atual = relatorio
        self.expiracao_relatorio = expiracao
        if self._verificar_prazo_relatorio():
            return
        self.codigo_recuperacao = relatorio["codigo"]
        if not self._exportando:
            self.botao_exportar.config(state="normal")

        pendentes = len(relatorio.get("leituras_pendentes", ()))
        self.status.config(
            text="Status: leitura parcial, requer revisão" if pendentes else "Status: análise concluída"
        )

        self.registrar_log(
            f"Documentos: {relatorio['total_documentos']}"
        )
        self.registrar_log(
            f"Conferências: {relatorio['total_conferencias']}"
        )
        self.registrar_log(
            f"Divergências: {relatorio['total_divergencias']}"
        )
        if hasattr(self, "arvore_documentos"):
            self._mostrar_resultado(relatorio)
            return
        if pendentes:
            aviso = f"Leitura parcial. Documentos que requerem revisão: {pendentes}."
            self.registrar_log(aviso)
            messagebox.showwarning("Projeto CEUMA", aviso)
            return
        self.registrar_log("Análise concluída.")

        messagebox.showinfo(
            "Projeto CEUMA",
            "Análise concluída!\n\n"
            f"Documentos: {relatorio['total_documentos']}\n"
            f"Conferências: {relatorio['total_conferencias']}\n"
            f"Divergências: {relatorio['total_divergencias']}"
        )

    def exportar_relatorio(self):
        from ceuma.relatorios.exportador_csv import identificar_destino_csv

        if self._verificar_prazo_relatorio():
            messagebox.showwarning("Projeto CEUMA", "O relatório expirou e não pode ser exportado.")
            return
        if self.processando or self._exportando or self.relatorio_atual is None:
            messagebox.showwarning(
                "Projeto CEUMA",
                "Nenhum relatório disponível para exportação."
            )
            return

        relatorio = self.relatorio_atual
        codigo = relatorio["codigo"]
        destino = filedialog.asksaveasfilename(
            title="Salvar relatório de conferência",
            defaultextension=".csv",
            initialfile=f"relatorio_{codigo}.csv",
            filetypes=[("Arquivo CSV", "*.csv")],
            confirmoverwrite=False,
        )
        if not destino:
            return
        if self._verificar_prazo_relatorio() or self.relatorio_atual is not relatorio:
            messagebox.showwarning("Projeto CEUMA", "O relatório não está mais disponível para exportação.")
            return

        try:
            destino_confirmado = identificar_destino_csv(destino)
        except (OSError, ValueError):
            messagebox.showerror("Erro na exportação", "Selecione um destino CSV válido e acessível.")
            return
        if destino_confirmado is not None:
            if not messagebox.askyesno(
                "Confirmar substituição",
                "O arquivo selecionado já existe. Deseja substituí-lo?",
                default="no",
                parent=self.root,
            ):
                return
            if self._verificar_prazo_relatorio() or self.relatorio_atual is not relatorio:
                messagebox.showwarning("Projeto CEUMA", "O relatório não está mais disponível para exportação.")
                return

        self._exportando = True
        self._atualizar_acao_releitura()
        self.botao_exportar.config(state="disabled")
        Thread(
            target=self._executar_exportacao,
            args=(relatorio, destino, destino_confirmado),
            daemon=True,
        ).start()

    def _executar_exportacao(self, relatorio, destino, destino_confirmado):
        from ceuma.relatorios.relatorio import RelatorioIndisponivel

        codigo = relatorio["codigo"]
        try:
            from ceuma.banco.conexao import conectar_banco, consultar_expiracao_ativa
            from ceuma.relatorios.exportador_csv import exportar_relatorio_csv

            with self._trava_banco, closing(conectar_banco()) as conexao:
                if consultar_expiracao_ativa(conexao, codigo) is None:
                    raise RelatorioIndisponivel("O relatório não está mais disponível.")

                exportar_relatorio_csv(
                    relatorio,
                    destino,
                    destino_confirmado=destino_confirmado,
                    conexao=conexao,
                )
        except RelatorioIndisponivel:
            self.fila.put(("exportacao", (codigo, "indisponivel", None)))
        except FileExistsError:
            self.fila.put(("exportacao", (codigo, "erro", "O destino mudou. Selecione-o novamente.")))
        except PermissionError:
            self.fila.put(("exportacao", (codigo, "erro", "Verifique a permissão de gravação e se o arquivo está aberto.")))
        except Exception:
            self.fila.put(("exportacao", (codigo, "erro", "Não foi possível publicar o relatório CSV.")))
        else:
            self.fila.put(("exportacao", (codigo, "sucesso", None)))

    def _finalizar_exportacao(self, codigo, resultado, mensagem):
        self._exportando = False
        self._atualizar_acao_releitura()
        if resultado == "indisponivel":
            self._invalidar_relatorio(codigo)
        self._verificar_prazo_relatorio()
        if self.relatorio_atual is not None and not self.processando:
            self.botao_exportar.config(state="normal")
        if self._fechando:
            return
        if resultado == "indisponivel":
            messagebox.showwarning("Projeto CEUMA", "O relatório não está mais disponível para exportação.")
            return
        if resultado == "erro":
            self.registrar_log(f"Erro ao exportar relatório: {mensagem}")
            messagebox.showerror("Erro na exportação", mensagem)
            return

        self.registrar_log("Relatório CSV exportado com sucesso.")
        messagebox.showinfo(
            "Projeto CEUMA", "Relatório exportado com sucesso!"
        )

    def _liberar_interface(self):
        self.processando = False
        self.botao_pasta.config(state="normal")
        self.botao_recuperar.config(state="normal")
        self.botao_processar.config(
            state="normal" if self.pasta_selecionada or self.arquivos_selecionados else "disabled"
        )
        if hasattr(self, "modo"):
            self.modo.config(state="readonly")

    def _encerrar_releitura_com_falha(self, mensagem):
        self._releitura_origem = None
        self._liberar_interface()
        self._verificar_prazo_relatorio()
        if self.relatorio_atual is not None:
            self.botao_exportar.config(state="normal")
            mensagem += " A análise anterior continua em exibição."
        self._atualizar_acao_releitura()
        self.status.config(text="Status: releitura avançada sem resultado disponível")
        self.registrar_log(mensagem)
        if not self._fechando:
            messagebox.showerror("OCR avançado", mensagem)

    def _finalizar_relatorio_indisponivel(self, mensagem):
        if self._releitura_origem:
            self._encerrar_releitura_com_falha(mensagem)
            return
        self._descartar_relatorio()
        self._liberar_interface()
        self.codigo_recuperacao = None
        self.status.config(text="Status: relatório indisponível")
        self.registrar_log(mensagem)
        if not self._fechando:
            messagebox.showwarning("Relatório indisponível", mensagem)

    def finalizar_erro(self, mensagem, etapa, codigo=None):
        if self._releitura_origem:
            if etapa == "apresentacao" and codigo:
                mensagem += f" Use Recuperar relatório para consultar a nova análise {codigo}."
            self._encerrar_releitura_com_falha(mensagem)
            return
        self._descartar_relatorio()
        self._liberar_interface()
        titulo = {
            "processamento": "Erro de processamento",
            "persistencia": "Erro de persistência",
            "apresentacao": "Erro de apresentação",
        }[etapa]
        if etapa == "apresentacao":
            self.codigo_recuperacao = codigo
            if codigo:
                mensagem += f"\n\nUse Recuperar relatório para tentar novamente com o código {codigo}."
            else:
                mensagem += "\n\nUse Recuperar relatório para consultar análises concluídas e ainda disponíveis."
        self.status.config(text=f"Status: {titulo.lower()}")
        self.registrar_log(f"{titulo}: {mensagem}")
        if not self._fechando:
            messagebox.showerror(titulo, mensagem)


def executar():
    root = tk.Tk()
    JanelaPrincipal(root)
    root.mainloop()


if __name__ == "__main__":
    executar()
