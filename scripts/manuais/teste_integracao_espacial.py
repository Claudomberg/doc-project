"""Execute na raiz do Projeto CEUMA após substituir analise/extrator_campos.py."""
from ceuma.extracao.campos import extrair_datas_classificadas_documento, extrair_rg_documento


def linha(texto, x1, y1, x2, y2):
    return {"texto": texto, "caixa": [x1, y1, x2, y2], "confianca": 0.99}


def doc(texto, linhas):
    return {"paginas": [{"numero": 1, "metodo": "OCR", "texto": texto, "linhas_ocr": linhas}]}


caso = doc("DATA DE\nNASCIMENTO\n12/03/2004", [
    linha("DATA DE", 20, 10, 110, 25),
    linha("NASCIMENTO", 20, 29, 125, 44),
    linha("12/03/2004", 20, 52, 125, 68),
])
assert extrair_datas_classificadas_documento(caso)[0]["campo"] == "DATA_NASCIMENTO"
print("Data fragmentada associada: OK")

caso = doc("12/03/2004", [linha("DATA DE NASCIMENTO", 20, 10, 160, 25), linha("12/03/2004", 20, 500, 125, 520)])
assert extrair_datas_classificadas_documento(caso)[0]["campo"] == "DATA_NAO_CLASSIFICADA"
print("Data distante preservada como nao classificada: OK")

caso = doc("REGISTRO\nGERAL\n12345678", [
    linha("REGISTRO", 20, 10, 110, 25),
    linha("GERAL", 20, 29, 90, 44),
    linha("12345678", 20, 52, 110, 68),
])
assert extrair_rg_documento(caso)["valor"] == "12345678"
print("RG fragmentado associado: OK")

caso = doc("RG\n12345678", [])
assert extrair_rg_documento(caso)["valor"] == "12345678"
print("Extracao textual anterior preservada: OK")

caso = doc("12/03/2004", [])
assert extrair_datas_classificadas_documento(caso)[0]["campo"] == "DATA_NAO_CLASSIFICADA"
print("Sem coordenadas, comportamento anterior preservado: OK")
print("Todos os testes passaram!")
