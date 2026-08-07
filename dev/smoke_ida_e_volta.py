"""Ler uma ficha e salvá-la de volta, sem tradução no meio.

É a operação mais comum do cadastro — abrir, mudar um campo, salvar — e era a que
ninguém testava: todos os smokes montavam o corpo à mão, em pt-BR, em vez de
reenviar o que o `GET` devolveu. Um teste de uso real pegou: o `GET` emitia
`"2497.7"` e a escrita, que exige pt-BR estrito, dava 500 no próprio formato que
tinha acabado de emitir.

Exige a pilha de pé e o `dev/seed.sql` aplicado.
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
            # 1. ida e volta pura: o que veio do GET volta inteiro pelo PUT
            sb = (await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            corpo = {k: sb[k] for k in ("db","params","obrasOverride","versao")}
            corpo["overrides"] = []
            r = await c.put("/api/unidades/u1/sub-bacias/b38_1", json=corpo)
            ck("a ficha lida pode ser salva de volta", r.status_code==200, f"{r.status_code} {r.text[:110]}")

            # 2. e com uma edicao, que e o caso real
            sb = (await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            corpo = {k: sb[k] for k in ("db","params","obrasOverride","versao")}
            corpo["params"] = {**corpo["params"], "preco": "2.500,00"}
            corpo["overrides"] = []
            r = await c.put("/api/unidades/u1/sub-bacias/b38_1", json=corpo)
            ck("editar um campo e salvar funciona", r.status_code==200, f"{r.status_code} {r.text[:110]}")

            sb = (await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            ck("o valor editado voltou em pt-BR", sb["params"]["preco"]=="2.500", repr(sb["params"]["preco"]))
            # A heuristica "tem ponto sem virgula" nao serve: `2.738` tanto pode
            # ser str(float) quanto pt-BR para 2738 — as duas grafias colidem. O
            # invariante que importa e outro, e e exato: TUDO que o GET emite tem
            # de ser aceito pelo parser que o PUT usa. Se algo sair num formato que
            # a escrita nao reconhece, a ida e volta quebra — que foi o defeito.
            from app.infra.repositorios.cadastro_escrita import _PT_BR
            recusados = [
                f"{onde}.{k}={v!r}"
                for onde, d in [("params", sb["params"]), ("db", sb["db"])]
                + [(f"obra{i}", o) for i, o in sb["obrasOverride"].items()]
                for k, v in d.items()
                if v not in (None, "") and k not in ("un", "nome")
                and not _PT_BR.match(str(v))
            ]
            ck("tudo que o GET emite e aceito pelo parser do PUT", recusados == [], str(recusados[:4]))

            # relê a versão: o PUT anterior mudou a ficha, e reusar a versão velha
            # daria 409 antes de o formato ser sequer olhado.
            atual = (await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            corpo = {k: atual[k] for k in ("db","params","obrasOverride","versao")}
            corpo["overrides"] = []
            ruim = {**corpo, "params": {**corpo["params"], "preco": "1.234 hab"}}
            r = await c.put("/api/unidades/u1/sub-bacias/b38_1", json=ruim)
            ck("numero com unidade colada da 422", r.status_code==422, f"{r.status_code} {r.text[:90]}")

            sb = (await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            base = {k: sb[k] for k in ("db","params","obrasOverride","versao")}
            base["overrides"] = []
            neg = {**base, "params": {**base["params"], "vaz": "-10"}}
            r = await c.put("/api/unidades/u1/sub-bacias/b38_1", json=neg)
            ck("vazao negativa e recusada", r.status_code==422, f"{r.status_code} {r.text[:90]}")

            obra_ruim = {**base, "obrasOverride": {**base["obrasOverride"], "0": {**base["obrasOverride"].get("0",{}), "qtd":"abc"}}}
            r = await c.put("/api/unidades/u1/sub-bacias/b38_1", json=obra_ruim)
            ck("quantidade de obra invalida da 422, nao 500", r.status_code==422, f"{r.status_code} {r.text[:90]}")

            # 4. JSON invalido e mensagem legivel
            r = await c.post("/api/runs", content=b'{"unidade_id": "u1"', headers={"content-type":"application/json"})
            ck("JSON truncado tem mensagem legivel",
               r.status_code==422 and "JSON válido" in r.text, f"{r.status_code} {r.text[:80]}")

            r = await c.get("/api/unidades/nao_existe")
            ck("404 de unidade concorda em genero", "encontrada" in r.text, r.text[:60])

            r = await c.post("/api/runs", json={"unidade_id":"u1","nome":"x"*300,"orcamento":{"2026":1e6}})
            ck("nome absurdamente longo e recusado", r.status_code==422, f"{r.status_code} {r.text[:70]}")
    print("\nFALHAS:", f or "nenhuma"); raise SystemExit(1 if f else 0)
asyncio.run(main())
