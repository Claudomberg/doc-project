import sqlite3
import unittest
from contextlib import closing

from analise.analisador_documento import analisar_documento
from analise.conferidor_banco import conferir_analise
from analise.extrator_campos import extrair_cpfs_documento, extrair_rg, extrair_rg_documento
from analise.persistencia import salvar_resultado_analise
from banco.conexao import criar_analise, criar_tabelas
from banco.operacoes import registrar_conferencia, salvar_campo_extraido


CPF_TITULAR = "00000000191"
CPF_RESPONSAVEL = "00000000272"
RG = "12.345.678-9"


def documento(arquivo, *textos):
    return {
        "nome_arquivo": arquivo,
        "caminho_origem": arquivo,
        "quantidade_paginas": len(textos),
        "paginas": [
            {"numero": n, "texto": texto, "metodo": "DIGITAL"}
            for n, texto in enumerate(textos, 1)
        ],
    }


def documento_ocr(*linhas):
    resultado = documento("ocr_ficticio.pdf", "\n".join(texto for texto, _ in linhas))
    resultado["paginas"][0].update(
        metodo="OCR", linhas_ocr=[{"texto": texto, "caixa": caixa} for texto, caixa in linhas],
    )
    return resultado


class TesteIdentificadores(unittest.TestCase):
    def test_cpfs_com_rotulos_explicitos(self):
        entrada = documento(
            "ficticio.pdf",
            f"CPF DO RESPONSÁVEL LEGAL: {CPF_RESPONSAVEL}; CPF DA TITULAR: {CPF_TITULAR}",
            "CPF DA MÃE:\n00000000353\nDECLARANTE: CPF: 00000000434",
        )
        cpfs = extrair_cpfs_documento(entrada)
        self.assertEqual([item["papel"] for item in cpfs],
                         ["RESPONSAVEL", "TITULAR", "FILIACAO", "OUTRO_PARTICIPANTE"])
        self.assertTrue(all(item["valido"] for item in cpfs))
        self.assertEqual([item["pagina_origem"] for item in cpfs], [1, 1, 2, 2])

    def test_cpf_isolado_e_cpf_sem_qualificador_sao_ambiguos(self):
        for texto in (CPF_TITULAR, f"NOME: PESSOA FICTÍCIA\nCPF: {CPF_TITULAR}"):
            with self.subTest(texto=texto):
                cpf, = extrair_cpfs_documento(documento("ficticio.pdf", texto))
                self.assertEqual(cpf["papel"], "AMBIGUO")
                self.assertTrue(cpf["valido"])

    def test_cpfs_de_titular_concorrentes_em_paginas_distintas(self):
        entrada = documento("duas_paginas.pdf", f"CPF DO TITULAR: {CPF_TITULAR}",
                            f"CPF DO TITULAR: {CPF_RESPONSAVEL}")
        self.assertEqual([x["papel"] for x in extrair_cpfs_documento(entrada)],
                         ["AMBIGUO", "AMBIGUO"])

    def test_rg_nao_avanca_apos_rotulo_cpf(self):
        for cpf in (CPF_TITULAR, "000.000.001-91"):
            for texto in (f"RG\nCPF: {cpf}\n12345678", f"RG: CPF: {cpf}", f"RG: {cpf}"):
                with self.subTest(texto=texto):
                    self.assertIsNone(extrair_rg(texto)["valor"])

    def test_rg_com_e_sem_pontuacao_sem_truncar(self):
        for valor in (RG, "123456789", "12345678X"):
            with self.subTest(valor=valor):
                self.assertEqual(extrair_rg(f"R.G.: {valor}")["valor"], valor)
                self.assertEqual(extrair_rg(f"RG\n{valor}")["valor"], valor)
                self.assertEqual(extrair_rg_documento(documento_ocr(
                    ("RG", [0, 0, 100, 10]), (valor, [0, 20, 100, 30]),
                ))["valor"], valor)
        for valor in ("12.345.678-99", "1234567890123456"):
            self.assertIsNone(extrair_rg(f"RG: {valor}")["valor"])
        self.assertEqual(extrair_rg(f"RG: {RG}; CPF: {CPF_TITULAR}")["valor"], RG)

    def test_cpf_espacial_nao_compete_com_rg_valido(self):
        res = extrair_rg_documento(documento_ocr(
            ("RG", [0, 0, 100, 10]),
            (CPF_TITULAR, [0, 17, 100, 27]),
            (RG, [0, 20, 100, 30]),
            ("CPF", [110, 35, 210, 45]),
        ))
        self.assertEqual(res["valor"], RG)

    def test_rotulo_interposto_bloqueia_tambem_candidato_com_formato_de_rg(self):
        rgs = (
            (("RG", [0, 0, 100, 10]), ("CPF", [0, 12, 100, 22]), (RG, [0, 25, 100, 35])),
            (("RG", [0, 0, 30, 10]), ("CPF", [33, 0, 52, 10]), (RG, [58, 0, 140, 10])),
            (("RG", [55, 0, 150, 10]), ("CPF", [0, 25, 48, 35]), (RG, [55, 25, 150, 35])),
        )
        for linhas in rgs:
            with self.subTest(linhas=linhas):
                self.assertIsNone(extrair_rg_documento(documento_ocr(*linhas))["valor"])

    def test_rg_ja_lido_na_caixa_do_rotulo_nao_disputa_com_numero_avulso(self):
        res = extrair_rg_documento(documento_ocr(
            (f"RG: {RG}", [0, 0, 100, 10]),
            ("87654321", [0, 20, 100, 30]),
        ))
        self.assertEqual(res["valor"], RG)

    def test_rg_concorrente_requer_revisao_sem_valor(self):
        resultados = (
            extrair_rg(f"RG: {RG}\nREGISTRO GERAL: 87654321"),
            extrair_rg(f"RG: {RG} / 87654321"),
            extrair_rg_documento(documento_ocr(
                ("RG", [0, 0, 100, 10]),
                (RG, [0, 20, 100, 30]),
                ("87654321", [0, 25, 100, 35]),
            )),
        )
        for item in resultados:
            self.assertEqual(item["status"], "AMBIGUO")
            self.assertIsNone(item["valor"])


class TesteConferenciaTitular(unittest.TestCase):
    def setUp(self):
        self.banco = self.enterContext(closing(sqlite3.connect(":memory:")))
        self.banco.execute("PRAGMA foreign_keys = ON")
        criar_tabelas(self.banco)
        criar_analise(self.banco)
        self.analise = self.banco.execute("SELECT id FROM analises").fetchone()[0]

    def persistir(self, arquivo, texto):
        return salvar_resultado_analise(
            self.banco, self.analise, analisar_documento(documento(arquivo, texto)),
        )["documento_id"]

    def resultados(self):
        return conferir_analise(self.banco, self.analise)

    def test_documentos_de_titulares_diferentes_com_cpf_compartilhado_de_responsavel(self):
        self.persistir("primeiro.pdf", f"CPF DO TITULAR: {CPF_TITULAR}\n"
                                       f"CPF DO RESPONSÁVEL: {CPF_RESPONSAVEL}")
        self.persistir("segundo.pdf", f"CPF DO TITULAR: {CPF_RESPONSAVEL}")
        estados = {status for status, in self.banco.execute(
            "SELECT status FROM campos_extraidos WHERE nome_campo='CPF'"
        )}
        self.assertEqual(estados, {"TITULAR_CONFIRMADO", "RESPONSAVEL"})
        item, = self.resultados()
        self.assertEqual((item["campo"], item["resultado"]), ("CPF", "DIVERGENTE"))

    def test_um_titular_confirmado_e_um_cpf_sem_evidencia_nao_sao_compativeis(self):
        self.persistir("primeiro.pdf", f"CPF DO TITULAR: {CPF_TITULAR}")
        self.persistir("segundo.pdf", f"CPF: {CPF_TITULAR}")
        item, = self.resultados()
        self.assertEqual(item["resultado"], "NAO_CONFERIDO")
        observacao, = self.banco.execute("SELECT observacao FROM conferencias").fetchone()
        self.assertIn("Revisão manual", observacao)

    def test_cpfs_legados_e_documento_sem_cpf_nao_provam_titularidade(self):
        id_legado = self.persistir("legado.pdf", "CARTEIRA DE IDENTIDADE")
        id_sem_cpf = self.persistir("sem_cpf.pdf", "CARTEIRA DE IDENTIDADE")
        salvar_campo_extraido(self.banco, id_legado, "CPF", CPF_TITULAR)
        item, = self.resultados()
        self.assertEqual(item["resultado"], "NAO_CONFERIDO")
        salvar_campo_extraido(self.banco, id_sem_cpf, "CPF", CPF_TITULAR)
        item, = self.resultados()
        self.assertEqual(item["resultado"], "NAO_CONFERIDO")

    def test_dois_cpfs_ambiguos_iguais_nao_estabelecem_compatibilidade(self):
        for arquivo in ("a.pdf", "b.pdf"):
            self.persistir(arquivo, f"CPF: {CPF_TITULAR}")
        item, = self.resultados()
        self.assertEqual(item["resultado"], "NAO_CONFERIDO")

    def test_reconferencia_preserva_registro_manual_e_nao_duplica_automaticos(self):
        for arquivo in ("a.pdf", "b.pdf"):
            self.persistir(arquivo, f"CPF DO TITULAR: {CPF_TITULAR}\n"
                                    f"CPF DO RESPONSÁVEL: {CPF_RESPONSAVEL}")
        registrar_conferencia(self.banco, self.analise, "CPF", "NAO_CONFERIDO", "MANUAL: fixture")
        for _ in range(2):
            item, = self.resultados()
            self.assertEqual(item["resultado"], "COMPATIVEL")
        self.assertEqual(self.banco.execute("SELECT COUNT(*) FROM conferencias").fetchone()[0], 2)

    def test_rg_ambiguo_persistido_para_revisao_sem_valor(self):
        self.persistir("dois_rgs.pdf", f"RG: {RG}\nRG: 87654321")
        linha, = self.banco.execute("SELECT valor, status FROM campos_extraidos WHERE nome_campo='RG'")
        self.assertEqual(linha, (None, "AMBIGUO"))


if __name__ == "__main__":
    unittest.main()
