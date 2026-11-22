"""Benchmark manual. Execute com Python da .venv e use --modo para escolher o motor."""

def main():
    import argparse
    import hashlib
    import math
    import os
    import statistics
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path
    from collections import Counter
    from contextlib import closing
    sys.path.insert(0,str(Path.cwd()))
    def bloquear_rede(evento, _):
        if evento in {"socket.connect", "socket.sendto", "socket.getaddrinfo",
                      "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"}:
            raise RuntimeError("Rede bloqueada no benchmark.")

    sys.addaudithook(bloquear_rede)
    import numpy as np
    import psutil
    import pymupdf as f
    from PIL import Image, ImageFilter
    from leitor import leitor_pdf as lp
    from leitor.leitor_adaptativo import LeitorAdaptativo, Tesseract, criar_leitor_ocr, FalhaOCR
    from analise.analisador_documento import analisar_documento
    from analise.persistencia import salvar_resultado_analise
    from analise.conferidor_banco import conferir_analise
    from analise.relatorio import recuperar_relatorio
    from banco import conexao, atribuicoes
    from teste_analise_familiar import cpf_sintetico
    p=psutil.Process()
    mem = []
    parar = threading.Event()
    def observar():
        while not parar.is_set():
            rss=p.memory_info().rss
            for filho in p.children():
                try: rss+=filho.memory_info().rss
                except psutil.Error: pass
            mem.append((rss,psutil.virtual_memory().available))
            parar.wait(.025)
    threading.Thread(target=observar,daemon=True).start()
    cpf = cpf_sintetico(1)
    outro = cpf_sintetico(2)
    casos=[
     ("DOCUMENTO_IDENTIDADE","identidade",["CARTEIRA DE IDENTIDADE","SECRETARIA DE SEGURANCA PUBLICA","REGISTRO GERAL: 00012345","NOME: TITULAR FICTICIO","DATA DE NASCIMENTO: 01/01/2000","CPF DO TITULAR: "+cpf],{"NOME":"TITULAR FICTICIO","RG":"00012345","CPF":cpf}),
     ("CIN","identidade",["CARTEIRA DE IDENTIDADE NACIONAL","REGISTRO GERAL CPF: "+cpf,"NOME: TITULAR FICTICIO","DATA DE NASCIMENTO: 01/01/2000"],{"NOME":"TITULAR FICTICIO","CPF":cpf}),
     ("CNH","cnh",["CARTEIRA NACIONAL DE HABILITACAO","NOME: TITULAR FICTICIO","CPF DO TITULAR: "+cpf,"CATEGORIA: B","NASCIMENTO: 01/01/2000"],{"NOME":"TITULAR FICTICIO","CPF":cpf,"CATEGORIA_CNH":"B"}),
     ("CERTIDAO_NASCIMENTO","certidao_nascimento",["CERTIDAO DE NASCIMENTO","REGISTRO CIVIL DAS PESSOAS NATURAIS","NOME DO REGISTRADO: TITULAR FICTICIO","DATA DE NASCIMENTO: 01/01/2000","LIVRO: A-0001","FOLHA: 002","TERMO: 00003"],{"NOME":"TITULAR FICTICIO","DATA_NASCIMENTO":"01/01/2000"}),
     ("CTPS_FISICA","ctps_fisica",["CARTEIRA DE TRABALHO E PREVIDENCIA SOCIAL","QUALIFICACAO CIVIL","NOME: TITULAR FICTICIO","NUMERO DA CTPS: 000123","SERIE: 0004","CPF DO TITULAR: "+cpf,"CPF DO REPRESENTANTE: "+outro,"CONTRATO DE TRABALHO","EMPREGADOR: EMPRESA FICTICIA","ADMISSAO: 01/01/2021","CONTRATO DE TRABALHO","EMPREGADOR: EMPRESA FICTICIA","ADMISSAO: 02/02/2022"],{"NOME":"TITULAR FICTICIO","NUMERO_CTPS":"000123","CPF":cpf}),
    ]
    def gerar(caminho,caso,estilo,angulo=0):
        with f.open() as fonte, f.open() as saida:
            pg=fonte.new_page(width=470,height=550)
            for i,l in enumerate(caso[2]):
                pg.insert_text((25,50+i*32),l,fontsize=12 if estilo=="degradado" else 14,color=(.35,.35,.35) if estilo=="degradado" else (0,0,0))
            if estilo=="digital":
                fonte.save(caminho)
                return
            if estilo=="colunas":
                pg.draw_rect(f.Rect(305,185,447,260),width=.5)
                pg.draw_line((375,185),(375,260),width=.5)
                pg.insert_text((310,201),"OBSERVACAO",fontsize=9)
                pg.insert_text((382,201),"FIXTURE",fontsize=9)
            area=f.Rect(0,0,470,482) if estilo=="misto" else pg.rect
            pix=pg.get_pixmap(matrix=f.Matrix(2,2).prerotate(angulo),clip=area)
            pg2=saida.new_page(width=pix.width/2,height=(pix.height/2+55 if estilo=="misto" else pix.height/2))
            png=pix.tobytes("png")
            if estilo=="degradado":
                import io
                im=Image.open(io.BytesIO(png)).filter(ImageFilter.GaussianBlur(.65))
                arq = io.BytesIO()
                im.save(arq, format="JPEG", quality=42)
                png = arq.getvalue()
            pg2.insert_image(f.Rect(0,0,pix.width/2,pix.height/2),stream=png)
            if estilo=="misto":
                pg2.insert_text((20,pg2.rect.height-22),"RODAPE DIGITAL DE TESTE",fontsize=12)
            saida.save(caminho)
    def somar(c, d, r, ang):
        tipo,chave,_,esperado=c
        met=Counter()
        met["tipo_errado"]+=r["identificacao"]["tipo"]!=tipo
        campos=r["campos"].get(chave,{})
        for k,v in esperado.items():
            campo=campos.get(k,{})
            if campo.get("valor")==v: met["corretos"]+=1
            elif campo.get("status") in {"AMBIGUO","INVALIDO","EVIDENCIA_INSUFICIENTE"}: met["revisao"]+=1
            elif campo.get("valor") is None: met["ausentes"]+=1
            else:
                met["incorretos"]+=1
                if k=="CPF": met["titularidade_errada"]+=1
        if tipo=="CTPS_FISICA":
            contratos=r.get("ctps_fisica",{}).get("contratos",[])
            datas=[(x["campos"]["EMPREGADOR"]["valor"],x["campos"]["ADMISSAO"]["valor"]) for x in contratos]
            if datas!=[("EMPRESA FICTICIA","01/01/2021"),("EMPRESA FICTICIA","02/02/2022")]:
                met["associacao_errada" if len(datas)==2 and all(all(v) for v in datas) else "contratos_incompletos"]+=1
        if ang:
            met["orientacao_errada"]+=not any(o["angulo_aplicado"]==ang for o in d["paginas"][0]["orientacoes"])
        met["docs_pendentes"]+=bool(r.get("pendencias_leitura"))
        for pg in d["paginas"]:
            met.update(pg.get("diagnostico_ocr",{}).get("metricas",{}))
            for motivo in pg.get("diagnostico_ocr",{}).get("pendencias",[]):
                met[motivo]+=1
        return met
    parser = argparse.ArgumentParser(description="Benchmark offline em PDFs e bancos sinteticos.")
    parser.add_argument("--modo", choices=("adaptativo", "tesseract", "paddle"), required=True)
    parser.add_argument("--orientacao", choices=("paddle", "pdf", "osd"), default="paddle")
    args = parser.parse_args()
    modo = args.modo
    os.environ["CEUMA_OCR_ORIENTACAO"] = args.orientacao
    os.environ["CEUMA_OCR_MODO"]=modo
    def mib(b): return round(b/1048576)
    def resumo(amostras):
        return [round(statistics.median(amostras),4),round(sorted(amostras)[math.ceil(.95*len(amostras))-1],4),round(max(amostras),4)]
    with tempfile.TemporaryDirectory() as pasta:
        raiz=Path(pasta)
        caminhos=[]
        specs=[("raster",90),("raster",180),("raster",270),("degradado",0),("misto",0),
               ("digital",0),("raster",0),("digital",0),("colunas",0),("raster",0)]
        for i,(estilo,ang) in enumerate(specs):
            c=casos[i%5]; caminho=raiz/f"{i:03}.pdf"; gerar(caminho,c,estilo,ang)
            caminhos.append((caminho,c,ang))
        orig={x[0]:hashlib.sha256(x[0].read_bytes()).digest() for x in caminhos}
        motor=criar_leitor_ocr()
        t=time.perf_counter()
        # Mede a frio uma página frontal e depois a preparação explícita da orientação.
        frontal=raiz/"frontal.pdf"
        gerar(frontal,casos[0],"raster")
        orig[frontal]=hashlib.sha256(frontal.read_bytes()).digest()
        d=lp.ler_pdf(frontal,leitor_ocr=motor)
        print(modo,"frio",round(time.perf_counter()-t,3),d["paginas"][0].get("diagnostico_ocr"),flush=True)
        if isinstance(motor, LeitorAdaptativo):
            inicio=time.perf_counter()
            motor.preaquecer_orientacao()
            print(modo,"preaquecer",round(time.perf_counter()-inicio,3),flush=True)
        candidatos=(5,) if modo=="paddle" or args.orientacao=="osd" else (5,10,20)
        for n in candidatos:
            cpu=criar_leitor_ocr()
            if isinstance(cpu,LeitorAdaptativo): cpu.paddle.orientador=motor.paddle.orientador
            else: cpu.ocr=motor.ocr; cpu.orientador=motor.orientador
            amostras=[]; m=Counter(); mem.clear()
            t=time.perf_counter()
            with closing(conexao.conectar_banco(raiz/f"{n}.db")) as banco:
                conexao.criar_tabelas(banco)
                codigo=conexao.criar_analise(banco,modo="FAMILIAR" if n!=5 else "INDIVIDUAL")
                pessoa=atribuicoes.criar_pessoa(banco,1) if n!=5 else None
                segunda=atribuicoes.criar_pessoa(banco,1) if n!=5 else None
                for i in range(n):
                    # Os 10 primeiros cobrem os casos desafiadores. Completa o lote com páginas simples.
                    j=i if n<20 else (i if i<10 else 5+(i%5))
                    caminho,c,ang=caminhos[j]
                    inicio=time.perf_counter(); d=lp.ler_pdf(caminho,leitor_ocr=cpu)
                    r=analisar_documento(d)
                    salvo=salvar_resultado_analise(banco,1,r)
                    if pessoa: atribuicoes.atribuir_documento(banco,salvo["documento_id"],pessoa if i%2==0 else segunda)
                    m.update(somar(c,d,r,ang)); amostras.append(time.perf_counter()-inicio)
                resultados=conferir_analise(banco,1)
                with banco: banco.execute("UPDATE analises SET status='CONCLUIDA'")
                rel,_=recuperar_relatorio(banco,codigo)
                assert banco.execute("PRAGMA foreign_key_check").fetchall()==[]
                print(modo,n,round(time.perf_counter()-t,3),resumo(amostras),dict(m),
                      "rss_disponivel",mib(max(x[0] for x in mem)),mib(min(x[1] for x in mem)),
                      "rel",len(rel["documentos"]),rel["total_pares_elegiveis"],len(resultados),flush=True)
        if modo=="adaptativo" and args.orientacao=="paddle":
            caminho,c,ang=caminhos[3]
            fdoc=f.open(caminho);pix=fdoc[0].get_pixmap(matrix=f.Matrix(2,2));im=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
            tess=Tesseract()
            for psm in (3,4,6,7,11):
                t=time.perf_counter()
                try:
                    linhas=tess.reconhecer(im,psm,2)
                    print("psm",psm,round(time.perf_counter()-t,3),len(linhas),flush=True)
                except FalhaOCR as e: print("psm",psm,str(e),flush=True)
            fdoc.close()
            for orient in ("pdf","osd"):
                for caminho,c,ang in caminhos[:3]:
                    cpu=LeitorAdaptativo(orientacao=orient)
                    t=time.perf_counter(); d=lp.ler_pdf(caminho,leitor_ocr=cpu);r=analisar_documento(d)
                    print("orient",orient,ang,round(time.perf_counter()-t,3),dict(somar(c,d,r,ang)),flush=True)
        assert all(hashlib.sha256(a.read_bytes()).digest()==v for a,v in orig.items())
    parar.set()

if __name__ == "__main__":
    main()
