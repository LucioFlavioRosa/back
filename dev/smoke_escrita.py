"""Escrita do cadastro contra Postgres real: a ficha grava, e a trilha vai junto."""
import asyncio, os, sys
os.environ["POSTGRES_URL"] = "postgresql://otim:otim@localhost:55432/otimizador"
os.environ["SERVICE_BUS_CONN"] = ""
sys.path.insert(0, ".")
import httpx, logging; logging.disable(logging.INFO)
from main import app
from app.infra import db

U, SUB, CID, ETE = "u1", "b38_1", "c_rio", "ete_s38"

async def main():
    falhas = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            # 1. PUT sub-bacia com override e obras
            r = await c.put(f"/api/unidades/{U}/sub-bacias/{SUB}", json={
                "params": {"preco": 1900, "pot": 0.05, "popU": 1200, "popA": 400},
                "db": {"ligU": 350, "ligA": 120},
                "obrasOverride": [{"nome": "Ligação de esgoto", "qtd": 80, "un": "un",
                                   "preco": 1850, "opex": 5580, "tPred": 0, "dur": 6,
                                   "anoObrig": 0, "proibAte": 0, "wacc": None}],
                "overrides": [{"campo": "ligU", "valorAntigo": 300, "valorNovo": 350}],
            })
            print(f"  {r.status_code}  PUT sub-bacia -> {r.text[:80]}")
            if r.status_code >= 400: falhas.append("PUT sub-bacia")

            # 2. PUT contrato (cidade + metas + faixas)
            r = await c.put(f"/api/unidades/{U}/contrato/{CID}", json={
                "cidade": {"nome": "Rio Bonito", "fimConcessao": 2049, "cob": "ligacoes"},
                "metas": [{"ano": 2030, "pct": 0.45}, {"ano": 2035, "pct": 0.6}],
                "fator": [{"coberturaPct": 0.4, "paridade": 0.72}],
                "overrides": [],
            })
            print(f"  {r.status_code}  PUT contrato  -> {r.text[:80]}")
            if r.status_code >= 400: falhas.append("PUT contrato")

            # 3. PUT ETE
            r = await c.put(f"/api/unidades/{U}/etes/{ETE}", json={
                "ete": {"capMod": 300, "capexMod": 140000000, "nova": True},
                "overrides": [{"campo": "capMod", "valorAntigo": 270, "valorNovo": 300}],
            })
            print(f"  {r.status_code}  PUT ETE       -> {r.text[:80]}")
            if r.status_code >= 400: falhas.append("PUT ETE")

            # 4. POST CTS -> 201 com a CTS criada
            r = await c.post(f"/api/unidades/{U}/cts", json={
                "subId": SUB, "cts": {"id": "cts_b38_1", "params": {"preco": 1700}, "db": {"ligU": 90}},
            })
            print(f"  {r.status_code}  POST CTS      -> {r.text[:110]}")
            if r.status_code >= 400: falhas.append("POST CTS")

            # 5. idempotencia: reenviar a MESMA ficha nao pode acumular
            for _ in range(2):
                await c.put(f"/api/unidades/{U}/sub-bacias/{SUB}", json={
                    "params": {"preco": 1900}, "db": {"ligU": 350},
                    "obrasOverride": [{"nome": "Tronco", "qtd": 10, "un": "m", "preco": 100,
                                       "opex": 0, "tPred": 0, "dur": 3, "anoObrig": 0, "proibAte": 0}],
                    "overrides": [{"campo": "ligU", "valorAntigo": 300, "valorNovo": 350}],
                })

            # 6. DELETE CTS
            r = await c.delete(f"/api/unidades/{U}/cts/cts_b38_1")
            print(f"  {r.status_code}  DELETE CTS")
            if r.status_code >= 400: falhas.append("DELETE CTS")

        # ---- conferencia no banco
        print("\n  no banco:")
        for rot, sql in [
            ("trilha da sub-bacia", "SELECT count(*) FROM input.override WHERE tipo='sub-bacia'"),
            ("trilha da ETE",       "SELECT count(*) FROM input.override WHERE tipo='ete'"),
            ("obras da sub-bacia",  "SELECT count(*) FROM input.componentes_subbacias_capex WHERE sub_bacia='b38_1'"),
            ("capex calculado",     "SELECT capex FROM input.componentes_subbacias_capex WHERE sub_bacia='b38_1'"),
            ("metas da cidade",     "SELECT count(*) FROM input.metas_cobertura WHERE cidade_id='c_rio'"),
            ("preco gravado",       "SELECT preco_por_ligacao FROM input.subbacia_operacional WHERE sub_bacia='b38_1'"),
            ("popU preservado",     "SELECT universo_populacao FROM input.subbacia_operacional WHERE sub_bacia='b38_1'"),
            ("CTS apagada",         "SELECT count(*) FROM input.cts_operacional WHERE cts='cts_b38_1'"),
            ("par apagado",         "SELECT count(*) FROM input.subbacia_cts WHERE cts='cts_b38_1'"),
            ("autor da trilha",     "SELECT DISTINCT autor FROM input.override"),
        ]:
            v = await db.buscar(sql)
            print(f"    {rot:22} {list(v[0].values())[0] if v else '(vazio)'}")
    print("\nFALHAS:", falhas or "nenhuma")

asyncio.run(main())
