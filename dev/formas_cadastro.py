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

# Unidade a conferir: use uma com DADO REAL (dev/rodar_simulacao_real.py) — o seed
# nao tem null em lugar nenhum, e foi por isso que essa classe de defeito passou.
U = os.environ.get("UNIDADE", "u1")

# Transcrito de `src/cadastro/domain/` — contrato.ts, subbacia.ts, cts.ts, ete.ts.
ESPERADO = {
    "db":      ["fat","arr","ligU","ligA","ligN","ligUInd","ligAInd","fatInd","arrInd",
                "ecoU","ecoA","ecoN","ticket"],
    "unidReg": ["rid","rnome","uid","unome","waccMedio"],
    "supH":    ["id","nome"],
    "cidadeH": ["id","nome","supId"],
    "sistemaH":["id","nome","cidId"],
    "topo":    ["sis","id","nome","jus"],
    "ete":     ["id","sub","cidId","nova","capMod","capexMod","opexMod","tExec",
                "capNom","vazOp","terreno","modulos","wacc"],
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
            ct = (await c.get(f"/api/unidades/{U}/contrato")).json()
            for rot, lista in (("cidade", ct["cidades"]), ("meta", ct["metas"]), ("fator", ct["fator"])):
                if not lista:
                    ck(f"{rot}: ha ao menos um para conferir", False, "lista vazia"); continue
                falta = [k for k in ESPERADO[rot] if k not in lista[0]]
                ck(f"{rot} tem os campos do front", falta==[], f"faltam {falta} — tem {sorted(lista[0])}")
                nao_texto = [k for k in TEXTO[rot] if not isinstance(lista[0].get(k), str)]
                ck(f"{rot}: campos de ficha sao string", nao_texto==[], str(nao_texto))

            sb = (await c.get(f"/api/unidades/{U}/sub-bacias")).json()
            ck("subs e mapa por id", isinstance(sb["subs"], dict), type(sb["subs"]).__name__)
            if sb["subs"]:
                s = next(iter(sb["subs"].values()))
                falta = [k for k in ESPERADO["sub"] if k not in s]
                ck("sub-bacia tem os campos do front", falta==[], f"faltam {falta}")
                nao_txt = [f"{b}.{k}" for b in ("db","params") for k,v in s[b].items() if not isinstance(v,str)]
                ck("db e params sao todos string", nao_txt==[], str(nao_txt[:5]))
                falta_db = [k for k in ESPERADO["db"] if k not in s["db"]]
                ck("o bloco db tem todos os campos do tipo", falta_db==[], f"faltam {falta_db}")

            ctss = (await c.get(f"/api/unidades/{U}/cts")).json()
            ck("ctss e mapa por id", isinstance(ctss["ctss"], dict), type(ctss["ctss"]).__name__)
            if ctss["pares"]:
                falta = [k for k in ESPERADO["par"] if k not in ctss["pares"][0]]
                ck("par de CTS tem sub e cts", falta==[], str(falta))

            et = (await c.get(f"/api/unidades/{U}/etes")).json()["etes"]
            if et:
                falta = [k for k in ESPERADO["ete"] if k not in et[0]]
                ck("ETE tem os campos do front", falta==[], f"faltam {falta}")
                nao_str = [k for k,v in et[0].items() if k!="versao" and not isinstance(v,str)]
                ck("ETE: todo campo e string", nao_str==[], str(nao_str))

            h = (await c.get(f"/api/unidades/{U}/hierarquia")).json()
            falta = [k for k in ESPERADO["unidReg"] if k not in h["unidReg"]]
            ck("unidReg tem os campos do front", falta==[], f"faltam {falta} — tem {sorted(h['unidReg'])}")
            for rot, chave in (("supH","superintendencias"),("cidadeH","cidades"),
                               ("sistemaH","sistemas"),("topo","topo")):
                if not h[chave]:
                    ck(f"{chave}: ha ao menos um", False, "vazio"); continue
                falta = [k for k in ESPERADO[rot] if k not in h[chave][0]]
                ck(f"{chave} tem os campos do front", falta==[], f"faltam {falta} — tem {sorted(h[chave][0])}")

            # NENHUM null em NENHUM payload: o front declara todo campo de ficha
            # como string e chama .trim(); `null` derruba a tela inteira.
            def nulos(o, cam=""):
                if isinstance(o, dict):
                    return [f"{cam}.{k}" for k,v in o.items() if v is None] +                            [x for k,v in o.items() if v is not None for x in nulos(v, f"{cam}.{k}")]
                if isinstance(o, list):
                    return [x for i,v in enumerate(o[:5]) for x in nulos(v, f"{cam}[{i}]")]
                return []
            achados = []
            for p in ("hierarquia","contrato","sub-bacias","etes","cts"):
                achados += nulos((await c.get(f"/api/unidades/{U}/{p}")).json(), p)
            ck("nenhum campo volta null", achados==[], str(sorted(set(achados))[:5]))

            # ANTES de criar a CTS de teste: uma CTS recem-criada nao tem override
            # nenhum (a tela parte da base), entao inclui-la aqui exigiria indices
            # que ela nao deve ter — o teste falharia por estar errado.
            subs = (await c.get(f"/api/unidades/{U}/sub-bacias")).json()["subs"]
            if subs:
                idx = sorted(next(iter(subs.values()))["obrasOverride"])
                ck("sub-bacia traz as 5 obras da base", idx == ["0","1","2","3","4"], str(idx))
            # Escolhe uma CTS que o proprio servidor NAO denuncia. Em uA2 a
            # primeira da lista e `cts_b2b80_1_3`, que esta no cadastro sem no na
            # topologia e sem componente nenhum: o teste falhava nela e parecia
            # regressao da base de obras, quando era a CTS que esta quebrada no
            # dado real. Pegar "a primeira" so funciona quando todas prestam.
            cts_payload = (await c.get(f"/api/unidades/{U}/cts")).json()
            quebradas = {x["id"] for x in cts_payload.get("inconsistencias", [])}
            sadias = [v for k, v in cts_payload["ctss"].items() if k not in quebradas]
            if sadias:
                idx = sorted(sadias[0]["obrasOverride"])
                ck("CTS traz as 4 obras da base dela", idx == ["0","1","2","3"], str(idx))
            elif cts_payload["ctss"]:
                print(f"  --   todas as CTS de {U} estao denunciadas; checagem pulada")
            else:
                print(f"  --   esta unidade nao tem CTS (normal em {U}); checagem pulada")

            # AQUI HAVIA tres checagens do `POST /cts` (ficha completa, sem
            # envelope, com `obrasOverride`) e o `DELETE` que limpava depois.
            # Elas nao falharam: o endpoint que cobriam foi REMOVIDO de
            # proposito — criar CTS pela tela gravava ficha e par sem tocar em
            # `sistema_topologia`, produzindo uma CTS que o motor nunca ve.
            # Hoje o POST responde 405.
            #
            # O bug que elas pegaram (o envelope `{par, cts}`, que derrubava a
            # tela em `undefined['0']`) morreu junto com o endpoint. Ficam
            # registradas aqui para ninguem as "restaurar" achando que sumiram
            # por descuido.

            # O que sobrou vivo daquele episodio: as CTS que existem pela metade
            # agora sao DENUNCIADAS em vez de servidas caladas.
            d = (await c.get(f"/api/unidades/{U}/cts")).json()
            ck("GET /cts traz `inconsistencias`", "inconsistencias" in d,
               str(sorted(d))[:70])
            ck("cada inconsistencia se explica",
               all({"tipo", "id", "detalhe"} <= set(x) for x in d.get("inconsistencias", [])),
               "falta tipo/id/detalhe")
    print("\nFALHAS:", f or "nenhuma"); raise SystemExit(1 if f else 0)
asyncio.run(main())
