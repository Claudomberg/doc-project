import csv
import hashlib
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf

from ceuma.documentos.analisador import analisar_documento
from ceuma.conferencia.automatica import conferir_analise
from ceuma.relatorios.exportador_csv import exportar_relatorio_csv
from ceuma.banco.persistencia import salvar_resultado_analise
from ceuma.relatorios.relatorio import recuperar_relatorio
from ceuma.banco import conexao as banco
from ceuma.leitura.pdf import ler_pdf
from teste_extracao_identidade import _CIN_COMPLETA, _RG_COMPLETO, analisar, linha
from teste_orientacao_pdf import OCRDeOrientacaoSimulado, _arte


_CPF = "00000000191"
_REGISTRO = "00000000272"
_TITULO = "CARTEIRA NACIONAL DE HABILITACAO"
_ANTIGA = f"""{_TITULO}
NOME: TITULAR FICTICIO DA SILVA
CPF DO TITULAR: {_CPF}
RG DE ORIGEM: 12.345.678-9
ORGAO EMISSOR DO RG: SSP/MA
NASCIMENTO: 14/05/2001
Nº REGISTRO: {_REGISTRO}
1ª HABILITACAO: 01/04/2021
EMISSAO: 17/04/2023
VALIDADE: 17/04/2033
CAT. HAB.: B
FILIACAO: PESSOA PARENTE FICTICIA
NACIONALIDADE: BRASILEIRA"""
_ATUAL = f"""{_TITULO}
2 E 1 NOME E SOBRENOME: TITULAR FICTICIO DA SILVA
3 DATA DE NASCIMENTO: 14/05/2001
4A DATA DE EMISSAO: 17/04/2023
4B VALIDADE: 17/04/2033
4C DOC. IDENTIDADE / ORG. EMISSOR / UF: 12.345.678-9 SSP/MA
4D CPF: {_CPF}
5 NUMERO DE REGISTRO: {_REGISTRO}
1A HABILITACAO: 01/04/2021
9 CAT. HAB.: AB
ORGAO RESPONSAVEL PELA CNH: DETRAN/SP"""


class TesteExtracaoCNH(unittest.TestCase):
    def campos(self, *paginas):
        resultado = analisar(*paginas)
        self.assertEqual(resultado["identificacao"]["tipo"], "CNH")
        return resultado["campos"]["cnh"]

    def test_cnh_antiga_com_campos_completos(self):
        campos = self.campos(_ANTIGA)
        esperados = {
            "NOME": "TITULAR FICTICIO DA SILVA", "CPF": _CPF, "RG_ORIGEM": "12.345.678-9",
            "ORGAO_EMISSOR_RG": "SSP", "UF_EMISSAO_RG": "MA", "REGISTRO_CNH": _REGISTRO,
            "DATA_NASCIMENTO": "14/05/2001", "DATA_PRIMEIRA_HABILITACAO": "01/04/2021",
            "DATA_EMISSAO": "17/04/2023", "DATA_VALIDADE": "17/04/2033", "CATEGORIA_CNH": "B",
            "FILIACAO": "PESSOA PARENTE FICTICIA", "NACIONALIDADE": "BRASILEIRA",
        }
        self.assertEqual({k: v["valor"] for k, v in campos.items() if v["status"] == "ENCONTRADO"}, esperados)

    def test_cnh_atual_com_rotulos_numerados_e_compostos(self):
        campos = self.campos(_ATUAL)
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO DA SILVA")
        self.assertEqual(campos["CPF"]["status"], "ENCONTRADO")
        self.assertEqual(campos["REGISTRO_CNH"]["valor"], _REGISTRO)
        self.assertEqual(campos["CATEGORIA_CNH"]["valor"], "AB")
        self.assertEqual(campos["RG_ORIGEM"]["valor"], "12.345.678-9")

    def test_permissao_para_dirigir_nao_e_categoria(self):
        texto = f"PERMISSAO PARA DIRIGIR\nNOME: TITULAR FICTICIO\nNº REGISTRO: {_REGISTRO}\nCATEGORIA: A"
        campos = self.campos(texto)
        self.assertEqual(campos["CATEGORIA_CNH"]["valor"], "A")
        self.assertEqual(self.campos(texto.rsplit("\n", 1)[0])["CATEGORIA_CNH"]["status"], "NAO_ENCONTRADO")

    def test_cnh_sem_rg_de_origem(self):
        campos = self.campos(f"{_TITULO}\n9 CAT. HAB.: B\nNUMERO DE REGISTRO: {_REGISTRO}")
        self.assertEqual(campos["RG_ORIGEM"]["status"], "NAO_ENCONTRADO")
        self.assertEqual(campos["REGISTRO_CNH"]["status"], "ENCONTRADO")

    def test_cpf_e_registro_sao_confirmados_pelos_rotulos(self):
        r = analisar(f"{_TITULO}\nCAT. HAB.: B\nNUMERO DE REGISTRO: {_REGISTRO}\nCPF DO TITULAR: {_CPF}")
        cpf, = r["campos"]["cpfs"]
        self.assertEqual((cpf["valor"], cpf["papel"]), (_CPF, "TITULAR"))
        self.assertEqual(r["campos"]["cnh"]["REGISTRO_CNH"]["valor"], _REGISTRO)

    def test_numero_de_controle_nao_e_registro_nem_cpf_do_titular(self):
        campos = self.campos(f"{_TITULO}\nCAT. HAB.: C\nCONTROLE: {_REGISTRO}\nRENACH: {_CPF}")
        for campo in ("REGISTRO_CNH", "CPF"):
            self.assertEqual(campos[campo]["status"], "NAO_ENCONTRADO")

    def test_candidato_de_controle_em_caixa_separada_fica_reservado(self):
        campos = self.campos([
            linha(_TITULO, 0, 5, 360), linha("CAT. HAB.: C", 0, 30),
            linha("CONTROLE", 10, 70, 110), linha("CPF", 130, 70, 35),
            linha(_CPF, 35, 92, 160),
        ])
        self.assertIsNone(campos["CPF"]["valor"])
        self.assertIsNone(campos["REGISTRO_CNH"]["valor"])

    def test_cpf_sem_contexto_pessoal_local_fica_inconclusivo(self):
        campos = self.campos(f"{_TITULO}\nCAT. HAB.: B\nCPF: {_CPF}")
        self.assertEqual(campos["CPF"]["status"], "EVIDENCIA_INSUFICIENTE")

    def test_numero_de_registro_curto_ou_longo_e_invalido(self):
        for numero in ("12345", "123456789012"):
            campos = self.campos(f"{_TITULO}\nCAT. HAB.: C\nNUMERO DE REGISTRO: {numero}")
            self.assertEqual(campos["REGISTRO_CNH"]["status"], "INVALIDO")
            self.assertIsNone(campos["REGISTRO_CNH"]["valor"])

    def test_datas_proximas_em_uma_linha_usam_rotulos_e_nao_ordem(self):
        campos = self.campos(
            f"{_TITULO}\nCAT. HAB.: C\nVALIDADE: 17/04/2033 EMISSAO: 17/04/2023 "
            "1A HABILITACAO: 01/04/2021 NASCIMENTO: 14/05/2001"
        )
        self.assertEqual([campos[c]["valor"] for c in (
            "DATA_NASCIMENTO", "DATA_PRIMEIRA_HABILITACAO", "DATA_EMISSAO", "DATA_VALIDADE",
        )], ["14/05/2001", "01/04/2021", "17/04/2023", "17/04/2033"])

    def test_data_invalida_e_emissao_conflitante_preservam_estados(self):
        campos = self.campos(
            f"{_TITULO}\nCAT. HAB.: C\nNASCIMENTO: 31/02/2001\nEMISSAO: 17/04/2023\nEMISSAO: 18/04/2023"
        )
        self.assertEqual(campos["DATA_NASCIMENTO"]["status"], "INVALIDO")
        self.assertEqual(campos["DATA_EMISSAO"]["status"], "AMBIGUO")
        self.assertIsNone(campos["DATA_EMISSAO"]["valor"])

    def test_cronologia_inconsistente_sinaliza_sem_corrigir_datas(self):
        campos = self.campos(f"{_TITULO}\nCAT. HAB.: B\nEMISSAO: 17/04/2033\nVALIDADE: 17/04/2023")
        self.assertEqual(campos["CRONOLOGIA_CNH"]["status"], "INCONSISTENCIA_CRONOLOGICA")
        self.assertEqual(campos["DATA_EMISSAO"]["valor"], "17/04/2033")
        self.assertEqual(campos["DATA_VALIDADE"]["valor"], "17/04/2023")

    def test_validade_no_passado_permanece_um_campo_identificado(self):
        campos = self.campos(f"{_TITULO}\nCAT. HAB.: B\nEMISSAO: 17/04/2005\nVALIDADE: 17/04/2010")
        self.assertEqual(campos["DATA_VALIDADE"]["status"], "ENCONTRADO")
        self.assertNotIn("CRONOLOGIA_CNH", campos)

    def test_categorias_individuais_e_combinacoes_com_a(self):
        for impresso, canonico in (
            ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"), ("E", "E"),
            ("B/A", "AB"), ("AC", "AC"), ("A + D", "AD"), ("AE", "AE"),
        ):
            with self.subTest(impresso=impresso):
                self.assertEqual(self.campos(f"{_TITULO}\nCAT. HAB.: {impresso}")["CATEGORIA_CNH"]["valor"],
                                 canonico)

    def test_categorias_concorrentes_ou_combinacao_incompativel_sao_ambiguas(self):
        for corpo in ("CAT. HAB.: B/C", "CAT. HAB.: B\nCAT. HAB.: C"):
            categoria = self.campos(f"{_TITULO}\n{corpo}")["CATEGORIA_CNH"]
            self.assertEqual(categoria["status"], "AMBIGUO")
            self.assertIsNone(categoria["valor"])

    def test_acc_observacoes_e_restricoes_nao_preenchem_categoria(self):
        campos = self.campos(
            f"{_TITULO}\nNUMERO DE REGISTRO: {_REGISTRO}\nACC: A\nOBSERVACOES: B\nRESTRICOES: C"
        )
        self.assertEqual(campos["CATEGORIA_CNH"]["status"], "NAO_ENCONTRADO")
        self.assertIsNone(self.campos(f"{_TITULO}\nCAT. HAB.: ACC")["CATEGORIA_CNH"]["valor"])

    def test_filiacao_proxima_do_nome_nao_vira_titular(self):
        campos = self.campos(
            f"{_TITULO}\nCAT. HAB.: B\nFILIACAO\nNOME: PARENTE FICTICIO\nNOME DO TITULAR: TITULAR FICTICIO"
        )
        self.assertEqual(campos["FILIACAO"]["valor"], "PARENTE FICTICIO")
        self.assertEqual(campos["NOME"]["valor"], "TITULAR FICTICIO")

    def test_orgao_rg_e_orgao_cnh_preservam_as_respectivas_ufs(self):
        campos = self.campos(_ATUAL + "\nUF DE NASCIMENTO: BA\nUF: RJ")
        self.assertEqual([campos[k]["valor"] for k in (
            "ORGAO_EMISSOR_RG", "UF_EMISSAO_RG", "ORGAO_EMISSOR_CNH", "UF_EMISSAO_CNH",
        )], ["SSP", "MA", "DETRAN", "SP"])

    def test_uf_e_orgao_genericos_sem_contexto_nao_sao_preenchidos(self):
        campos = self.campos(f"{_TITULO}\nCAT. HAB.: B\nORGAO EMISSOR: SSP/MA\nUF: SP")
        for campo in ("ORGAO_EMISSOR_RG", "UF_EMISSAO_RG", "ORGAO_EMISSOR_CNH", "UF_EMISSAO_CNH"):
            self.assertIsNone(campos[campo]["valor"])

    def test_rotulo_composto_liga_rg_orgao_e_uf_em_caixas_distintas(self):
        campos = self.campos([
            linha(_TITULO, 5, 5, 380), linha("CAT. HAB.: B", 5, 30),
            linha("DOC. IDENTIDADE / ORG. EMISSOR / UF", 5, 64, 280),
            linha("12.345.678-9", 5, 90, 99), linha("SSP", 119, 90, 38),
            linha("MA", 173, 90, 30), linha("SP", 243, 90, 30),
        ])
        self.assertEqual(campos["RG_ORIGEM"]["valor"], "12.345.678-9")
        self.assertEqual(campos["ORGAO_EMISSOR_RG"]["valor"], "SSP")
        self.assertEqual(campos["UF_EMISSAO_RG"]["valor"], "MA")
        self.assertIsNone(campos["UF_EMISSAO_CNH"]["valor"])

    def test_frente_e_verso_em_paginas_diferentes_preservam_a_origem(self):
        campos = self.campos(
            f"{_TITULO}\nCAT. HAB.: B\nNUMERO DE REGISTRO: {_REGISTRO}",
            "FILIACAO: PESSOA PARENTE FICTICIA\nNACIONALIDADE: BRASILEIRA",
        )
        self.assertEqual(campos["REGISTRO_CNH"]["pagina_origem"], 1)
        self.assertEqual(campos["FILIACAO"]["pagina_origem"], 2)
        self.assertEqual(campos["NACIONALIDADE"]["pagina_origem"], 2)

    def test_rotulos_fragmentados_e_valores_horizontal_e_vertical(self):
        campos = self.campos([
            linha(_TITULO, 5, 5, 380), linha("CAT. HAB.: B", 5, 30),
            linha("NUMERO DE", 5, 64, 90), linha("REGISTRO", 100, 64, 78),
            linha(_REGISTRO, 186, 64, 80),
            linha("DATA DE", 5, 110, 56), linha("EMISSAO", 64, 110, 73),
            linha("17/04/2023", 12, 139, 115),
        ])
        self.assertEqual(campos["REGISTRO_CNH"]["valor"], _REGISTRO)
        self.assertEqual(campos["DATA_EMISSAO"]["valor"], "17/04/2023")

    def test_pdf_rotacionado_misto_preserva_categorias_rodape_e_original(self):
        class Simulado(OCRDeOrientacaoSimulado):
            def reconhecer(self, imagem):
                self.leituras += 1
                if self._correcao(imagem):
                    return {"linhas": []}
                return {"linhas": [linha(texto, 30, 15 + 25 * i, 360)
                                   for i, texto in enumerate(_ANTIGA.splitlines()[:11])]}

        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "cnh-sintetica.pdf"
            with pymupdf.open() as pdf:
                p = pdf.new_page(width=240, height=205)
                p.insert_image(pymupdf.Rect(0, 0, 240, 160), stream=_arte(), rotate=180)
                p.insert_text((10, 190), "RODAPE DIGITAL FICTICIO")
                pdf.save(arquivo)
            antes = hashlib.sha256(arquivo.read_bytes()).digest()
            documento = ler_pdf(arquivo, leitor_ocr=Simulado())
            self.assertEqual(hashlib.sha256(arquivo.read_bytes()).digest(), antes)
        pagina, = documento["paginas"]
        self.assertEqual(pagina["orientacoes"][0]["angulo_aplicado"], 180)
        self.assertTrue(pagina["texto"].endswith("RODAPE DIGITAL FICTICIO"))
        campos = analisar_documento(documento)["campos"]["cnh"]
        for campo in ("CPF", "REGISTRO_CNH", "DATA_EMISSAO", "CATEGORIA_CNH"):
            self.assertEqual(campos[campo]["status"], "ENCONTRADO")


class TesteRelatorioCNH(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(closing(banco.conectar_banco(":memory:")))
        banco.criar_tabelas(self.con)
        self.codigo = banco.criar_analise(self.con)
        self.ident = self.con.execute("SELECT id FROM analises").fetchone()[0]

    def salvar(self, texto, nome):
        return salvar_resultado_analise(self.con, self.ident, analisar(texto, nome=nome))["documento_id"]

    def recuperar(self):
        with self.con:
            self.con.execute("UPDATE analises SET status='CONCLUIDA' WHERE id=?", (self.ident,))
        return recuperar_relatorio(self.con, self.codigo)[0]

    def test_persistencia_e_recuperacao_distinguem_registro_cpf_e_rg(self):
        self.salvar(_ATUAL, "cnh.pdf")
        relatorio = self.recuperar()
        campos = {item["campo"]: item for item in relatorio["campos_extraidos"]}
        self.assertEqual(campos["CPF"]["valor"], _CPF)
        self.assertEqual(campos["CPF"]["status"], "TITULAR_CONFIRMADO")
        self.assertEqual(campos["REGISTRO_CNH"]["valor"], _REGISTRO)
        self.assertEqual(campos["RG_ORIGEM"]["valor"], "12.345.678-9")
        self.assertNotIn("RG", campos)
        self.assertTrue(all(item["trecho_origem"] is None for item in campos.values()))

    def test_seis_estados_recuperados_e_exportados_sem_juntar_arquivos_homonimos(self):
        rg = self.salvar(
            "CARTEIRA DE IDENTIDADE\nRG: 12345678\nNOME: TITULAR FICTICIO\n"
            "NASCIMENTO: 31/02/2001\nEXPEDICAO: 01/04/2035\nEXPEDICAO: 02/04/2035\nORGAO EMISSOR",
            "mesmo_nome.pdf",
        )
        cin = self.salvar(
            f"CARTEIRA DE IDENTIDADE NACIONAL\nNOME: TITULAR FICTICIO\nCPF DO TITULAR: {_CPF}\n"
            "VALIDADE: INDETERMINADA", "mesmo_nome.pdf",
        )
        relatorio = self.recuperar()
        estados = {(r["documento_id"], r["campo"]): r["status"] for r in relatorio["estados_campos"]}
        esperados = {
            (rg, "CPF"): "NAO_ENCONTRADO", (cin, "RG"): "NAO_APLICAVEL",
            (cin, "DATA_VALIDADE"): "NAO_APLICAVEL", (cin, "CPF"): "ENCONTRADO",
            (rg, "NOME"): "ENCONTRADO", (rg, "DATA_NASCIMENTO"): "INVALIDO",
            (rg, "DATA_EXPEDICAO"): "AMBIGUO", (rg, "ORGAO_EMISSOR"): "EVIDENCIA_INSUFICIENTE",
        }
        for chave, status in esperados.items():
            self.assertEqual(estados[chave], status)
        self.assertNotIn("NAO_APLICAVEL", {r["status"] for r in relatorio["campos_extraidos"]})
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM campos_extraidos WHERE status='NAO_APLICAVEL'"
        ).fetchone()[0], 1)
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "estados.csv"
            exportar_relatorio_csv(relatorio, arquivo)
            with arquivo.open(encoding="utf-8-sig", newline="") as f:
                linhas = list(csv.reader(f, delimiter=";"))
        self.assertIn(["ESTADOS DOS CAMPOS"], linhas)
        for (ident, campo), status in esperados.items():
            self.assertIn([str(ident), campo, status], linhas)

    def test_cronologia_inconsistente_aparece_na_recuperacao(self):
        self.salvar(f"{_TITULO}\nCAT. HAB.: B\nEMISSAO: 17/04/2033\nVALIDADE: 17/04/2023", "cnh.pdf")
        campos = {r["campo"]: r for r in self.recuperar()["campos_extraidos"]}
        self.assertEqual(campos["CRONOLOGIA_CNH"]["status"], "INCONSISTENCIA_CRONOLOGICA")
        self.assertEqual(campos["DATA_VALIDADE"]["valor"], "17/04/2023")

    def test_conferencia_cnh_rg_cin_so_compara_campos_equivalentes(self):
        for texto, arquivo in ((_ANTIGA, "cnh.pdf"), (_RG_COMPLETO, "rg.pdf"), (_CIN_COMPLETA, "cin.pdf")):
            self.salvar(texto, arquivo)
        resultados = conferir_analise(self.con, self.ident)
        self.assertEqual(len(resultados), 9)
        self.assertEqual({r["campo"] for r in resultados}, {"NOME", "CPF", "DATA_NASCIMENTO"})
        self.assertTrue(all(r["resultado"] == "COMPATIVEL" for r in resultados))

    def test_ausencia_de_cpf_na_cnh_nao_gera_divergencia_com_cin(self):
        self.salvar(f"{_TITULO}\nCAT. HAB.: B\nNOME: TITULAR FICTICIO DA SILVA", "cnh.pdf")
        self.salvar(_CIN_COMPLETA, "cin.pdf")
        resultados = conferir_analise(self.con, self.ident)
        self.assertIn(("CPF", "NAO_CONFERIDO"), {(r["campo"], r["resultado"]) for r in resultados})
        self.assertTrue(all(r["resultado"] != "DIVERGENTE" for r in resultados))


if __name__ == "__main__":
    unittest.main()
