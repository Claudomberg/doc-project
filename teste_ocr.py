import os

os.environ["FLAGS_use_mkldnn"] = "0"

from paddleocr import PaddleOCR

print("=== TESTE OCR CEUMA ===")

print("Inicializando PaddleOCR...")

ocr = PaddleOCR(
    lang="pt",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    enable_mkldnn=False,
    cpu_threads=2
)

print("PaddleOCR inicializado com sucesso!")

print("Executando teste de reconhecimento...")

resultado = ocr.predict(
    "pdf/jpg2pdf.pdf"
)

print("\n=== RESULTADO ===")

for pagina in resultado:
    dados = pagina.json
    textos = dados.get("res", {}).get("rec_texts", [])

    for texto in textos:
        print(texto)

print("\nTeste concluído!")
