"""O recorte por unidade — provado com DUAS unidades no banco.

Com uma unidade so, toda consulta sem filtro parece correta: nao ha o que vazar.
Foi assim que `GET /etes` e `GET /cts` passaram meses trazendo o banco inteiro, e
que `PUT /etes/{id}` aceitava id de qualquer unidade.

Exige `dev/seed.sql` E `dev/seed_u2.sql` aplicados.
"""
import asyncio, os, sys
os.environ["POSTGRES_URL"]="postgresql://otim:otim@localhost:55432/otimizador"; os.environ["SERVICE_BUS_CONN"]=""
sys.path.insert(0,".")
import logging, httpx; logging.disable(logging.WARNING)
from main import app
falhas=[]
def ck(n,c,d=""):
    print(f"  {'ok  ' if c else 'FALHA'} {n}{'' if c else '  <- '+d}"); (falhas.append(n) if not c else None)
async def main():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            e1=(await c.get("/api/unidades/u1/etes")).json()["etes"]
            e2=(await c.get("/api/unidades/u2/etes")).json()["etes"]
            ck("GET etes de u1 traz so a dela", [x["id"] for x in e1]==["ete_s38"], str([x["id"] for x in e1]))
            ck("GET etes de u2 traz so a dela", [x["id"] for x in e2]==["ete_s99"], str([x["id"] for x in e2]))
            c1=(await c.get("/api/unidades/u1/cts")).json()
            ck("GET cts de u1 traz so a dela", [x["cts"] for x in c1["pares"]]==["cts_u1"], str(c1["pares"]))
            u1=(await c.get("/api/unidades/u1")).json()["resumo"]
            ck("contadores da capa nao somam a outra unidade", u1["subBacias"]==1, str(u1))
            r=await c.put("/api/unidades/u2/etes/ete_s38", json={"ete":{"capMod":1},"overrides":[]})
            ck("PUT em ETE de outra unidade e recusado", r.status_code==404, f"deu {r.status_code}")
            r=await c.put("/api/unidades/u1/etes/ete_s38", json={"ete":{"capMod":999},"overrides":[]})
            ck("PUT na ETE propria funciona", r.status_code==200, f"deu {r.status_code} {r.text[:70]}")
            r=await c.put("/api/unidades/u1/etes/nao_existe", json={"ete":{"capMod":1},"overrides":[]})
            ck("PUT em ETE inexistente da 404", r.status_code==404, f"deu {r.status_code}")
            r=await c.put("/api/unidades/u1/etes/b38_1", json={"ete":{"capMod":1},"overrides":[]})
            ck("sub-bacia nao passa pela rota de ETE", r.status_code==404, f"deu {r.status_code}")
    print("\nFALHAS:", falhas or "nenhuma"); raise SystemExit(1 if falhas else 0)
asyncio.run(main())
