"""Abre a demonstração com PDFs fictícios e banco SQLite temporário."""

from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import Tk

import pymupdf as fitz

from ceuma.banco import conexao
from ceuma.interface.janela_principal import JanelaPrincipal


def gerar_documentos(pasta, *, raster=False):
    pasta.mkdir(parents=True, exist_ok=True)
    casos = [
        ("01_rg", [
            "CARTEIRA DE IDENTIDADE", "REGISTRO GERAL: 12.345.678-9",
            "NOME: PESSOA FICTICIA ALFA", "DATA DE NASCIMENTO: 12/04/2000",
            "DEMONSTRACAO - DOCUMENTO SEM VALIDADE",
        ]),
        ("02_casamento", [
            "CERTIDAO DE CASAMENTO", "REGISTRO CIVIL DAS PESSOAS NATURAIS",
            "PRIMEIRO CONJUGE: PESSOA FICTICIA ALFA",
            "SEGUNDO CONJUGE: PESSOA FICTICIA BETA",
            "DATA DO CASAMENTO: 15/06/2020",
            "DEMONSTRACAO - DOCUMENTO SEM VALIDADE",
        ]),
        ("03_inconclusivo", [
            "CERTIDAO DE CASAMENTO", "AMOSTRA FICTICIA SEM INFORMACOES DO REGISTRO",
            "DEMONSTRACAO - DOCUMENTO SEM VALIDADE",
        ]),
    ]
    caminhos = []
    for nome, linhas in casos:
        caminho = pasta / f"{nome}.pdf"
        with fitz.open() as pdf:
            pagina = pdf.new_page(width=520, height=225)
            for linha, texto in enumerate(linhas):
                pagina.insert_text((25, 32 + 26 * linha), texto, fontsize=14)
            if raster and nome == "01_rg":
                pixmap = pagina.get_pixmap(matrix=fitz.Matrix(2, 2))
                pdf.delete_page(0)
                pdf.new_page(width=520, height=225).insert_image(fitz.Rect(0, 0, 520, 225), pixmap=pixmap)
            pdf.save(caminho)
        caminhos.append(caminho)
    return caminhos


def executar():
    with TemporaryDirectory(prefix="ceuma_demo_") as temporario:
        pasta = Path(temporario)
        gerar_documentos(pasta, raster=True)
        conexao.PASTA_DADOS = pasta
        conexao.CAMINHO_BANCO = pasta / "demo.sqlite"
        root = Tk()
        JanelaPrincipal(root, pasta_inicial=pasta, demonstracao=True)
        root.mainloop()


if __name__ == "__main__":
    executar()
