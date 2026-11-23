"""Piloto offline. As caixas devolvidas usam o raster recebido pelo leitor."""
import math
import os
import re
from collections import Counter
from time import monotonic

import numpy as np

from ceuma.extracao.campos import normalizar_rotulo, validar_cpf, validar_data
from ceuma.leitura.ocr.paddle import LeitorOCR
from ceuma.leitura.ocr.tesseract import FalhaOCR, Tesseract


_ROTULO = re.compile(r"^(CPF(?: DO TITULAR| DO TRABALHADOR)?|(?:DATA DE )?(?:NASCIMENTO|ADMISSAO|DESLIGAMENTO)|REGISTRO GERAL|RG)\s*:\s*(.*)$")


def _rotulo(texto):
    return _ROTULO.fullmatch(normalizar_rotulo(texto).strip())


def _valido(rotulo, valor):
    if rotulo.startswith("CPF"):
        return bool(re.fullmatch(r"\d{3}[. ]?\d{3}[. ]?\d{3}[- ]?\d{2}", valor) and validar_cpf(valor))
    if rotulo in {"RG", "REGISTRO GERAL"}:
        # A extração de RG tem regras contextuais. O piloto não corrige seus dígitos.
        return False
    return validar_data(valor)


def _fora_das_linhas(novas, existentes):
    def sobreposta(a, b):
        largura = min(a[2], b[2]) - max(a[0], b[0])
        altura = min(a[3], b[3]) - max(a[1], b[1])
        return largura > 0 and altura > 0 and largura * altura >= 0.2 * (a[2] - a[0]) * (a[3] - a[1])

    return [
        nova for nova in novas
        if len(nova["texto"].strip()) >= 2
        and not any(sobreposta(nova["caixa"], linha["caixa"]) for linha in existentes)
    ]


class LeitorAdaptativo:
    def __init__(self, *, tesseract=None, paddle=None, orientacao="paddle", orientador=None,
                 segundos_pagina=15, segundos_documento=60, max_tentativas=6,
                 permitir_paddle=False, reserva_paddle=35, corrigir=True, leitura_esparsa=True):
        if orientacao not in {"paddle", "osd", "pdf"}:
            raise ValueError("Modo de orientação inválido.")
        self.tesseract = tesseract or Tesseract()
        self.paddle = paddle or LeitorOCR()
        self.orientacao = orientacao
        self.orientador = orientador
        self._falha_orientacao = False
        self.segundos_pagina = segundos_pagina
        self.segundos_documento = segundos_documento
        self.max_tentativas = max_tentativas
        self.permitir_paddle = permitir_paddle
        self.reserva_paddle = reserva_paddle
        self.corrigir = corrigir
        self.leitura_esparsa = leitura_esparsa
        if (max_tentativas < 1 or not all(math.isfinite(x) and x > 0 for x in
                                         (segundos_pagina, segundos_documento, reserva_paddle))):
            raise ValueError("Orçamento OCR inválido.")
        self.fim_documento = None
        self.iniciar_pagina()

    def iniciar_documento(self):
        # O leitor é compartilhado no lote; sem reiniciar, o prazo esgotado zera o OCR dos PDFs seguintes.
        self.fim_documento = None
        self.iniciar_pagina()

    def iniciar_pagina(self):
        self.fim_pagina = monotonic() + self.segundos_pagina
        self.tentativas = 0
        self.metricas = Counter()
        self.pendencias = set()
        self._previa = None

    def _restante(self):
        agora = monotonic()
        if self.fim_documento is None:
            self.fim_documento = agora + self.segundos_documento
        return min(self.fim_pagina, self.fim_documento) - agora

    def pode_rasterizar(self, minimo=0.05):
        if self._restante() < minimo:
            self.pendencias.add("ORCAMENTO_ESGOTADO")
            return False
        if self.tentativas >= self.max_tentativas:
            self.pendencias.add("LIMITE_TENTATIVAS")
            return False
        return True

    def _reservar(self):
        if not self.pode_rasterizar():
            return False
        self.tentativas += 1
        return True

    def preaquecer_orientacao(self):
        """Prepara apenas o classificador, fora do orçamento recorrente."""
        if self.orientacao == "paddle" and self.orientador is None:
            imagem = np.full((128, 128, 3), 255, dtype=np.uint8)
            self.paddle.classificar_orientacao(imagem)

    def _orientar_paddle(self, imagem):
        if self._falha_orientacao:
            self.pendencias.add("ORIENTACAO_INDISPONIVEL")
            return None
        precisa_preparar = (not self.orientador.pronta if self.orientador is not None
                            else getattr(self.paddle, "orientador", None) is None)
        try:
            if precisa_preparar:
                inicio = monotonic()
                try:
                    if self.orientador is not None:
                        self.orientador.aguardar()
                    else:
                        self.preaquecer_orientacao()
                finally:
                    espera = monotonic() - inicio
                    self.fim_pagina += espera
                    self.fim_documento += espera
                    self.metricas["segundos_preparacao_orientacao"] += espera
            inicio_inferencia = monotonic()
            try:
                if self.orientador is not None:
                    resultado = self.orientador.classificar(imagem) if self.orientador.aguardar() else None
                else:
                    resultado = self.paddle.classificar_orientacao(imagem)
            finally:
                self.metricas["segundos_inferencia_orientacao"] += monotonic() - inicio_inferencia
        except Exception:
            resultado = None
        if resultado is None:
            self._falha_orientacao = True
            self.pendencias.add("ORIENTACAO_INDISPONIVEL")
        return resultado

    @staticmethod
    def _psm(imagem):
        altura, largura = imagem.shape[:2]
        if altura <= 120 and largura >= altura * 4:
            return 7
        # psm 6 trata a área como um bloco único e mistura colunas, fotos e cartões lado a lado.
        return 3

    def classificar_orientacao(self, imagem):
        if self.orientacao == "pdf" or self._restante() < 0.1:
            return None
        if (self.orientacao == "paddle" and
                (not self.orientador.pronta if self.orientador is not None
                 else getattr(self.paddle, "orientador", None) is None)):
            # Na primeira página frontal, o texto reconhecível pode dispensar a
            # carga a frio do classificador. As mesmas linhas são reaproveitadas.
            from ceuma.documentos.identificador import identificar_documento
            linhas = self._leve(imagem, self._psm(imagem))
            self._previa = imagem, linhas
            if sum((l.get("confianca") or 0) >= 0.80 for l in linhas) >= 4:
                tipo = identificar_documento("\n".join(l["texto"] for l in linhas))
                if tipo["status"] == "IDENTIFICADO":
                    self.metricas["orientacao_textual"] += 1
                    return [(0, 0.85)]
        self.metricas["orientacao"] += 1
        try:
            if self.orientacao == "osd":
                resultado = self.tesseract.orientar(imagem, max(0.01, min(0.7, self._restante())))
                if not resultado:
                    self.pendencias.add("ORIENTACAO_INCONCLUSIVA")
                return resultado
            resultado = self._orientar_paddle(imagem)
            if self._restante() <= 0:
                self.pendencias.add("ORCAMENTO_ESGOTADO")
            return resultado
        except FalhaOCR:
            self.pendencias.add("ORIENTACAO_INCONCLUSIVA")
            return None
        except Exception:
            self.pendencias.add("ORIENTACAO_INDISPONIVEL")
            return None

    def _leve(self, imagem, psm):
        if not self._reservar():
            return []
        self.metricas["tesseract"] += 1
        inicio_tesseract = monotonic()
        try:
            linhas = self.tesseract.reconhecer(imagem, psm, max(0.01, self._restante()))
            h, w = imagem.shape[:2]
            if any(l["caixa"][2] > w or l["caixa"][3] > h for l in linhas):
                raise FalhaOCR("GEOMETRIA_TSV_INVALIDA")
            return linhas
        except FalhaOCR as erro:
            self.pendencias.add(str(erro))
            return []
        finally:
            self.metricas["segundos_tesseract"] += monotonic() - inicio_tesseract


    def _pesado(self, imagem):
        if not self.permitir_paddle:
            return []
        if self._restante() < self.reserva_paddle:
            self.pendencias.add("PADDLE_FORA_DO_ORCAMENTO")
            return []
        if not self._reservar():
            return []
        inicio = monotonic()
        self.metricas["paddle"] += 1
        try:
            resultado = self.paddle.reconhecer(imagem)
            return [{**linha, "motor": "PADDLE"} for linha in resultado["linhas"]]
        except Exception:
            self.pendencias.add("PADDLE_INDISPONIVEL")
            return []
        finally:
            self.metricas["segundos_paddle"] += monotonic() - inicio
            if self._restante() <= 0:
                self.pendencias.add("ORCAMENTO_ESGOTADO")

    def _reler(self, imagem, linha, chave, *, ate=None):
        x0, y0, x1, y1 = linha["caixa"]
        altura, largura = imagem.shape[:2]
        x, y = max(0, x0 - 3), max(0, y0 - 3)
        amostra = imagem[y:min(altura, y1 + 3), x:min(largura, ate if ate else x1 + 3)]
        if not amostra.size or amostra.shape[0] > 200 or amostra.size > 600_000:
            return None

        def evidencias(candidatas):
            propostas = {}
            for candidata in candidatas:
                rotulo = _rotulo(candidata["texto"])
                if (rotulo and rotulo[1] == chave and _valido(*rotulo.groups())
                        and (candidata.get("confianca") or 0) >= 0.85):
                    propostas[rotulo[2]] = candidata
            return propostas

        lo, hi = np.percentile(amostra, (5, 95))
        ajustada = (np.clip((amostra.astype(np.float32) - lo) * (255 / (hi - lo)), 0, 255)
                    .astype(np.uint8)) if hi - lo > 12 else amostra
        ampliada = np.repeat(np.repeat(ajustada, 2, axis=0), 2, axis=1)
        primeira = evidencias(self._leve(ampliada, 7))
        if not ate:
            return bool(primeira or evidencias(self._pesado(ajustada)))
        if len(primeira) != 1:
            return None
        segunda = evidencias(self._leve(amostra, 7))
        if primeira.keys() != segunda.keys():
            return None
        candidata = next(iter(segunda.values()))
        c = candidata["caixa"]
        return {**candidata, "caixa": [c[0] + x, c[1] + y, c[2] + x, c[3] + y]}

    def reconhecer(self, imagem):
        largura = imagem.shape[1]
        previa, self._previa = self._previa, None
        linhas = previa[1] if previa and previa[0] is imagem else self._leve(imagem, self._psm(imagem))
        if self.corrigir and not any(len(l["texto"].strip()) >= 4 for l in linhas):
            if not any(x.endswith("INDISPONIVEL") for x in self.pendencias):
                linhas = self._leve(imagem, 11)
            if not linhas:
                linhas = self._pesado(imagem)
        elif (self.corrigir and self.leitura_esparsa and self._psm(imagem) == 3
              and self.tentativas < self.max_tentativas):
            # A análise de layout trata rótulos miúdos e isolados ("GERAL", "EXPEDIÇÃO") e números
            # em tinta colorida como desenho; a leitura esparsa os recupera nas áreas que ficaram vazias.
            linhas = [*linhas, *_fora_das_linhas(self._leve(imagem, 11), linhas)]
        if not linhas:
            self.pendencias.add("SEM_TEXTO_UTIL")
        if self.corrigir:
            for i, linha in enumerate(linhas):
                achado = _rotulo(linha["texto"])
                if not achado or achado[1] in {"RG", "REGISTRO GERAL"}:
                    continue
                chave, valor = achado.groups()
                if valor and not _valido(chave, valor):
                    self.pendencias.add("CAMPO_REQUER_REVISAO")
                    if self.tentativas < self.max_tentativas and self._reler(imagem, linha, chave):
                        self.pendencias.add("EVIDENCIA_ALTERNATIVA_REQUER_REVISAO")
                elif not valor and chave in {"CPF DO TITULAR", "CPF DO TRABALHADOR",
                                             "NASCIMENTO", "DATA DE NASCIMENTO"}:
                    _, y0, x1, y1 = linha["caixa"]
                    limites = [
                        c[0] for l in linhas if l is not linha
                        and (c := l["caixa"])[0] >= x1 and min(c[3], y1) > max(c[1], y0)
                    ]
                    fim = min(x1 + 180, largura, *limites) if limites else min(x1 + 180, largura)
                    if fim - x1 >= 20 and self.tentativas + 2 <= self.max_tentativas:
                        revisada = self._reler(imagem, linha, chave, ate=fim)
                        if revisada:
                            linhas[i] = revisada
                            self.metricas["campos_recuperados"] += 1
                            continue
                    self.pendencias.add("CAMPO_REQUER_REVISAO")
        return {"texto": "\n".join(l["texto"] for l in linhas), "linhas": linhas}


def modo_ocr():
    modo = os.environ.get("CEUMA_OCR_MODO", "adaptativo").lower()
    if modo not in {"adaptativo", "tesseract", "paddle"}:
        raise ValueError("CEUMA_OCR_MODO inválido.")
    return modo


def modo_orientacao():
    return os.environ.get("CEUMA_OCR_ORIENTACAO", "paddle").lower()


def criar_leitor_ocr(*, orientador=None):
    modo = modo_ocr()
    if modo == "paddle":
        return LeitorOCR()
    orientacao = modo_orientacao()
    return LeitorAdaptativo(
        orientacao=orientacao, orientador=orientador, corrigir=modo == "adaptativo",
        permitir_paddle=modo == "adaptativo" and os.environ.get("CEUMA_OCR_FALLBACK_PADDLE") == "1",
        segundos_pagina=float(os.environ.get("CEUMA_OCR_SEGUNDOS_PAGINA", "15")),
        segundos_documento=float(os.environ.get("CEUMA_OCR_SEGUNDOS_DOCUMENTO", "60")),
        reserva_paddle=float(os.environ.get("CEUMA_OCR_RESERVA_PADDLE", "35")),
    )
