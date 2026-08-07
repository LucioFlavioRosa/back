"""Bate em todos os endpoints contra o Postgres real e reporta o que quebra."""
import asyncio, json, os, sys
os.environ["POSTGRES_URL"] = "postgresql://otim:otim@localhost:55432/otimizador"
os.environ["SERVICE_BUS_CONN"] = ""
sys.path.insert(0, ".")
import httpx
from main import app

RID, UID, CID, SID, SUB, OBR = "run_teste_1", "u1", "Rio Bonito", "Sistema 38", "b38_1", "lig_b38_1"
GETS = [
    "/api/regionais", f"/api/regionais/r1/unidades", f"/api/unidades/{UID}",
    f"/api/unidades/{UID}/hierarquia", f"/api/unidades/{UID}/contrato",
    f"/api/unidades/{UID}/sub-bacias", f"/api/unidades/{UID}/etes", f"/api/unidades/{UID}/cts",
    f"/api/unidades/{UID}/prontidao",
    "/api/runs", f"/api/runs/{RID}/meta", f"/api/runs/{RID}/painel", f"/api/runs/{RID}/ebitda",
    f"/api/runs/{RID}/ebitda?cidade={CID}", f"/api/runs/{RID}/cidades",
    f"/api/runs/{RID}/cidades/{CID}", f"/api/runs/{RID}/sistemas/{SID}/topologia",
    f"/api/runs/{RID}/subbacias/{SUB}", f"/api/runs/{RID}/obras/{OBR}",
    f"/api/runs/{RID}/status", "/readyz",
]

async def main():
    ok = falhas = 0
    async with app.router.lifespan_context(app):
        t = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:
            for u in GETS:
                r = await c.get(u)
                if r.status_code < 400:
                    ok += 1
                    print(f"  200  {u}")
                else:
                    falhas += 1
                    corpo = r.text[:300].replace("\n", " ")
                    print(f"  {r.status_code}  {u}\n       -> {corpo}")
            # POST /runs
            r = await c.post("/api/runs", json={
                "unidade_id": UID, "nome": "smoke",
                "orcamento": {"2026": 60e6, "2027": 50e6},
                "base_receita": "arrecadada", "usar_cts": True, "foco_cobertura": 1.0})
            print(f"  {r.status_code}  POST /api/runs -> {r.text[:220]}")
    print(f"\n{ok} ok, {falhas} falha(s) nos GET")

asyncio.run(main())
