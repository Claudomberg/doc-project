import codecs
import csv
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from ceuma.relatorios import exportador_csv as exportador
from ceuma.relatorios.relatorio import gerar_relatorio
from ceuma.banco import conexao as banco
from ceuma.interface import janela_principal as interface


class TesteExportacaoCSVSegura(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(self.enterContext(TemporaryDirectory()))
        self.destino = self.pasta / "relatorio.csv"
        self.relatorio = {
            "codigo": "ANALISE-FICTICIA",
            "data_criacao": "2026-01-01T00:00:00+00:00",
            "status": "CONCLUIDA",
            "documentos": [],
            "campos_extraidos": [],
            "conferencias": [],
            "total_documentos": 0,
            "total_conferencias": 0,
            "total_divergencias": 0,
        }

    def ler_linhas(self):
        with self.destino.open(encoding="utf-8-sig", newline="") as arquivo:
            return list(csv.reader(arquivo, delimiter=";"))

    def confirmar_destino(self):
        self.destino.write_bytes(b"relatorio anterior")
        return exportador.identificar_destino_csv(self.destino)

    def test_neutraliza_prefixos_em_todas_as_colunas_textuais(self):
        self.relatorio.update(
            codigo="=1+1", data_criacao=" +1", status="-1",
            documentos=[{"id": 1, "arquivo": "@ficticio.pdf", "tipo": "\u00a0=1+1"}],
            campos_extraidos=[{
                "arquivo": "@ficticio.pdf", "campo": "\ufeff=1+1", "valor": "\ttexto",
                "pagina": 1, "status": "\r\ntexto", "trecho_origem": "\x01+1",
            }],
            conferencias=[{
                "campo": "\u200b@ficticio", "resultado": "\uff1d1+1", "observacao": "\u22121",
            }],
        )
        exportador.exportar_relatorio_csv(self.relatorio, self.destino)

        linhas = self.ler_linhas()
        self.assertIn(["Codigo da analise", "'=1+1"], linhas)
        self.assertIn(["Data de criacao", "' +1"], linhas)
        self.assertIn(["Status", "'-1"], linhas)
        self.assertIn(["1", "'@ficticio.pdf", "'\u00a0=1+1"], linhas)
        self.assertIn(["'@ficticio.pdf", "'\ufeff=1+1", "'\ttexto", "1", "'\r\ntexto", "'\x01+1"], linhas)
        self.assertIn(["'\u200b@ficticio", "'\uff1d1+1", "'\u22121"], linhas)

    def test_preserva_acentos_aspas_separadores_e_zeros_sem_alterar_sqlite(self):
        nome = 'Cópia, "fictícia"; nº 1.pdf'
        observacao = '  Revisão, "fictícia"; mantida\nLinha 2.'
        campos = [("CPF", "00000000001"), ("RG", "00123456")]
        trecho = " =1+1"
        with closing(banco.conectar_banco(":memory:")) as conexao:
            banco.criar_tabelas(conexao)
            codigo = banco.criar_analise(conexao)
            ident = conexao.execute("SELECT id FROM analises WHERE codigo=?", (codigo,)).fetchone()[0]
            with conexao:
                documento = conexao.execute(
                    "INSERT INTO documentos(analise_id,nome_arquivo,caminho_origem) VALUES(?,?,?)",
                    (ident, nome, "ficticio.pdf"),
                ).lastrowid
                conexao.executemany(
                    "INSERT INTO campos_extraidos(documento_id,nome_campo,valor,trecho_origem) VALUES(?,?,?,?)",
                    [(documento, campo, valor, trecho) for campo, valor in campos],
                )
                conexao.execute(
                    "INSERT INTO conferencias(analise_id,campo_verificado,resultado,observacao) VALUES(?,?,?,?)",
                    (ident, "RG", "REVISAO", observacao),
                )
            antes = gerar_relatorio(conexao, ident)
            exportador.exportar_relatorio_csv(antes, self.destino)
            self.assertEqual(gerar_relatorio(conexao, ident), antes)
            self.assertEqual(antes["campos_extraidos"][0]["trecho_origem"], trecho)

        self.assertTrue(self.destino.read_bytes().startswith(codecs.BOM_UTF8))
        linhas = self.ler_linhas()
        self.assertIn(["RG", "REVISAO", observacao], linhas)
        for campo, valor in campos:
            self.assertIn([nome, campo, valor, "", "ENCONTRADO", "'" + trecho], linhas)

    def test_destino_existente_exige_confirmacao(self):
        self.confirmar_destino()
        with self.assertRaises(FileExistsError):
            exportador.exportar_relatorio_csv(self.relatorio, self.destino)
        self.assertEqual(self.destino.read_bytes(), b"relatorio anterior")
        self.assertEqual(list(self.pasta.iterdir()), [self.destino])

    def test_substitui_destino_confirmado_com_csv_completo(self):
        confirmacao = self.confirmar_destino()
        exportador.exportar_relatorio_csv(self.relatorio, self.destino, destino_confirmado=confirmacao)
        self.assertEqual(self.ler_linhas()[-1], ["Total de divergencias", "0"])
        self.assertEqual(list(self.pasta.iterdir()), [self.destino])

    def test_arquivo_criado_no_instante_da_publicacao_e_preservado(self):
        publicar = exportador._publicar_sem_sobrescrever

        def criar_destino_e_publicar(temporario, destino):
            destino.write_bytes(b"gravacao concorrente")
            publicar(temporario, destino)

        with patch.object(exportador, "_publicar_sem_sobrescrever", side_effect=criar_destino_e_publicar):
            with self.assertRaises(FileExistsError):
                exportador.exportar_relatorio_csv(self.relatorio, self.destino)
        self.assertEqual(self.destino.read_bytes(), b"gravacao concorrente")
        self.assertEqual(list(self.pasta.iterdir()), [self.destino])

    def test_mudanca_no_arquivo_confirmado_exige_nova_confirmacao(self):
        confirmacao = self.confirmar_destino()
        gravar = exportador._gravar_relatorio

        def gravar_e_mudar_destino(dados, arquivo):
            gravar(dados, arquivo)
            self.destino.write_bytes(b"alterado por outro programa")

        with patch.object(exportador, "_gravar_relatorio", side_effect=gravar_e_mudar_destino):
            with self.assertRaises(FileExistsError):
                exportador.exportar_relatorio_csv(self.relatorio, self.destino, destino_confirmado=confirmacao)
        self.assertEqual(self.destino.read_bytes(), b"alterado por outro programa")
        self.assertEqual(list(self.pasta.iterdir()), [self.destino])

    def test_falha_na_escrita_limpa_apenas_o_temporario_da_operacao(self):
        confirmacao = self.confirmar_destino()
        pasta_alheia = self.pasta / ".ceuma-exportacao-ficticia"
        pasta_alheia.mkdir()
        alheio = pasta_alheia / "relatorio.csv"
        alheio.write_bytes(b"arquivo alheio")

        def gravar_parcialmente(dados, arquivo):
            arquivo.write("parte do CSV")
            raise OSError("falha de gravação fictícia")

        with patch.object(exportador, "_gravar_relatorio", side_effect=gravar_parcialmente):
            with self.assertRaises(OSError):
                exportador.exportar_relatorio_csv(self.relatorio, self.destino, destino_confirmado=confirmacao)
        self.assertEqual(self.destino.read_bytes(), b"relatorio anterior")
        self.assertEqual(alheio.read_bytes(), b"arquivo alheio")
        self.assertEqual(set(self.pasta.iterdir()), {self.destino, pasta_alheia})

    def test_falha_na_publicacao_preserva_destino_e_limpa_temporario(self):
        confirmacao = self.confirmar_destino()
        with patch.object(Path, "replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                exportador.exportar_relatorio_csv(self.relatorio, self.destino, destino_confirmado=confirmacao)
        self.assertEqual(self.destino.read_bytes(), b"relatorio anterior")
        self.assertEqual(list(self.pasta.iterdir()), [self.destino])

    def test_arquivo_alheio_dentro_da_pasta_temporaria_e_preservado(self):
        def gravar_parcialmente(dados, arquivo):
            alheio = Path(arquivo.name).parent / "alheio.txt"
            alheio.write_bytes(b"arquivo alheio")
            raise OSError("falha fictícia")

        with patch.object(exportador, "_gravar_relatorio", side_effect=gravar_parcialmente):
            with self.assertRaises(OSError):
                exportador.exportar_relatorio_csv(self.relatorio, self.destino)
        temporario, = self.pasta.iterdir()
        alheio, = temporario.iterdir()
        self.assertEqual(alheio.name, "alheio.txt")
        self.assertEqual(alheio.read_bytes(), b"arquivo alheio")

    def test_cancelamento_na_interface_nao_inicia_exportacao(self):
        self.confirmar_destino()
        janela = Mock(processando=False, _exportando=False, relatorio_atual=self.relatorio)
        janela._verificar_prazo_relatorio.return_value = False
        with (
            patch.object(interface.filedialog, "asksaveasfilename", return_value=str(self.destino)) as escolher,
            patch.object(interface.messagebox, "askyesno", return_value=False) as confirmar,
            patch.object(interface, "Thread") as worker,
        ):
            interface.JanelaPrincipal.exportar_relatorio(janela)
        self.assertFalse(escolher.call_args.kwargs["confirmoverwrite"])
        self.assertEqual(confirmar.call_args.kwargs["default"], "no")
        worker.assert_not_called()
        self.assertFalse(janela._exportando)
        self.assertEqual(self.destino.read_bytes(), b"relatorio anterior")


if __name__ == "__main__":
    unittest.main()
