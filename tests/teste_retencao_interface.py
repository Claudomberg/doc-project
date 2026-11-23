import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ceuma.relatorios import exportador_csv as exportador
from ceuma.relatorios.relatorio import gerar_relatorio
from ceuma.banco import conexao as banco
from ceuma.banco.operacoes import adicionar_documento, registrar_conferencia, salvar_campo_extraido
from ceuma.banco.retencao import executar_limpeza
from ceuma.interface import janela_principal as interface


AGORA = datetime(2040, 1, 2, 12, tzinfo=timezone.utc)


class TesteRetencaoSQLite(unittest.TestCase):
    def setUp(self):
        self.conexao = self.enterContext(closing(banco.conectar_banco(":memory:")))
        banco.criar_tabelas(self.conexao)
        self.enterContext(patch.object(banco, "agora_utc", return_value=AGORA))

    def criar(self, instante):
        with patch.object(banco, "agora_utc", return_value=instante):
            return banco.criar_analise(self.conexao)

    def test_limite_de_24_horas_inclusivo_e_cascata(self):
        ativa = self.criar(AGORA - timedelta(hours=24) + timedelta(seconds=1))
        no_limite = self.criar(AGORA - timedelta(hours=24))
        expirada = self.criar(AGORA - timedelta(hours=25))
        with TemporaryDirectory() as pasta:
            pdf = Path(pasta) / "origem_ficticia.pdf"
            pdf.write_bytes(b"%PDF-conteudo-ficticio-nao-lido-pela-retencao")
            conteudo = pdf.read_bytes()
            for codigo in (ativa, no_limite, expirada):
                ident = self.conexao.execute(
                    "SELECT id FROM analises WHERE codigo=?", (codigo,),
                ).fetchone()[0]
                documento = adicionar_documento(self.conexao, ident, pdf.name, str(pdf))
                salvar_campo_extraido(self.conexao, documento, "NOME", "TITULAR FICTÍCIO")
                registrar_conferencia(self.conexao, ident, "NOME", "NAO_CONFERIDO")

            self.assertEqual(banco.consultar_expiracao_ativa(self.conexao, ativa),
                             AGORA + timedelta(seconds=1))
            self.assertIsNone(banco.consultar_expiracao_ativa(self.conexao, no_limite))
            self.assertIsNone(banco.consultar_expiracao_ativa(self.conexao, expirada))
            self.assertEqual(executar_limpeza(self.conexao), 2)
            for tabela in ("analises", "documentos", "campos_extraidos", "conferencias"):
                self.assertEqual(self.conexao.execute(
                    f"SELECT COUNT(*) FROM {tabela}",
                ).fetchone()[0], 1)
            self.assertEqual(pdf.read_bytes(), conteudo)

    def test_horarios_com_offset_utc_menos_tres_e_legado_sem_fuso(self):
        fuso = timezone(timedelta(hours=-3))
        ativa = self.criar(AGORA)
        limite = self.criar(AGORA)
        legado = self.criar(AGORA)
        horarios = (
            ((AGORA + timedelta(seconds=1)).astimezone(fuso).isoformat(), ativa),
            (AGORA.astimezone(fuso).isoformat(), limite),
            (AGORA.replace(tzinfo=None).isoformat(), legado),
        )
        with self.conexao:
            self.conexao.executemany("UPDATE analises SET data_expiracao=? WHERE codigo=?", horarios)

        self.assertEqual(banco.consultar_expiracao_ativa(self.conexao, ativa),
                         AGORA + timedelta(seconds=1))
        self.assertIsNone(banco.consultar_expiracao_ativa(self.conexao, limite))
        self.assertEqual(banco.limpar_analises_expiradas(self.conexao), 2)
        self.assertEqual(executar_limpeza(self.conexao), 0)

    def test_limpeza_nao_antecipa_o_limite_por_arredondamento(self):
        codigo = self.criar(AGORA - timedelta(hours=24))
        with patch.object(banco, "agora_utc", return_value=AGORA - timedelta(microseconds=1)):
            self.assertEqual(banco.consultar_expiracao_ativa(self.conexao, codigo), AGORA)
            self.assertEqual(executar_limpeza(self.conexao), 0)
        self.assertEqual(executar_limpeza(self.conexao), 1)

    def test_falha_na_exclusao_reverte_o_lote_e_permite_tentar_novamente(self):
        primeira = self.criar(AGORA - timedelta(hours=25))
        segunda = self.criar(AGORA - timedelta(hours=26))
        self.conexao.execute(f"""
            CREATE TRIGGER falha_fixture AFTER DELETE ON analises
            WHEN OLD.codigo = '{segunda}'
            BEGIN
                SELECT RAISE(ABORT, 'falha ficticia');
            END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            executar_limpeza(self.conexao)
        self.assertEqual({linha[0] for linha in self.conexao.execute("SELECT codigo FROM analises")},
                         {primeira, segunda})
        self.conexao.execute("DROP TRIGGER falha_fixture")
        self.assertEqual(banco.limpar_analises_expiradas(self.conexao), 2)

    def test_limpeza_em_transacao_ativa_com_expirados_preserva_rollback(self):
        expirada = self.criar(AGORA - timedelta(hours=25))
        self.conexao.execute("UPDATE analises SET status='CONCLUIDA' WHERE codigo=?", (expirada,))
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "sem transação ativa"):
            executar_limpeza(self.conexao)
        self.assertTrue(self.conexao.in_transaction)
        self.assertEqual(self.conexao.execute("SELECT status FROM analises").fetchone()[0], "CONCLUIDA")
        self.conexao.rollback()
        self.assertEqual(self.conexao.execute("SELECT status FROM analises").fetchone()[0], "EM_ANDAMENTO")
        self.assertEqual(executar_limpeza(self.conexao), 1)

    def test_limpeza_rejeita_transacao_ativa_mesmo_sem_expirados(self):
        ativa = self.criar(AGORA)
        self.conexao.execute("UPDATE analises SET status='CONCLUIDA' WHERE codigo=?", (ativa,))
        with self.assertRaises(sqlite3.ProgrammingError):
            banco.limpar_analises_expiradas(self.conexao)
        self.assertTrue(self.conexao.in_transaction)
        self.assertEqual(self.conexao.execute("SELECT status FROM analises").fetchone()[0], "CONCLUIDA")
        self.conexao.rollback()
        self.assertEqual(self.conexao.execute("SELECT status FROM analises").fetchone()[0], "EM_ANDAMENTO")
        self.assertEqual(banco.limpar_analises_expiradas(self.conexao), 0)


class Agenda:
    def __init__(self):
        self.callbacks = []

    def after(self, intervalo, callback):
        self.callbacks.append((intervalo, callback))

    def executar(self, intervalo):
        indice = next(i for i, item in enumerate(self.callbacks) if item[0] == intervalo)
        self.callbacks.pop(indice)[1]()

    def title(self, _):
        pass

    def geometry(self, _):
        pass

    def minsize(self, *_):
        pass


class Controle:
    def __init__(self):
        self.opcoes = {"state": "disabled"}

    def config(self, **opcoes):
        self.opcoes.update(opcoes)


class ThreadImediata:
    """Executa a tarefa no teste e mantém a entrega pela fila da interface."""

    def __init__(self, *, target, args, daemon):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


class TesteRetencaoInterface(unittest.TestCase):
    def setUp(self):
        pasta = Path(self.enterContext(TemporaryDirectory()))
        self.banco = pasta / "ceuma_ficticio.db"
        self.csv = pasta / "destino_ficticio.csv"
        self.conectar = banco.conectar_banco
        with closing(self.conectar(self.banco)) as conexao:
            banco.criar_tabelas(conexao)

        self.relogio = self.enterContext(patch.object(banco, "agora_utc", return_value=AGORA))
        self.enterContext(patch.object(banco, "conectar_banco", side_effect=lambda: self.conectar(self.banco)))
        self.enterContext(patch.object(interface, "Thread", ThreadImediata))
        self.enterContext(patch.object(interface.JanelaPrincipal, "criar_interface", self.criar_controles))
        self.logs = self.enterContext(patch.object(interface.JanelaPrincipal, "registrar_log"))
        self.enterContext(patch.object(interface.messagebox, "showinfo"))
        self.aviso = self.enterContext(patch.object(interface.messagebox, "showwarning"))
        self.erro = self.enterContext(patch.object(interface.messagebox, "showerror"))
        self.destino = self.enterContext(patch.object(
            interface.filedialog, "asksaveasfilename", return_value=str(self.csv),
        ))
        self.agenda = Agenda()
        self.janela = interface.JanelaPrincipal(self.agenda)

    @staticmethod
    def criar_controles(janela):
        janela.botao_exportar = Controle()
        janela.botao_recuperar = Controle()
        janela.botao_pasta = Controle()
        janela.botao_processar = Controle()
        janela.status = Controle()

    def criar_relatorio(self):
        with closing(self.conectar(self.banco)) as conexao:
            codigo = banco.criar_analise(conexao)
            ident = conexao.execute("SELECT id FROM analises WHERE codigo=?", (codigo,)).fetchone()[0]
            expiracao = banco.consultar_expiracao_ativa(conexao, codigo)
            return gerar_relatorio(conexao, ident), expiracao

    def contar_analises(self):
        with closing(self.conectar(self.banco)) as conexao:
            return conexao.execute("SELECT COUNT(*) FROM analises").fetchone()[0]

    def iniciar_manutencao(self):
        self.agenda.executar(0)
        self.agenda.executar(150)

    def test_janela_aberta_e_ociosa_invalida_cache_e_limpa_sem_novo_lote(self):
        self.iniciar_manutencao()
        relatorio, expiracao = self.criar_relatorio()
        self.janela.finalizar_sucesso(relatorio, expiracao)
        self.assertEqual(self.janela.botao_exportar.opcoes["state"], "normal")
        self.relogio.return_value = expiracao

        self.agenda.executar(150)
        self.assertIsNone(self.janela.relatorio_atual)
        self.assertEqual(self.janela.botao_exportar.opcoes["state"], "disabled")
        self.assertEqual(self.contar_analises(), 1)

        self.agenda.executar(interface.INTERVALO_RETENCAO_MS)
        self.agenda.executar(150)
        self.assertEqual(self.contar_analises(), 0)
        self.assertIn(interface.INTERVALO_RETENCAO_MS, [valor for valor, _ in self.agenda.callbacks])

    def test_abertura_apos_prazo_limpa_sem_selecionar_pasta(self):
        _, expiracao = self.criar_relatorio()
        self.relogio.return_value = expiracao + timedelta(hours=1)
        self.assertEqual(self.contar_analises(), 1)
        self.iniciar_manutencao()
        self.assertEqual(self.contar_analises(), 0)
        self.assertIsNone(self.janela.pasta_selecionada)

    def test_falha_da_limpeza_nao_reabilita_relatorio_expirado_e_gera_nova_tentativa(self):
        relatorio, expiracao = self.criar_relatorio()
        self.janela.finalizar_sucesso(relatorio, expiracao)
        self.relogio.return_value = expiracao
        with patch.object(banco, "limpar_analises_expiradas", side_effect=sqlite3.OperationalError):
            self.iniciar_manutencao()
        self.assertTrue(any("tentará novamente" in chamada.args[0] for chamada in self.logs.call_args_list))
        self.assertEqual(self.contar_analises(), 1)
        self.assertIsNone(self.janela.relatorio_atual)
        self.assertEqual(self.janela.botao_exportar.opcoes["state"], "disabled")

        self.agenda.executar(interface.INTERVALO_RETENCAO_MS)
        self.agenda.executar(150)
        self.assertEqual(self.contar_analises(), 0)

    def test_exportacao_valida_e_expirada_preserva_csv_existente(self):
        relatorio, expiracao = self.criar_relatorio()
        self.janela.finalizar_sucesso(relatorio, expiracao)
        self.janela.exportar_relatorio()
        self.agenda.executar(150)
        conteudo = self.csv.read_bytes()
        self.assertIn(b"RELATORIO DE CONFERENCIA CEUMA", conteudo)
        self.relogio.return_value = expiracao
        self.destino.reset_mock()

        self.janela.exportar_relatorio()
        self.aviso.assert_called_once()
        self.destino.assert_not_called()
        self.assertEqual(self.csv.read_bytes(), conteudo)

    def test_exportacao_consulta_sqlite_mesmo_com_cache_aparentemente_ativo(self):
        relatorio, expiracao = self.criar_relatorio()
        self.janela.finalizar_sucesso(relatorio, expiracao)
        with closing(self.conectar(self.banco)) as conexao, conexao:
            conexao.execute("UPDATE analises SET data_expiracao=?", (AGORA.isoformat(),))

        self.janela.exportar_relatorio()
        self.agenda.executar(150)
        self.assertFalse(self.csv.exists())
        self.assertIsNone(self.janela.relatorio_atual)
        self.aviso.assert_called_once()

    def test_expiracao_durante_o_dialogo_bloqueia_exportacao(self):
        relatorio, expiracao = self.criar_relatorio()
        self.janela.finalizar_sucesso(relatorio, expiracao)

        def selecionar_destino(**_):
            self.relogio.return_value = expiracao
            return str(self.csv)

        self.destino.side_effect = selecionar_destino
        self.janela.exportar_relatorio()
        self.assertFalse(self.csv.exists())
        self.assertIsNone(self.janela.relatorio_atual)
        self.aviso.assert_called_once()

    def test_expiracao_durante_a_escrita_nao_publica_csv(self):
        relatorio, expiracao = self.criar_relatorio()
        self.janela.finalizar_sucesso(relatorio, expiracao)
        self.csv.write_bytes(b"csv anterior ficticio")
        original = exportador._gravar_relatorio

        def gravar_e_expirar(dados, arquivo):
            original(dados, arquivo)
            self.relogio.return_value = expiracao

        with (
            patch.object(interface.messagebox, "askyesno", return_value=True),
            patch.object(exportador, "_gravar_relatorio", side_effect=gravar_e_expirar),
        ):
            self.janela.exportar_relatorio()
        self.agenda.executar(150)
        self.assertEqual(self.csv.read_bytes(), b"csv anterior ficticio")
        self.assertFalse(list(self.csv.parent.glob(".ceuma-exportacao-*")))
        self.assertIsNone(self.janela.relatorio_atual)
        self.aviso.assert_called_once()


if __name__ == "__main__":
    unittest.main()
