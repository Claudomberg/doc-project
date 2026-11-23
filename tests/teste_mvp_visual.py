import csv
import gc
import os
import tempfile
import time
import tkinter as tk
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pymupdf

from ceuma.banco import atribuicoes
from ceuma.banco import conexao
from ceuma.demo import gerar_documentos
from ceuma.interface import janela_principal as gui
from ceuma.leitura.ocr.adaptativo import LeitorAdaptativo
from ceuma.leitura.ocr.tesseract import Tesseract
from ceuma.leitura.ocr.paddle import LeitorOCR


class TesteMVPVisual(unittest.TestCase):
    def setUp(self):
        pasta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.pasta = pasta
        self.pdfs = gerar_documentos(pasta)
        self.banco = pasta / "teste.sqlite"
        self.enterContext(patch.multiple(conexao, PASTA_DADOS=pasta, CAMINHO_BANCO=self.banco))
        self.enterContext(patch.dict(os.environ, {
            "CEUMA_OCR_MODO": "adaptativo", "CEUMA_OCR_ORIENTACAO": "pdf",
            "CEUMA_OCR_PREAQUECER": "0", "CEUMA_OCR_FALLBACK_PADDLE": "0",
        }))
        self.pesado = self.enterContext(patch.object(LeitorOCR, "inicializar", side_effect=AssertionError))
        self.avisos = self.enterContext(patch.object(gui.messagebox, "showerror"))
        self.enterContext(patch.object(gui.messagebox, "showinfo"))
        inicio = time.perf_counter()
        self.root = tk.Tk()
        self.addCleanup(self.encerrar)
        self.janela = gui.JanelaPrincipal(self.root)
        self.root.update()
        self.abertura = time.perf_counter() - inicio
        self.root.withdraw()
        self.esperar(lambda: not self.janela._limpando)

    def encerrar(self):
        try:
            self.janela._solicitar_fechamento()
            self.esperar(lambda: not self.janela.processando and not self.janela._exportando)
        except tk.TclError:
            pass
        finally:
            self.janela = None
            self.root = None
            gc.collect()

    def esperar(self, pronto):
        fim = time.monotonic() + 20
        while not pronto() and time.monotonic() < fim:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(pronto())

    def selecionar(self, *pdfs):
        with patch.object(gui.filedialog, "askopenfilenames", return_value=tuple(map(str, pdfs))):
            self.janela.selecionar_pdfs()

    def processar(self, *pdfs):
        self.selecionar(*pdfs)
        self.janela.iniciar_analise()
        self.esperar(lambda: not self.janela.processando)
        self.avisos.assert_not_called()
        return self.janela.relatorio_atual

    def test_inicializacao_selecao_e_cancelamento(self):
        self.assertEqual(str(self.janela.botao_processar["state"]), "disabled")
        self.selecionar(self.pdfs[0])
        self.assertEqual(len(self.janela.arvore_documentos.get_children()), 1)
        self.selecionar()
        self.selecionar(self.pasta / "inexistente.txt")
        self.assertEqual(self.janela.arquivos_selecionados, (self.pdfs[0],))
        self.selecionar(*self.pdfs)
        self.assertEqual(len(self.janela.arvore_documentos.get_children()), 3)
        self.pesado.assert_not_called()

    def test_individual_integrado_campos_classificacao_recuperacao_csv(self):
        originais = [pdf.read_bytes() for pdf in self.pdfs]
        rel = self.processar(*self.pdfs)
        self.assertEqual(rel["modo"], "INDIVIDUAL")
        self.assertEqual(str(self.janela.botao_ocr_avancado["state"]), "disabled")
        self.assertEqual(rel["documentos"][1]["tipo"], "CERTIDAO_CASAMENTO")
        self.assertNotEqual(rel["documentos"][2]["tipo"], "CERTIDAO_CASAMENTO")
        tree = self.janela.arvore_documentos
        ident = str(rel["documentos"][1]["id"])
        self.assertIn("casamento", tree.item(ident, "values")[1])
        tree.selection_set(ident)
        self.root.update()
        campos = self.janela.arvore_campos
        linhas = [campos.item(item, "values") for item in campos.get_children()]
        self.assertTrue(any("PESSOA FICTICIA" in row[1].upper() and row[2] == "1" for row in linhas))
        self.assertTrue(any(row[3] == "Não encontrado" for row in linhas))
        with patch.object(gui.simpledialog, "askstring", return_value=rel["codigo"]):
            self.janela.recuperar_relatorio()
        self.esperar(lambda: not self.janela.processando)
        self.assertEqual(self.janela.relatorio_atual["codigo"], rel["codigo"])
        destino = self.pasta / "relatorio.csv"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(destino)):
            self.janela.exportar_relatorio()
        self.esperar(lambda: not self.janela._exportando)
        self.assertIn("CERTIDAO_CASAMENTO", destino.read_text(encoding="utf-8-sig"))
        self.assertEqual(originais, [pdf.read_bytes() for pdf in self.pdfs])
        with closing(conexao.conectar_banco()) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.pesado.assert_not_called()

    def test_familiar_preserva_atribuicao_e_certidao_pendente(self):
        self.janela.modo.current(1)
        rel = self.processar(*self.pdfs[:2])
        self.assertEqual(rel["modo"], "FAMILIAR")
        rg, certidao = [doc["id"] for doc in rel["documentos"]]
        self.assertIn(certidao, rel["documentos_inconclusivos"])
        self.assertEqual(rel["total_pares_elegiveis"], 0)
        with closing(conexao.conectar_banco()) as db:
            analise = db.execute("SELECT id FROM analises WHERE codigo=?", (rel["codigo"],)).fetchone()[0]
            pessoa = atribuicoes.criar_pessoa(db, analise, "ESTUDANTE_TITULAR")
            atribuicoes.atribuir_documento(db, rg, pessoa)
        with patch.object(gui.simpledialog, "askstring", return_value=rel["codigo"]):
            self.janela.recuperar_relatorio()
        self.esperar(lambda: not self.janela.processando)
        self.assertIn(f"Pessoa #{pessoa}", self.janela.atribuicao["text"])

    def test_pdf_invalido_com_erro_controlado(self):
        pdf = self.pasta / "invalido.pdf"
        pdf.write_bytes(b"fixture invalida")
        self.selecionar(pdf)
        self.janela.iniciar_analise()
        self.esperar(lambda: not self.janela.processando)
        self.avisos.assert_called_once()
        self.assertIn("#1", self.avisos.call_args.args[1])
        self.assertNotIn("invalido.pdf", self.avisos.call_args.args[1])
        self.assertIsNone(self.janela.relatorio_atual)

    def test_sem_tesseract_indica_revisao_sem_paddle_pesado(self):
        pdf, *_ = gerar_documentos(self.pasta / "raster", raster=True)
        indisponivel = self.pasta / "inexistente.exe"
        with patch.object(Tesseract, "__init__", lambda motor: setattr(motor, "executavel", indisponivel)):
            rel = self.processar(pdf)
        self.assertTrue(rel["leituras_pendentes"])
        doc, = self.janela.arvore_documentos.get_children()
        self.assertEqual(self.janela.arvore_documentos.item(doc, "values")[2], "Revisar leitura")
        self.pesado.assert_not_called()

    def test_progresso_responsividade_e_fechamento_durante_ocr(self):
        from threading import Event

        entrou, liberar = Event(), Event()
        reconhecer = LeitorAdaptativo.reconhecer

        def pausar(motor, imagem):
            entrou.set()
            liberar.wait(5)
            return reconhecer(motor, imagem)

        pdf, *_ = gerar_documentos(self.pasta / "raster", raster=True)
        self.selecionar(pdf)
        with patch.object(LeitorAdaptativo, "reconhecer", pausar):
            self.janela.iniciar_analise()
            try:
                self.esperar(lambda: entrou.is_set() and "1 de 1" in self.janela.status["text"])
                self.assertEqual(str(self.janela.botao_processar["state"]), "disabled")
                self.janela._solicitar_fechamento()
                self.assertTrue(self.janela.processando)
                self.assertIn("Aguardando", self.janela.status["text"])
            finally:
                liberar.set()
            self.esperar(lambda: not self.janela.processando)
        with closing(conexao.conectar_banco()) as db:
            self.assertEqual(db.execute("SELECT status FROM analises").fetchone()[0], "CONCLUIDA")
        self.pesado.assert_not_called()


    def _pdf_identidade(self, nome, linhas, *, formato="digital"):
        caminho = self.pasta / f"{nome}.pdf"
        with pymupdf.open() as pdf:
            pagina = pdf.new_page(width=520, height=250)
            for indice, linha in enumerate(linhas):
                pagina.insert_text((25, 32 + 26 * indice), linha, fontsize=15)
            if formato != "digital":
                pixmap = pagina.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                pdf.delete_page(0)
                pagina = pdf.new_page(width=520, height=320)
                pagina.insert_image(pymupdf.Rect(0, 0, 520, 250), pixmap=pixmap)
                if formato == "hibrido":
                    pagina.insert_text((25, 295), "RODAPE DIGITAL FICTICIO")
            pdf.save(caminho)
        return caminho

    def test_identidade_digital_raster_hibrida_recuperacao_csv_e_tabela(self):
        from ceuma.leitura.ocr.tesseract import Tesseract

        if not Tesseract().executavel.is_file():
            self.skipTest("Tesseract local indisponível")
        linhas = (
            "VALIDA EM TODO O TERRITORIO NACIONAL", "REGISTRO GERAL: 12.345.678-9",
            "NOME: PESSOA FICTICIA ALFA", "DATA DE NASCIMENTO: 12/04/2000",
            "CPF DO TITULAR: 00000000191", "DATA DE EXPEDICAO: 13/05/2020",
        )
        pdfs = [
            self._pdf_identidade(formato, linhas, formato=formato)
            for formato in ("digital", "raster", "hibrido")
        ]
        originais = [p.read_bytes() for p in pdfs]
        rel = self.processar(*pdfs)
        self.assertEqual([d["tipo"] for d in rel["documentos"]], ["DOCUMENTO_IDENTIDADE"] * 3)
        with patch.object(gui.simpledialog, "askstring", return_value=rel["codigo"]):
            self.janela.recuperar_relatorio()
        self.esperar(lambda: not self.janela.processando)
        self.assertEqual(self.janela.relatorio_atual["campos_extraidos"], rel["campos_extraidos"])
        for doc in rel["documentos"]:
            campos = {c["campo"]: c for c in rel["campos_extraidos"] if c["documento_id"] == doc["id"]}
            self.assertTrue({"RG", "NOME", "DATA_NASCIMENTO", "CPF", "DATA_EXPEDICAO"} <= campos.keys(), campos.keys())
            self.assertEqual(campos["RG"]["valor"], "12.345.678-9")
            self.assertEqual(campos["RG"]["status"], "ENCONTRADO")
            self.assertEqual(campos["RG"]["pagina"], 1)
            self.janela.arvore_documentos.selection_set(str(doc["id"]))
            self.root.update()
            arvore = self.janela.arvore_campos
            linhas_tabela = [arvore.item(ident, "values") for ident in arvore.get_children()]
            self.assertIn(("RG", "12.345.678-9", "1", "Encontrado"), linhas_tabela)
        destino = self.pasta / "identidade.csv"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(destino)):
            self.janela.exportar_relatorio()
        self.esperar(lambda: not self.janela._exportando)
        with destino.open(encoding="utf-8-sig", newline="") as csv_arquivo:
            exportadas = list(csv.reader(csv_arquivo, delimiter=";"))
        self.assertIn(["CAMPOS EXTRAIDOS"], exportadas)
        for campo in rel["campos_extraidos"]:
            self.assertTrue(any(linha[:5] == [
                campo["arquivo"], campo["campo"], campo["valor"] or "",
                str(campo["pagina"] or ""), campo["status"],
            ] for linha in exportadas), campo["campo"])
        self.assertEqual(originais, [p.read_bytes() for p in pdfs])
        self.pesado.assert_not_called()

    def test_identidade_ocr_imperfeito_desconhecido_e_sem_texto(self):
        identidade = self._pdf_identidade("titulo-ocr", (
            "CARTE1RA DE LDENTIDADE", "REGISTRO GERAL: 12.345.678-9",
            "NOME: PESSOA FICTICIA ALFA",
        ))
        parcial = self._pdf_identidade("parcial", ("NOME: PESSOA FICTICIA BETA", "CPF: 00000000191"))
        desconhecido = self._pdf_identidade("cpf-nao-e-rg", (
            "VALIDA EM TODO O TERRITORIO NACIONAL", "REGISTRO GERAL: 00000000191",
            "NOME: PESSOA FICTICIA GAMA",
        ))
        vazio = self._pdf_identidade("sem-texto", ())
        rel = self.processar(identidade, parcial, desconhecido, vazio)
        docs = rel["documentos"]
        self.assertEqual(docs[0]["tipo"], "DOCUMENTO_IDENTIDADE")
        self.assertTrue(all(d["tipo"] == "DOCUMENTO_DESCONHECIDO" for d in docs[1:]))
        self.assertIn(docs[-1]["id"], rel["leituras_pendentes"])
        self.assertTrue(any(c["documento_id"] == docs[0]["id"] and c["campo"] == "DATA_NASCIMENTO"
                            and c["status"] == "NAO_ENCONTRADO" for c in rel["estados_campos"]))
        self.janela.arvore_documentos.selection_set(str(docs[1]["id"]))
        self.root.update()
        arvore = self.janela.arvore_campos
        self.assertTrue(any(arvore.item(i, "values")[0] == "Nome" for i in arvore.get_children()))
        self.assertFalse(any(c["documento_id"] == docs[2]["id"] and c["campo"] == "RG"
                             for c in rel["campos_extraidos"]))
        self.janela.arvore_documentos.selection_set(str(docs[-1]["id"]))
        self.root.update()
        self.assertFalse(arvore.get_children())
        self.assertIn("Confira a leitura", self.janela.aviso_campos["text"])


    def _lote_com_ocr_insuficiente(self):
        # Os cenários de falha injetam respostas do OCR na fronteira do leitor.
        self.enterContext(patch("ceuma.processamento.lote.LeitorOCRIsolado", spec=True, side_effect=self._leitor_local))
        pdf, *_ = gerar_documentos(self.pasta / "releitura", raster=True)
        with patch.object(Tesseract, "reconhecer", return_value=[]):
            relatorio = self.processar(pdf)
        self.assertEqual(relatorio["documentos"][0]["tipo"], "DOCUMENTO_DESCONHECIDO")
        self.assertEqual(str(self.janela.botao_ocr_avancado["state"]), "normal")
        self.pesado.assert_not_called()
        return pdf, relatorio

    @staticmethod
    def _leitor_local():
        from unittest.mock import Mock

        leitor = LeitorOCR()
        return Mock(
            classificar_orientacao=leitor.classificar_orientacao,
            reconhecer=leitor.reconhecer,
        )

    def _confirmar_releitura(self, confirmar=True):
        with patch.object(gui.messagebox, "askyesno", return_value=confirmar) as dialogo:
            self.janela.reprocessar_com_ocr_avancado()
        dialogo.assert_called_once()
        self.assertEqual(dialogo.call_args.kwargs["default"], "no")

    def _ocr_simulado(self):
        return {"linhas": [
            {"texto": texto, "caixa": [18, 24 + i * 30, 460, 46 + i * 30], "confianca": .98}
            for i, texto in enumerate((
                "CARTEIRA DE IDENTIDADE", "REGISTRO GERAL: 12.345.678-9",
                "NOME: PESSOA FICTICIA ALFA", "DATA DE NASCIMENTO: 12/04/2000",
            ))
        ]}

    def test_releitura_cancelada_nao_cria_analise(self):
        _, anterior = self._lote_com_ocr_insuficiente()
        self._confirmar_releitura(False)
        self.assertIs(self.janela.relatorio_atual, anterior)
        self.assertFalse(self.janela.processando)
        with closing(conexao.conectar_banco()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM analises").fetchone()[0], 1)

    def test_releitura_modelos_ausentes_preserva_analise(self):
        _, anterior = self._lote_com_ocr_insuficiente()
        with patch("ceuma.leitura.ocr.paddle.modelos_reconhecimento_locais", side_effect=FileNotFoundError):
            self._confirmar_releitura()
            self.esperar(lambda: not self.janela.processando)
        self.assertIn("modelos locais", self.avisos.call_args.args[1])
        self.assertIs(self.janela.relatorio_atual, anterior)
        self.pesado.assert_not_called()

    def test_releitura_falha_ou_sem_resultado_reverte_apenas_a_tentativa(self):
        pdf, anterior = self._lote_com_ocr_insuficiente()
        for saida in (RuntimeError("falha ficticia"), {"linhas": []}):
            with (
                self.subTest(tipo=type(saida).__name__),
                patch("ceuma.leitura.ocr.paddle.modelos_reconhecimento_locais"),
                patch.object(LeitorOCR, "classificar_orientacao", return_value=[(0, .95)]),
                patch.object(LeitorOCR, "reconhecer", side_effect=[saida]),
            ):
                self._confirmar_releitura()
                self.esperar(lambda: not self.janela.processando)
            self.assertIs(self.janela.relatorio_atual, anterior)
            self.assertEqual(str(self.janela.botao_exportar["state"]), "normal")
            with closing(conexao.conectar_banco()) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM analises").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM documentos").fetchone()[0], 1)
        pdf.write_bytes(pdf.read_bytes() + b"modificacao ficticia")
        with patch("ceuma.leitura.ocr.paddle.modelos_reconhecimento_locais"), patch.object(LeitorOCR, "reconhecer") as ocr:
            self._confirmar_releitura()
            self.esperar(lambda: not self.janela.processando)
        ocr.assert_not_called()
        self.assertIn("PDF original mudou", self.avisos.call_args.args[1])

    def test_releitura_publica_relatorio_separado_e_csv(self):
        pdf, anterior = self._lote_com_ocr_insuficiente()
        original = pdf.read_bytes()
        with (
            patch("ceuma.leitura.ocr.paddle.modelos_reconhecimento_locais"),
            patch.object(LeitorOCR, "classificar_orientacao", return_value=[(0, .95)]),
            patch.object(LeitorOCR, "reconhecer", return_value=self._ocr_simulado()) as ocr,
        ):
            self._confirmar_releitura()
            self.assertIs(self.janela.relatorio_atual, anterior)
            self.esperar(lambda: not self.janela.processando)
        ocr.assert_called_once()
        self.avisos.assert_not_called()
        novo = self.janela.relatorio_atual
        self.assertNotEqual(novo["codigo"], anterior["codigo"])
        self.assertEqual(novo["total_documentos"], 1)
        self.assertEqual(novo["documentos"][0]["tipo"], "DOCUMENTO_IDENTIDADE")
        arvore = self.janela.arvore_campos
        self.assertTrue(any(arvore.item(i, "values") == ("RG", "12.345.678-9", "1", "Encontrado")
                            for i in arvore.get_children()))
        destino = self.pasta / "releitura.csv"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(destino)):
            self.janela.exportar_relatorio()
        self.esperar(lambda: not self.janela._exportando)
        self.assertIn("12.345.678-9", destino.read_text(encoding="utf-8-sig"))
        for relatorio in (anterior, novo):
            with patch.object(gui.simpledialog, "askstring", return_value=relatorio["codigo"]):
                self.janela.recuperar_relatorio()
            self.esperar(lambda: not self.janela.processando)
            self.assertEqual(self.janela.relatorio_atual["campos_extraidos"], relatorio["campos_extraidos"])
        with closing(conexao.conectar_banco()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM analises WHERE status='CONCLUIDA'").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(pdf.read_bytes(), original)

    def test_releitura_responsiva_e_fechamento_aguarda_commit(self):
        from threading import Event

        _, anterior = self._lote_com_ocr_insuficiente()
        entrou, liberar = Event(), Event()

        def reconhecer(imagem):
            entrou.set()
            liberar.wait(5)
            return self._ocr_simulado()

        with (
            patch("ceuma.leitura.ocr.paddle.modelos_reconhecimento_locais"),
            patch.object(LeitorOCR, "classificar_orientacao", return_value=[(0, .95)]),
            patch.object(LeitorOCR, "reconhecer", side_effect=reconhecer),
        ):
            self._confirmar_releitura()
            try:
                self.esperar(lambda: entrou.is_set() and "OCR avançado: documento" in self.janela.status["text"])
                self.assertIs(self.janela.relatorio_atual, anterior)
                self.janela._solicitar_fechamento()
                self.assertIn("Aguardando", self.janela.status["text"])
            finally:
                liberar.set()
            self.esperar(lambda: not self.janela.processando)
        with closing(conexao.conectar_banco()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM analises WHERE status='CONCLUIDA'").fetchone()[0], 2)


    def test_cin_rasterizada_e_digital_com_valor_completo(self):
        if not Tesseract().executavel.is_file():
            self.skipTest("Tesseract local indisponível")
        nome = "PESSOA FICTICIA ALFA DE EXEMPLO"
        linhas = (
            "CARTEIRA DE IDENTIDADE NACIONAL", f"NOME / NAME: {nome}",
            "CPF / PERSONAL NUMBER: 000.000.001-91",
            "NASCIMENTO / DATE OF BIRTH: 14/05/2001",
            "VALIDADE / EXPIRY DATE: 17/04/2045",
        )
        arquivos = [self._pdf_identidade(f"cin-{formato}", linhas, formato=formato)
                    for formato in ("digital", "raster")]
        relatorio = self.processar(*arquivos)
        self.assertEqual([d["tipo"] for d in relatorio["documentos"]], ["CIN", "CIN"])
        for doc in relatorio["documentos"]:
            self.janela.arvore_documentos.selection_set(str(doc["id"]))
            self.root.update()
            campos = self.janela.arvore_campos
            ids = {campos.item(ident, "values")[0]: ident for ident in campos.get_children()}
            self.assertEqual(campos.item(ids["RG"], "values")[-1], "Não aplicável")
            self.assertEqual(campos.item(ids["Data expedicao"], "values")[-1], "Não encontrado")
            self.assertEqual(campos.item(ids["CPF"], "values")[1], "00000000191")
            self.assertEqual(campos.item(ids["Data validade"], "values")[1], "17/04/2045")
            campos.selection_set(ids["Nome"])
            self.root.update()
            self.assertEqual(self.janela.valor_campo.get(), nome)
        destino = self.pasta / "cin.csv"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(destino)):
            self.janela.exportar_relatorio()
        self.esperar(lambda: not self.janela._exportando)
        with destino.open(encoding="utf-8-sig", newline="") as arquivo:
            linhas_csv = list(csv.reader(arquivo, delimiter=";"))
        for doc in relatorio["documentos"]:
            self.assertTrue(any(linha[:5] == [
                doc["arquivo"], "NOME", nome, "1", "ENCONTRADO",
            ] for linha in linhas_csv))
            self.assertTrue(any(linha[:5] == [
                doc["arquivo"], "CPF", "00000000191", "1", "TITULAR_CONFIRMADO",
            ] for linha in linhas_csv))
        self.pesado.assert_not_called()
