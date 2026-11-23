import subprocess
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np
import pymupdf

from ceuma.documentos.analisador import analisar_documento
from ceuma.banco.persistencia import salvar_resultado_analise
from ceuma.relatorios.relatorio import recuperar_relatorio
from ceuma.banco import conexao
from ceuma.leitura.ocr.adaptativo import LeitorAdaptativo
from ceuma.leitura.ocr.tesseract import FalhaOCR, Tesseract, _tsv
from ceuma.leitura.pdf import ler_pdf


_HEADER = b"level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n"


class TesteAdaptativo(unittest.TestCase):
    def setUp(self):
        self.img = np.zeros((100, 250, 3), dtype=np.uint8)

    def test_tsv_e_geometria(self):
        linhas = _tsv(_HEADER + b"5\t1\t1\t1\t2\t3\t40\t8\t90\tNOME:\n"
                      b"5\t1\t1\t1\t45\t3\t50\t8\t80\tFIXTURE\n")
        self.assertEqual(linhas, [{"texto": "NOME: FIXTURE", "caixa": [2, 3, 95, 11],
                                   "confianca": 0.8, "motor": "TESSERACT"}])

    def test_tsv_invalido(self):
        for entrada in (b"inv", _HEADER + b"5\t1\t1\t1\t0\t0\t3\t8\tNaN\tFIXTURE\n"):
            with self.assertRaisesRegex(FalhaOCR, "TSV_INVALIDO"):
                _tsv(entrada)

    def test_executavel_ausente(self):
        with TemporaryDirectory() as pasta:
            with self.assertRaisesRegex(FalhaOCR, "TESSERACT_INDISPONIVEL"):
                Tesseract(Path(pasta, "ausente.exe")).reconhecer(self.img, 6, 1)

    def test_portugues_falha_e_timeout(self):
        with TemporaryDirectory() as pasta:
            exe = Path(pasta, "fixture.exe")
            exe.touch()
            motor = Tesseract(exe)
            for stderr, estado in ((b"por.traineddata", "IDIOMA_PORTUGUES_INDISPONIVEL"),
                                   (b"fixture", "FALHA_TESSERACT")):
                with patch("ceuma.leitura.ocr.tesseract.subprocess.run", return_value=Mock(
                    returncode=1, stderr=stderr,
                )) as chamada:
                    with self.assertRaisesRegex(FalhaOCR, estado):
                        motor.reconhecer(self.img, 7, 0.8)
                    args, kwargs = chamada.call_args
                    self.assertIn("por", args[0])
                    self.assertFalse(kwargs["shell"])
                    self.assertEqual(kwargs["timeout"], 0.8)
                    self.assertTrue(kwargs["input"].startswith(b"\x89PNG"))
            with patch("ceuma.leitura.ocr.tesseract.subprocess.run",
                       side_effect=subprocess.TimeoutExpired("fixture", 1)):
                with self.assertRaisesRegex(FalhaOCR, "TIMEOUT_TESSERACT"):
                    motor.reconhecer(self.img, 6, 1)

    def test_orcamento_sem_tentativa_e_sem_paddle(self):
        tess, paddle = Mock(), Mock()
        motor = LeitorAdaptativo(tesseract=tess, paddle=paddle)
        motor.fim_pagina = 0
        self.assertEqual(motor.reconhecer(self.img)["linhas"], [])
        self.assertIn("ORCAMENTO_ESGOTADO", motor.pendencias)
        tess.reconhecer.assert_not_called()
        paddle.reconhecer.assert_not_called()

    def test_fallback_indisponivel_e_limite(self):
        tess, paddle = Mock(), Mock()
        tess.reconhecer.return_value = []
        paddle.reconhecer.side_effect = FileNotFoundError
        motor = LeitorAdaptativo(tesseract=tess, paddle=paddle, permitir_paddle=True,
                                 reserva_paddle=0.1, max_tentativas=3)
        motor.reconhecer(self.img)
        self.assertEqual(tess.reconhecer.call_count, 2)
        self.assertEqual(paddle.reconhecer.call_count, 1)
        self.assertIn("PADDLE_INDISPONIVEL", motor.pendencias)
        motor.reconhecer(self.img)
        self.assertEqual(motor.tentativas, 3)

    def test_recorte_nao_substitui_identificador_conflitante(self):
        linha = {"texto": "CPF: 00000000000", "caixa": [5, 5, 100, 20], "confianca": .8}
        tess = Mock()
        from teste_analise_familiar import cpf_sintetico
        tess.reconhecer.side_effect = [[linha], [{**linha, "confianca": .95, "texto": "CPF: " + cpf_sintetico(1)}]]
        motor = LeitorAdaptativo(tesseract=tess)
        dados = motor.reconhecer(self.img)
        self.assertEqual(tess.reconhecer.call_args.args[1], 7)
        self.assertEqual(dados["linhas"], [linha])
        self.assertIn("EVIDENCIA_ALTERNATIVA_REQUER_REVISAO", motor.pendencias)

    def test_recupera_rotulo_vazio_com_duas_leituras_concordantes(self):
        from teste_analise_familiar import cpf_sintetico
        linha = {"texto": "CPF DO TITULAR:", "caixa": [5, 5, 95, 20], "confianca": .92}
        recuperada = {**linha, "texto": "CPF DO TITULAR: " + cpf_sintetico(1)}
        tess = Mock()
        tess.reconhecer.side_effect = [[linha], [recuperada], [recuperada]]
        motor = LeitorAdaptativo(tesseract=tess)
        dados = motor.reconhecer(self.img)
        self.assertEqual(dados["texto"], recuperada["texto"])
        self.assertEqual(dados["linhas"][0]["caixa"], [7, 7, 97, 22])
        self.assertEqual(motor.metricas["campos_recuperados"], 1)

    def test_texto_frontal_reutiliza_tesseract_sem_carregar_orientador(self):
        tess, paddle = Mock(), Mock()
        paddle.orientador = None
        tess.reconhecer.return_value = [
            {"texto": t, "caixa": [5, y, 160, y + 8], "confianca": .94}
            for y, t in enumerate((
                "CARTEIRA NACIONAL DE HABILITACAO", "NOME: TITULAR FICTICIO",
                "CATEGORIA: B", "DATA DE NASCIMENTO: 01/01/2000",
            ), 10)
        ]
        motor = LeitorAdaptativo(tesseract=tess, paddle=paddle)
        self.assertEqual(motor.classificar_orientacao(self.img), [(0, .85)])
        motor.reconhecer(self.img)
        tess.reconhecer.assert_called_once()
        paddle.classificar_orientacao.assert_not_called()

    def test_pendencia_chega_ao_relatorio(self):
        from teste_ctps_fisica import documento, CAPA, QUALIFICACAO
        d = documento(CAPA, QUALIFICACAO)
        d["paginas"][0]["diagnostico_ocr"] = {"pendencias": ["ORCAMENTO_ESGOTADO"], "metricas": {}}
        r = analisar_documento(d)
        with closing(conexao.conectar_banco(":memory:")) as banco:
            conexao.criar_tabelas(banco)
            codigo = conexao.criar_analise(banco)
            doc = salvar_resultado_analise(banco, 1, r)["documento_id"]
            with banco:
                banco.execute("UPDATE analises SET status='CONCLUIDA'")
            self.assertEqual(recuperar_relatorio(banco, codigo)[0]["leituras_pendentes"], [doc])

    def test_digest_e_caminho_digital_sem_chamadas(self):
        import hashlib
        tess, paddle = Mock(), Mock()
        motor = LeitorAdaptativo(tesseract=tess, paddle=paddle)
        with TemporaryDirectory() as pasta:
            caminho = Path(pasta, "fixture.pdf")
            with pymupdf.open() as pdf:
                p = pdf.new_page()
                p.insert_text((50, 50), "PESSOA FICTICIA DOCUMENTO DIGITAL PARA TESTE")
                pdf.save(caminho)
            original = hashlib.sha256(caminho.read_bytes()).digest()
            leitura = ler_pdf(caminho, leitor_ocr=motor)["paginas"][0]
            self.assertEqual(leitura["metodo"], "DIGITAL")
            self.assertEqual(hashlib.sha256(caminho.read_bytes()).digest(), original)
        self.assertFalse(tess.mock_calls)
        self.assertFalse(paddle.mock_calls)
