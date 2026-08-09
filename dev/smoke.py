"""Bate em todos os endpoints contra o Postgres real e reporta o que quebra.

O mais barato dos smokes, e por isso o primeiro a rodar: nao confere numero
nenhum, so exige que nada responda 4xx/5xx. Serve para pegar o erro grosso —
coluna renomeada, handler que estoura, rota que sumiu.

Os ids sao DESCOBERTOS pela propria API. Antes eram fixos (`u1`, `run_teste_1`,
`b38_1`, do `dev/seed.sql`) e o script passou a acusar 8 falhas no dia em que o
banco foi carregado com o dado real — falha que parecia do servico e era do teste
olhando para dado que nao existe mais. Um smoke que falha por motivo errado e
pior que nenhum: ensina a ignorar a saida.
"""

import asyncio
import os
import sys
import urllib.parse

os.environ.setdefault("POSTGRES_URL", "postgresql://otim:otim@localhost:55432/otimizador")
os.environ.setdefault("SERVICE_BUS_CONN", "")
sys.path.insert(0, ".")

import httpx  # noqa: E402

from main import app  # noqa: E402

q = urllib.parse.quote


async def descobrir(c: httpx.AsyncClient) -> list[str]:
    """As rotas a testar, com ids que existem NESTE banco."""
    regs = (await c.get("/api/regionais")).json()
    if not regs:
        raise SystemExit("sem regionais: o banco esta vazio (rode dev/recarregar_tudo.py)")
    r = regs[0]["id"]
    u = (await c.get(f"/api/regionais/{r}/unidades")).json()[0]["id"]

    rotas = [
        "/api/regionais",
        f"/api/regionais/{r}/unidades",
        f"/api/unidades/{u}",
        f"/api/unidades/{u}/hierarquia",
        f"/api/unidades/{u}/contrato",
        f"/api/unidades/{u}/sub-bacias",
        f"/api/unidades/{u}/etes",
        f"/api/unidades/{u}/cts",
        f"/api/unidades/{u}/prontidao",
        "/api/runs",
        "/readyz",
    ]

    runs = (await c.get("/api/runs")).json()
    if not runs:
        print("  --   nenhuma rodada publicada: as rotas de RESULTADO ficam de fora")
        return rotas

    # SO as PUBLICADAS: desde que o historico passou a incluir as rodadas em voo
    # (PENDENTE/RODANDO/ERRO), `runs[0]` pode ser uma que ainda nao tem resultado
    # nenhum — e o drill-down abaixo estourava com IndexError. `publicada` existe
    # exatamente para essa distincao.
    publicadas = [r for r in runs if r.get("publicada")]
    if not publicadas:
        print("  --   nenhuma rodada PUBLICADA: as rotas de RESULTADO ficam de fora")
        return rotas
    rid = publicadas[0]["runId"]
    cidades = (await c.get(f"/api/runs/{rid}/cidades")).json()
    cid = cidades[0]["id"]
    sis = (await c.get(f"/api/runs/{rid}/cidades/{q(cid)}")).json()["sistemas"][0]["id"]
    topo = (await c.get(f"/api/runs/{rid}/sistemas/{q(sis)}/topologia")).json()
    sub = topo["nos"][0]["id"]
    obra = next(
        (x["obraId"] for n in topo["nos"] for x in n["componentes"] if x.get("obraId")), None
    )

    rotas += [
        f"/api/runs/{rid}/meta",
        f"/api/runs/{rid}/painel",
        f"/api/runs/{rid}/ebitda",
        f"/api/runs/{rid}/ebitda?cidade={q(cid)}",
        f"/api/runs/{rid}/cidades",
        f"/api/runs/{rid}/cidades/{q(cid)}",
        f"/api/runs/{rid}/sistemas/{q(sis)}/topologia",
        f"/api/runs/{rid}/subbacias/{q(sub)}",
        f"/api/runs/{rid}/status",
    ]
    if obra:
        rotas.append(f"/api/runs/{rid}/obras/{q(obra)}")
    else:
        print("  --   nenhum componente com obraId: /obras/{id} fica de fora")
    return rotas


async def main() -> None:
    ok = falhas = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60
        ) as c:
            for rota in await descobrir(c):
                r = await c.get(rota)
                if r.status_code < 400:
                    ok += 1
                    print(f"  {r.status_code}  {rota}")
                else:
                    falhas += 1
                    print(f"  {r.status_code}  {rota}\n       -> {r.text[:300]}")

            # `POST /runs` da 503 sem Service Bus, e isso e o esperado no local:
            # nao conta como falha, mas aparece para nao passar despercebido.
            reg = (await c.get("/api/regionais")).json()[0]["id"]
            u = (await c.get(f"/api/regionais/{reg}/unidades")).json()[0]["id"]
            r = await c.post(
                "/api/runs",
                json={
                    "unidade_id": u,
                    "nome": "smoke",
                    "orcamento": {"2026": 60e6, "2027": 50e6},
                    "base_receita": "arrecadada",
                    "usar_cts": True,
                    "foco_cobertura": 1.0,
                },
            )
            print(f"  {r.status_code}  POST /api/runs -> {r.text[:200]}")

    print(f"\n{ok} ok, {falhas} falha(s) nos GET")
    raise SystemExit(1 if falhas else 0)


asyncio.run(main())
