"""Teste integrado do leitor OCR, sem exibir dados pessoais."""

from pathlib import Path

from ceuma.leitura.pdf import ler_pdf


def testar():
    print("=== TESTE DO MODULO OCR CEUMA ===")

    caminho = Path("pdf/jpg2pdf.pdf")
    if not caminho.is_file():
        raise FileNotFoundError(f"PDF de teste nao encontrado: {caminho}")

    documento = ler_pdf(str(caminho), usar_ocr=True)
    paginas = documento["paginas"]
    assert paginas, "O PDF nao possui paginas."

    total_linhas = 0
    total_caixas = 0
    total_confiancas = 0

    for pagina in paginas:
        print(f"\n--- PAGINA {pagina['numero']} ---")
        print("Metodo:", pagina["metodo"])
        print("Linhas OCR:", len(pagina["linhas_ocr"]))
        print("Caracteres:", len(pagina["texto"]))

        if pagina["metodo"] != "OCR":
            print("Pagina nao processada por OCR; sem verificacao de coordenadas.")
            continue

        linhas = pagina["linhas_ocr"]
        assert isinstance(linhas, list), "linhas_ocr deve ser uma lista."
        assert linhas, "OCR nao retornou linhas nesta pagina."

        for linha in linhas:
            assert isinstance(linha, dict), "Cada linha OCR deve ser um dicionario."
            assert isinstance(linha.get("texto"), str), "Texto da linha ausente."
            assert "confianca" in linha, "Campo confianca ausente."
            assert "caixa" in linha, "Campo caixa ausente."
            assert "poligono" in linha, "Campo poligono ausente."

            if linha["confianca"] is not None:
                assert 0 <= linha["confianca"] <= 1, "Confianca fora do intervalo 0 a 1."
                total_confiancas += 1

            if linha["caixa"] is not None:
                caixa = linha["caixa"]
                assert len(caixa) == 4, "Caixa deve conter 4 coordenadas."
                assert all(isinstance(valor, (int, float)) for valor in caixa)
                assert caixa[0] <= caixa[2] and caixa[1] <= caixa[3]
                total_caixas += 1

            if linha["poligono"] is not None:
                assert len(linha["poligono"]) >= 4, "Poligono incompleto."

        total_linhas += len(linhas)
        print("Caixas encontradas:", sum(linha["caixa"] is not None for linha in linhas))
        print("Confiancas encontradas:", sum(linha["confianca"] is not None for linha in linhas))
        print("Texto reconhecido com sucesso!" if pagina["texto"].strip() else "Nenhum texto reconhecido.")

    assert total_linhas > 0, "Nenhuma linha OCR foi reconhecida."
    assert total_caixas > 0, "OCR nao retornou coordenadas de caixas."
    assert total_confiancas > 0, "OCR nao retornou confiancas."
    print("\nTeste concluido! Coordenadas e confiancas verificadas.")


if __name__ == "__main__":
    testar()
