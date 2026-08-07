"""Versao por ficha (409) e a identidade da unidade no resultado.

O seed passa a gravar `otim_meta.regional='Litoral 1'` — o NOME, que e o que o job
realmente publica (`otimizador_capex_v62.py:1117`). Com `'u1'`, como estava, os
dois problemas ficavam invisiveis.
"""
import asyncio, os, sys
os.environ["POSTGRES_URL"]="postgresql://otim:otim@localhost:55432/otimizador"; os.environ["SERVICE_BUS_CONN"]=""
sys.path.insert(0,".")
import logging, httpx; logging.disable(logging.WARNING)
from main import app
f=[]
def ck(n,c,d=""):
    print(f"  {'ok  ' if c else 'FALHA'} {n}{'' if c else '  <- '+d}"); (f.append(n) if not c else None)
async def main():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            sb=(await c.get("/api/unidades/u1/sub-bacias")).json()
            v1=sb["subs"]["b38_1"]["versao"]
            ck("a ficha vem com versao", bool(v1), str(v1))

            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json={"versao":v1,"params":{"preco":"1.900,00"},"overrides":[]})
            ck("versao correta grava", r.status_code==200, f"{r.status_code} {r.text[:70]}")

            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json={"versao":v1,"params":{"preco":"2.500,00"},"overrides":[]})
            ck("versao velha da 409", r.status_code==409, f"{r.status_code} {r.text[:90]}")

            v2=(await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]["versao"]
            ck("a versao mudou apos gravar", v2!=v1, f"{v1} -> {v2}")
            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json={"versao":v2,"params":{"preco":"2.500,00"},"overrides":[]})
            ck("com a versao nova, grava", r.status_code==200, f"{r.status_code}")

            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json={"params":{"preco":"2.600,00"},"overrides":[]})
            ck("cliente sem versao continua passando", r.status_code==200, f"{r.status_code}")

            # duas gravacoes SIMULTANEAS com a mesma versao: so uma pode vencer
            v3=(await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]["versao"]
            r1,r2=await asyncio.gather(
                c.put("/api/unidades/u1/sub-bacias/b38_1", json={"versao":v3,"params":{"preco":"1.111,00"},"overrides":[]}),
                c.put("/api/unidades/u1/sub-bacias/b38_1", json={"versao":v3,"params":{"preco":"2.222,00"},"overrides":[]}))
            ck("gravacoes simultaneas: uma 200, uma 409",
               sorted([r1.status_code,r2.status_code])==[200,409], f"{r1.status_code}/{r2.status_code}")

            ete=(await c.get("/api/unidades/u1/etes")).json()["etes"]
            cid=(await c.get("/api/unidades/u1/contrato")).json()["cidades"]
            ck("ETE e cidade tambem tem versao",
               all("versao" in x for x in ete+cid), f"etes={len(ete)} cidades={len(cid)}")

            # ---- identidade da unidade
            m=(await c.get("/api/runs/run_teste_1/meta")).json()
            ck("unidadeId volta o ID e unidadeNome o NOME",
               m["unidadeId"]=="u1" and m["unidadeNome"]=="Litoral 1", f"{m['unidadeId']} / {m['unidadeNome']}")
            h=(await c.get("/api/runs?unidade=u1")).json()
            ck("filtro ?unidade=<id> encontra a rodada", len(h)==1, f"{len(h)} rodada(s)")
            h=(await c.get("/api/runs?unidade=u9")).json()
            ck("filtro por unidade inexistente volta vazio", len(h)==0, f"{len(h)}")
    print("\nFALHAS:", f or "nenhuma"); raise SystemExit(1 if f else 0)
asyncio.run(main())
