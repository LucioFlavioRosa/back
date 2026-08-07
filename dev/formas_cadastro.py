"""Os payloads do CADASTRO contra os tipos do front, campo a campo.

`dev/formas.py` fazia isso só para os endpoints de RESULTADO. Foi essa lacuna que
deixou passar dois defeitos que derrubam a tela inteira, os dois achados por gente
usando o produto e não por teste:

  - `subs`/`ctss` vinham como LISTA, e o front espera mapa por id;
  - o contrato vinha com `fimConcessao`/`cidadeId`/`coberturaPct`/`paridade`, e o
    front lê `fim`/`cid`/`cob`/`par` — e chama `.trim()` neles SEM guarda, então
    `undefined.trim()` dá "Unexpected Application Error" e nada renderiza.

A regra que os dois violaram é a mesma: campo de ficha é TEXTO para o front. Número
cru quebra igual, porque `.trim()` não existe em number.
"""
import asyncio, os, sys
os.environ["POSTGRES_URL"]="postgresql://otim:otim@localhost:55432/otimizador"; os.environ["SERVICE_BUS_CONN"]=""
sys.path.insert(0,".")
import logging, httpx; logging.disable(logging.WARNING)
from main import app

# Transcrito de `src/cadastro/domain/` — contrato.ts, subbacia.ts, cts.ts, ete.ts.
ESPERADO = {
    "cidade": ["id","nome","fim","cob"],
    "meta":   ["cid","ano","pct"],
    "fator":  ["cid","cob","par"],
    "sub":    ["id","nome","sisId","sistema","jusante","db","params","obrasOverride"],
    "cts":    ["id","nome","subId","sisId","sistema","jusante","db","params","obrasOverride"],
    "par":    ["sub","cts"],
}
# Campos que o front trata como TEXTO editavel (chama .trim()).
TEXTO = {
    "cidade": ["fim","cob"], "meta": ["ano","pct"], "fator": ["cob","par"],
}
f=[]
def ck(n,c,d=""):
    print(f"  {'ok  ' if c else 'FALHA'} {n}{'' if c else '  <- '+d}"); (f.append(n) if not c else None)

async def main():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            ct = (await c.get("/api/unidades/u1/contrato")).json()
            for rot, lista in (("cidade", ct["cidades"]), ("meta", ct["metas"]), ("fator", ct["fator"])):
                if not lista:
                    ck(f"{rot}: ha ao menos um para conferir", False, "lista vazia"); continue
                falta = [k for k in ESPERADO[rot] if k not in lista[0]]
                ck(f"{rot} tem os campos do front", falta==[], f"faltam {falta} — tem {sorted(lista[0])}")
                nao_texto = [k for k in TEXTO[rot] if not isinstance(lista[0].get(k), str)]
                ck(f"{rot}: campos de ficha sao string", nao_texto==[], str(nao_texto))

            sb = (await c.get("/api/unidades/u1/sub-bacias")).json()
            ck("subs e mapa por id", isinstance(sb["subs"], dict), type(sb["subs"]).__name__)
            if sb["subs"]:
                s = next(iter(sb["subs"].values()))
                falta = [k for k in ESPERADO["sub"] if k not in s]
                ck("sub-bacia tem os campos do front", falta==[], f"faltam {falta}")
                nao_txt = [f"{b}.{k}" for b in ("db","params") for k,v in s[b].items() if not isinstance(v,str)]
                ck("db e params sao todos string", nao_txt==[], str(nao_txt[:5]))

            ctss = (await c.get("/api/unidades/u1/cts")).json()
            ck("ctss e mapa por id", isinstance(ctss["ctss"], dict), type(ctss["ctss"]).__name__)
            if ctss["pares"]:
                falta = [k for k in ESPERADO["par"] if k not in ctss["pares"][0]]
                ck("par de CTS tem sub e cts", falta==[], str(falta))

            et = (await c.get("/api/unidades/u1/etes")).json()["etes"]
            ck("etes vem como lista com id", bool(et) and "id" in et[0], str(sorted(et[0]) if et else "vazio"))
    print("\nFALHAS:", f or "nenhuma"); raise SystemExit(1 if f else 0)
asyncio.run(main())
