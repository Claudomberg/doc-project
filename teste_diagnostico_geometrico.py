import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from analise.associador_espacial import associar_rotulo_valor, reconstruir_rotulos
from analise.diagnostico_geometrico import diagnosticar_geometria
from analise.extrator_campos import extrair_datas_classificadas_documento, extrair_rg_documento


def linha(texto, caixa):
    return {"texto": texto, "caixa": caixa}


def documento_ficticio(linhas):
    return {"paginas": [{"numero": 1, "metodo": "OCR", "linhas_ocr": linhas,
                        "texto": "\n".join(item["texto"] for item in linhas)}]}


class TesteDiagnosticoGeometrico(unittest.TestCase):
    def test_ausencia_de_rotulo_e_candidato(self):
        relatorio = diagnosticar_geometria([linha("CAMPO FICTICIO", [0, 0, 100, 10])])
        for campo in ("DATA_EXPEDICAO", "RG"):
            self.assertEqual(relatorio[campo], [
                {"decisao": "ROTULOS_VALIDOS", "quantidade": 0},
                {"decisao": "CANDIDATOS_VALIDOS", "quantidade": 0},
                {"decisao": "SEM_ROTULO"},
                {"decisao": "SEM_CANDIDATO"},
            ])

    def test_caixas_invalidas_e_poligono(self):
        relatorio = diagnosticar_geometria([
            linha("EXPEDICAO", [0, 0, 0, 10]),
            linha("01/01/2042", [0, 20, float("inf"), 30]),
            {"texto": "AUXILIAR", "poligono": [[0]]},
            {"texto": "RG", "poligono": [[0, 0], [100, 0], [100, 10], [0, 10]]},
            linha("00000000", [0, 20, 100, 30]),
        ])
        self.assertEqual(relatorio["ENTRADA"], [
            {"decisao": "CAIXA_INVALIDA", "id_linha": indice} for indice in range(3)
        ])
        self.assertIn({"decisao": "CAIXA_INVALIDA", "id_linha": 0, "tipo": "ROTULO"},
                      relatorio["DATA_EXPEDICAO"])
        self.assertIn({"decisao": "CAIXA_INVALIDA", "id_linha": 1, "tipo": "CANDIDATO"},
                      relatorio["DATA_EXPEDICAO"])
        self.assertIn({"decisao": "ASSOCIADO"}, relatorio["RG"])

    def test_limite_de_24_pixels_entre_fragmentos(self):
        for distancia in (23, 24, 25):
            for eixo in ("horizontal", "vertical"):
                with self.subTest(distancia=distancia, eixo=eixo):
                    geral = ([50 + distancia, 0, 130, 10] if eixo == "horizontal"
                             else [0, 10 + distancia, 50, 20 + distancia])
                    eventos = diagnosticar_geometria([
                        linha("REGISTRO", [0, 0, 50, 10]), linha("GERAL", geral),
                        linha("00000000", [0, 50, 100, 60]),
                    ])["RG"]
                    if distancia <= 24:
                        self.assertIn({"decisao": "ASSOCIADO"}, eventos)
                    else:
                        rejeicao = next(e for e in eventos
                                        if e["decisao"] == "FRAGMENTOS_FORA_DA_PROXIMIDADE")
                        self.assertEqual(rejeicao["ids_fragmentos"], (0, 1))
                        self.assertEqual(rejeicao[f"distancia_{eixo}"], 25)
                        self.assertIn({"decisao": "SEM_ROTULO"}, eventos)

    def test_geometria_do_par_para_expedicao_e_rg(self):
        casos = (
            ([0, 109, 100, 119], "PAR_SELECIONADO"),
            ([0, 110, 100, 120], "PAR_SELECIONADO"),
            ([0, 111, 100, 121], "DISTANCIA_EXCEDIDA"),
            ([79, 20, 179, 30], "PAR_SELECIONADO"),
            ([80, 20, 180, 30], "PAR_SELECIONADO"),
            ([81, 20, 181, 30], "SOBREPOSICAO_HORIZONTAL_INSUFICIENTE"),
            ([105, 0, 205, 10], "PAR_SELECIONADO"),
            ([0, 9, 100, 19], "SOBREPOSICAO_VERTICAL"),
            ([0, 10, 100, 20], "PAR_SELECIONADO"),
        )
        for campo, rotulo, valor in (("RG", "RG", "00000000"),
                                      ("DATA_EXPEDICAO", "EXPEDICAO", "01/01/2042")):
            for caixa, decisao in casos:
                with self.subTest(campo=campo, caixa=caixa):
                    eventos = diagnosticar_geometria([
                        linha(rotulo, [0, 0, 100, 10]), linha(valor, caixa),
                    ])[campo]
                    par = next(e for e in eventos if e["decisao"] == decisao)
                    self.assertEqual(par["ids_rotulo"], (0,))
                    self.assertEqual(par["id_valor"], 1)
                    self.assertEqual(par["caixa_valor"], tuple(caixa))
                    if decisao == "PAR_SELECIONADO":
                        self.assertEqual(par["eixo"], "HORIZONTAL" if caixa[0] >= 100 else "VERTICAL")
                    self.assertEqual({"decisao": "ASSOCIADO"} in eventos,
                                     decisao == "PAR_SELECIONADO")

    def test_limites_da_associacao_a_direita(self):
        casos = (
            ([132, 5, 232, 15], "PAR_SELECIONADO", 32, 0.50),
            ([133, 5, 233, 15], "DISTANCIA_HORIZONTAL_EXCEDIDA", 33, 0.50),
            ([132, 6, 232, 16], "ALINHAMENTO_VERTICAL_INSUFICIENTE", 32, 0.40),
        )
        for caixa, decisao, distancia, fracao in casos:
            with self.subTest(decisao=decisao):
                eventos = diagnosticar_geometria([
                    linha("RG", [0, 0, 100, 10]),
                    linha("00000000", caixa),
                ])["RG"]
                par = next(e for e in eventos if e["decisao"] == decisao)
                self.assertEqual(par["eixo"], "HORIZONTAL")
                self.assertEqual(par["distancia_horizontal"], distancia)
                self.assertEqual(par["sobreposicao_vertical"], fracao)
                self.assertEqual({"decisao": "ASSOCIADO"} in eventos, decisao == "PAR_SELECIONADO")

    def test_valor_a_direita_precisa_corresponder_ao_padrao(self):
        eventos = diagnosticar_geometria([
            linha("RG", [0, 0, 100, 10]), linha("NUMERO 00000000", [110, 0, 210, 10]),
        ])["RG"]
        self.assertIn({"decisao": "SEM_CANDIDATO"}, eventos)
        self.assertNotIn({"decisao": "ASSOCIADO"}, eventos)

    def test_valores_concorrentes_na_horizontal_e_entre_eixos(self):
        for caixa in ([130, 0, 230, 10], [0, 80, 100, 90]):
            with self.subTest(caixa=caixa):
                eventos = diagnosticar_geometria([
                    linha("RG", [0, 0, 100, 10]),
                    linha("00000000", [101, 0, 201, 10]),
                    linha("00000001", caixa),
                ])["RG"]
                self.assertIn({"decisao": "AMBIGUIDADE_ENTRE_VALORES", "ids_valores": (1, 2)},
                              eventos)
                self.assertNotIn({"decisao": "ASSOCIADO"}, eventos)

    def test_rotulos_independentes_concorrem_pelo_mesmo_valor(self):
        for caixa in ([5, 10, 95, 20], [110, 0, 210, 10]):
            with self.subTest(caixa=caixa):
                eventos = diagnosticar_geometria([
                    linha("REGISTRO GERAL", [0, 10, 100, 20]),
                    linha("RG", caixa),
                    linha("00000000", [110, 10, 210, 20]),
                ])["RG"]
                rejeicao = next(e for e in eventos if e["decisao"] == "AMBIGUIDADE_ENTRE_ROTULOS")
                self.assertEqual(set(rejeicao["ids_rotulos"]), {(0,), (1,)})
                self.assertNotIn({"decisao": "ASSOCIADO"}, eventos)

    def test_rotulos_reconstruido_e_curto_na_mesma_evidencia(self):
        eventos = diagnosticar_geometria([
            linha("DATA DE", [0, 0, 80, 12]),
            linha("EXPEDICAO", [0, 11, 100, 23]),
            linha("01/01/2042", [110, 11, 210, 23]),
        ])["DATA_EXPEDICAO"]
        par = next(e for e in eventos if e["decisao"] == "PAR_SELECIONADO")
        self.assertEqual(par["ids_rotulo"], (0, 1))

    def test_sobreposicao_de_fragmentos_na_mesma_coluna(self):
        for sobreposicao in (1, 2, 3):
            with self.subTest(sobreposicao=sobreposicao):
                linhas = [
                    linha("REGISTRO", [0, 0, 70, 18]),
                    linha("GERAL", [0, 18 - sobreposicao, 60, 36 - sobreposicao]),
                ]
                rotulos = reconstruir_rotulos(linhas, ["REGISTRO GERAL"])
                self.assertEqual(bool(rotulos), sobreposicao <= 2)

    def test_tolerancia_de_fragmentos_configuravel(self):
        linhas = [
            linha("REGISTRO", [0, 0, 70, 18]), linha("GERAL", [0, 17, 60, 35]),
            linha("00000000", [0, 42, 100, 54]),
        ]
        self.assertEqual(reconstruir_rotulos(linhas, ["REGISTRO GERAL"],
                                            tolerancia_sobreposicao_fragmentos=0), [])
        self.assertIsNone(associar_rotulo_valor(
            linhas, ["REGISTRO GERAL"], r"\d{8}", tolerancia_sobreposicao_fragmentos=0,
        ))
        self.assertIsNotNone(associar_rotulo_valor(linhas, ["REGISTRO GERAL"], r"\d{8}"))

    def test_fragmentos_sobrepostos_em_colunas_diferentes(self):
        for caixa in ([130, 17, 200, 35], [65, 17, 125, 35]):
            with self.subTest(caixa=caixa):
                linhas = [linha("REGISTRO", [0, 0, 70, 18]), linha("GERAL", caixa)]
                self.assertEqual(reconstruir_rotulos(linhas, ["REGISTRO GERAL"]), [])

    def test_extratores_com_geometria_ficticia_equivalente(self):
        documento = documento_ficticio([
            linha("REGISTRO", [0, 0, 70, 18]),
            linha("DATA DE", [200, 2, 270, 22]),
            linha("01/01/2042", [313, 0, 430, 31]),
            linha("GERAL", [0, 17, 60, 35]),
            linha("EXPEDICAO", [200, 19, 288, 39]),
            linha("00000000", [0, 45, 100, 60]),
        ])
        self.assertEqual(extrair_datas_classificadas_documento(documento)[0]["campo"],
                         "DATA_EXPEDICAO")
        self.assertEqual(extrair_rg_documento(documento)["status"], "ENCONTRADO")

    def test_limite_de_12_pixels_entre_valores_e_rotulos(self):
        for diferenca in (11, 12, 13):
            for concorrencia in ("VALORES", "ROTULOS"):
                with self.subTest(diferenca=diferenca, concorrencia=concorrencia):
                    linhas = [
                        linha("RG", [0, 0, 100, 10]), linha("00000000", [0, 30, 100, 40]),
                    ]
                    if concorrencia == "VALORES":
                        linhas.append(linha("00000001", [0, 30 + diferenca, 100, 40 + diferenca]))
                    else:
                        linhas.append(linha("RG", [0, diferenca, 100, 10 + diferenca]))
                    eventos = diagnosticar_geometria(linhas)["RG"]
                    decisao = {e["decisao"] for e in eventos}
                    self.assertEqual(f"AMBIGUIDADE_ENTRE_{concorrencia}" in decisao, diferenca < 12)
                    self.assertEqual("ASSOCIADO" in decisao, diferenca >= 12)

    def test_validacao_final_e_saida_sem_dados_pessoais(self):
        dados_ficticios = ("00000000191", "31/02/2042", "PESSOA FICTICIA", "EXPEDICAO")
        saida = io.StringIO()
        with redirect_stdout(saida), redirect_stderr(saida):
            relatorio = diagnosticar_geometria([
                linha("RG", [0, 0, 100, 10]),
                linha(dados_ficticios[0], [0, 20, 100, 30]),
                linha(dados_ficticios[3], [200, 0, 300, 10]),
                linha(dados_ficticios[1], [200, 20, 300, 30]),
                linha(dados_ficticios[2], [100, 40, 200, 50]),
            ])
        self.assertEqual(saida.getvalue(), "")
        self.assertIn({"decisao": "CANDIDATO_REJEITADO_COMO_CPF"}, relatorio["RG"])
        self.assertIn({"decisao": "DATA_INVALIDA"}, relatorio["DATA_EXPEDICAO"])
        self.assertNotIn({"decisao": "ASSOCIADO"}, relatorio["RG"])
        self.assertNotIn({"decisao": "ASSOCIADO"}, relatorio["DATA_EXPEDICAO"])
        serializado = json.dumps(relatorio, allow_nan=False)
        for dado in dados_ficticios[:3] + ('"texto"', '"rotulo"', '"valor"', '"trecho_origem"'):
            self.assertNotIn(dado, serializado)

    def test_ambiguidade_entre_categorias_de_data(self):
        eventos = diagnosticar_geometria([
            linha("NASCIMENTO", [0, 0, 100, 10]),
            linha("EXPEDICAO", [0, 20, 100, 30]),
            linha("01/01/2042", [0, 40, 100, 50]),
        ])["DATA_EXPEDICAO"]
        self.assertIn({"decisao": "AMBIGUIDADE_ENTRE_CATEGORIAS", "quantidade": 2}, eventos)
        self.assertNotIn({"decisao": "ASSOCIADO"}, eventos)


if __name__ == "__main__":
    unittest.main()
