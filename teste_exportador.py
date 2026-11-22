
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from analise.exportador import exportar_relatorio_csv


def testar():
    print("=== TESTE EXPORTADOR CEUMA ===")

    relatorio = {
        "codigo": "AN-TESTE",
        "data_criacao": "2026-11-20T00:00:00",
        "status": "EM_ANALISE",
        "documentos": [
            {
                "id": 1,
                "arquivo": "identidade_ficticia.pdf",
                "tipo": "DOCUMENTO_IDENTIDADE"
            },
            {
                "id": 2,
                "arquivo": "certidao_ficticia.pdf",
                "tipo": "CERTIDAO_NASCIMENTO"
            }
        ],
        "conferencias": [
            {
                "campo": "NOME",
                "resultado": "DIVERGENTE",
                "observacao": "Documentos 1 e 2"
            },
            {
                "campo": "DATA_NASCIMENTO",
                "resultado": "COMPATIVEL",
                "observacao": "Documentos 1 e 2"
            }
        ],
        "total_documentos": 2,
        "total_conferencias": 2,
        "total_divergencias": 1
    }

    # Pasta temporária: apagada após o teste.
    with TemporaryDirectory() as pasta:
        destino = Path(pasta) / "relatorio_teste.csv"

        exportar_relatorio_csv(
            relatorio,
            destino
        )

        assert destino.exists()

        with destino.open(
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as arquivo:
            linhas = list(
                csv.reader(arquivo, delimiter=";")
            )

        assert ["Codigo da analise", "AN-TESTE"] in linhas
        assert ["NOME", "DIVERGENTE", "Documentos 1 e 2"] in linhas
        assert ["Total de divergencias", "1"] in linhas

        print("Arquivo CSV criado: OK")
        print("Codigo da analise: OK")
        print("Conferencias exportadas: OK")
        print("Total de divergencias: OK")

    print("\nTodos os testes passaram!")


if __name__ == "__main__":
    testar()
