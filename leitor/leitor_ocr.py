import os
import math
from pathlib import Path

import numpy as np

os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

_MODELO_DETECCAO = "PP-OCRv6_medium_det"
_MODELO_RECONHECIMENTO = "PP-OCRv6_medium_rec"
_MODELO_ORIENTACAO = "PP-LCNet_x1_0_doc_ori"

def _modelo_local(nome):
    cache = Path(os.environ.get("PADDLE_PDX_CACHE_HOME") or Path.home() / ".paddlex")
    pasta = cache / "official_models" / nome
    if not all((pasta / arquivo).is_file() for arquivo in (
        "inference.json", "inference.pdiparams", "inference.yml",
    )):
        raise FileNotFoundError(f"O modelo OCR local {nome} está ausente ou incompleto.")
    return str(pasta)


def modelos_reconhecimento_locais():
    return _modelo_local(_MODELO_DETECCAO), _modelo_local(_MODELO_RECONHECIMENTO)


class LeitorOCR:
    def __init__(self):
        self.ocr = None
        self.orientador = None
        self._orientacao_indisponivel = False

    def classificar_orientacao(self, imagem):
        """Retorna até duas correções anti-horárias e os escores reais do modelo."""
        if self._orientacao_indisponivel:
            return None
        if self.orientador is None:
            try:
                pasta = _modelo_local(_MODELO_ORIENTACAO)
            except FileNotFoundError:
                self._orientacao_indisponivel = True
                return None
            from paddleocr import DocImgOrientationClassification

            self.orientador = DocImgOrientationClassification(
                model_name=_MODELO_ORIENTACAO, model_dir=pasta, topk=2,
                device="cpu", cpu_threads=2, enable_mkldnn=False,
            )

        passo = max(1, math.ceil(max(imagem.shape[:2]) / 640))
        previa_bgr = np.ascontiguousarray(imagem[::passo, ::passo, ::-1])
        resultados = iter(self.orientador.predict(previa_bgr, batch_size=1))
        resultado = next(resultados, None)
        if resultado is None:
            return []
        return [
            (int(angulo), float(escore))
            for angulo, escore in zip(resultado["label_names"], resultado["scores"])
        ]

    def inicializar(self):
        if self.ocr is None:
            detector, reconhecedor = modelos_reconhecimento_locais()
            from paddleocr import PaddleOCR

            print("Inicializando OCR...")
            self.ocr = PaddleOCR(
                text_detection_model_name=_MODELO_DETECCAO,
                text_detection_model_dir=detector,
                text_recognition_model_name=_MODELO_RECONHECIMENTO,
                text_recognition_model_dir=reconhecedor,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
                cpu_threads=2,
            )
            print("OCR inicializado!")

    def reconhecer(self, imagem):
        self.inicializar()
        resultados = self.ocr.predict(imagem)
        linhas = []

        for resultado in resultados:
            dados = resultado.json.get("res", {})
            textos = dados.get("rec_texts", [])
            confiancas = dados.get("rec_scores", [])
            poligonos = dados.get("rec_polys", [])
            caixas = dados.get("rec_boxes", [])

            for indice, texto in enumerate(textos):
                confianca = (
                    float(confiancas[indice])
                    if indice < len(confiancas) and confiancas[indice] is not None
                    else None
                )

                # Coordenadas em pixels da imagem renderizada para OCR.
                # O formato é convertido para listas comuns (serializáveis).
                poligono = None
                if indice < len(poligonos) and poligonos[indice] is not None:
                    poligono = [
                        [float(coordenada) for coordenada in ponto]
                        for ponto in poligonos[indice]
                    ]

                caixa = None
                if indice < len(caixas) and caixas[indice] is not None:
                    caixa = [float(coordenada) for coordenada in caixas[indice]]
                elif poligono:
                    xs = [ponto[0] for ponto in poligono]
                    ys = [ponto[1] for ponto in poligono]
                    caixa = [min(xs), min(ys), max(xs), max(ys)]

                linhas.append({
                    "texto": texto,
                    "confianca": confianca,
                    "poligono": poligono,
                    "caixa": caixa,
                })

        return {
            "texto": "\n".join(linha["texto"] for linha in linhas),
            "linhas": linhas,
        }


def _executar_ocr_local(canal):
    import sys

    def bloquear_rede(evento, args):
        if evento in {"socket.connect", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname"}:
            raise RuntimeError("O OCR avançado usa apenas recursos locais.")

    sys.addaudithook(bloquear_rede)
    leitor = LeitorOCR()
    with canal:
        while True:
            try:
                operacao, imagem = canal.recv()
            except EOFError:
                return
            try:
                if operacao == "orientacao":
                    resultado = leitor.classificar_orientacao(imagem)
                else:
                    resultado = leitor.reconhecer(imagem)
                canal.send((True, resultado))
            except Exception:
                canal.send((False, None))
                return


class LeitorOCRIsolado:
    """Usa o leitor local em outro processo para isolar a execução nativa do Tkinter."""

    def __init__(self):
        from multiprocessing import get_context

        contexto = get_context("spawn")
        self._canal, filho = contexto.Pipe()
        self._processo = contexto.Process(target=_executar_ocr_local, args=(filho,), daemon=True)
        try:
            self._processo.start()
        except BaseException:
            self._canal.close()
            raise
        finally:
            filho.close()

    def _solicitar(self, operacao, imagem):
        self._canal.send((operacao, imagem))
        if not self._canal.poll(240):
            raise TimeoutError("O OCR avançado excedeu o tempo de espera.")
        sucesso, resultado = self._canal.recv()
        if not sucesso:
            raise RuntimeError("O OCR avançado não concluiu a leitura local.")
        return resultado

    def classificar_orientacao(self, imagem):
        return self._solicitar("orientacao", imagem)

    def reconhecer(self, imagem):
        return self._solicitar("reconhecimento", imagem)

    def fechar(self):
        self._canal.close()
        self._processo.join(timeout=1)
        if self._processo.is_alive():
            self._processo.terminate()
            self._processo.join()
        self._processo.close()
