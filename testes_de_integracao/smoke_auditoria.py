"""Auditoria por ficha, identidade da unidade no resultado, e ficha inteira.

Este arquivo se chamava `smoke_conflito.py` e provava o 409 por conteudo: ler a
versao, gravar, tentar de novo com a versao velha, levar conflito. O 409 de ficha
saiu (R6) e a auditoria visivel entrou no lugar — entao o smoke prova o que EXISTE
agora: quem gravou, quando, e que o autor vem do token e nao do corpo.

Nao e a mesma garantia, e vale dizer em voz alta: o 409 IMPEDIA a sobrescrita, e a
auditoria so a TORNA VISIVEL depois. A troca foi decisao do dono do produto, e o
motivo esta em `migracoes/006_auditoria_cadastro.sql`.

O seed grava `otim_meta.regional='Litoral 1'` — o NOME, que e o que o job
realmente publica (`otimizador_capex_v62.py:1117`). Com `'u1'`, como estava, os
problemas de identidade ficavam invisiveis.
"""
import asyncio, os, sys
os.environ["POSTGRES_URL"]="postgresql://otim:otim@localhost:55432/otimizador"; os.environ["SERVICE_BUS_CONN"]=""
sys.path.insert(0,".")
import logging, httpx; logging.disable(logging.WARNING)
from main import app

# `params` e `db` viajam INTEIROS (contrato, e agora `_exigir_ficha_inteira`):
# campo vazio vai como string vazia, nunca ausente. Este helper monta a ficha
# completa para o teste nao repetir as 20 chaves em cada chamada — que e
# exatamente o que o `fichas.ts` do front faz num lugar so.
_DB = ["arr", "arrInd", "ecoA", "ecoN", "ecoU", "fat", "fatInd",
       "ligA", "ligAInd", "ligN", "ligU", "ligUInd"]
_PARAMS = ["preco", "tarr", "ramp", "vaz", "vazInd", "pot", "popU", "popA"]


def ficha(params=None, db=None, **resto):
    corpo = {"params": {**{k: "" for k in _PARAMS}, **(params or {})},
             "db": {**{k: "" for k in _DB}, **(db or {})}}
    corpo.update(resto)
    return corpo

f=[]
def ck(n,c,d=""):
    print(f"  {'ok  ' if c else 'FALHA'} {n}{'' if c else '  <- '+d}"); (f.append(n) if not c else None)
async def main():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            sb=(await c.get("/api/unidades/u1/sub-bacias")).json()
            antes=sb["subs"]["b38_1"]
            ck("a ficha vem com os campos de auditoria",
               "atualizadoEm" in antes and "atualizadoPor" in antes, str(sorted(antes)))

            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json=ficha(params={"preco":"1.900,00"}, overrides=[]),
                headers={"X-Usuario-Dev":"ana@aegea"})
            ck("a ficha grava", r.status_code==200, f"{r.status_code} {r.text[:70]}")

            depois=(await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            ck("o autor gravado e o do TOKEN", depois["atualizadoPor"]=="ana@aegea",
               repr(depois["atualizadoPor"]))
            ck("a data de alteracao mudou", depois["atualizadoEm"]!=antes["atualizadoEm"],
               f"{antes['atualizadoEm']!r} -> {depois['atualizadoEm']!r}")

            # O corpo NAO pode escolher quem assina. Se pudesse, a auditoria seria
            # decoracao: bastaria mandar o nome de outra pessoa.
            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json=ficha(params={"preco":"1.950,00"}, overrides=[],
                           atualizadoPor="outra@pessoa"),
                headers={"X-Usuario-Dev":"bia@aegea"})
            de_novo=(await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            ck("autor do CORPO e ignorado", de_novo["atualizadoPor"]=="bia@aegea",
               repr(de_novo["atualizadoPor"]))

            # A versao saiu: mandar `versao` no corpo nao pode mais recusar nada.
            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json=ficha(params={"preco":"2.600,00"}, overrides=[], versao="obsoleta"))
            ck("cliente antigo mandando `versao` nao leva 409",
               r.status_code==200, f"{r.status_code} {r.text[:90]}")

            # Duas gravacoes SIMULTANEAS: as duas passam (era aqui que uma levava
            # 409). O lock so garante que elas nao se intercalem — e a ficha fica
            # com o valor de UMA delas, inteiro, nunca com metade de cada.
            r1,r2=await asyncio.gather(
                c.put("/api/unidades/u1/sub-bacias/b38_1", json=ficha(params={"preco":"1.111,00"}, overrides=[])),
                c.put("/api/unidades/u1/sub-bacias/b38_1", json=ficha(params={"preco":"2.222,00"}, overrides=[])))
            ck("gravacoes simultaneas: as duas passam",
               [r1.status_code,r2.status_code]==[200,200], f"{r1.status_code}/{r2.status_code}")
            final=(await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            ck("a ficha fica com o valor de UMA das duas, e nao com uma mistura",
               final["params"]["preco"] in ("1.111","2.222"), repr(final["params"]["preco"]))

            ete=(await c.get("/api/unidades/u1/etes")).json()["etes"]
            cid=(await c.get("/api/unidades/u1/contrato")).json()["cidades"]
            ck("ETE e cidade tambem tem auditoria",
               all("atualizadoPor" in x for x in ete+cid), f"etes={len(ete)} cidades={len(cid)}")

            # ---- identidade da unidade
            m=(await c.get("/api/runs/run_teste_1/meta")).json()
            ck("unidadeId volta o ID e unidadeNome o NOME",
               m["unidadeId"]=="u1" and m["unidadeNome"]=="Litoral 1", f"{m['unidadeId']} / {m['unidadeNome']}")
            h=(await c.get("/api/runs?unidade=u1")).json()
            ck("filtro ?unidade=<id> encontra a rodada", len(h)==1, f"{len(h)} rodada(s)")
            h=(await c.get("/api/runs?unidade=u9")).json()
            ck("filtro por unidade inexistente volta vazio", len(h)==0, f"{len(h)}")

            # ---- ficha INTEIRA: o contrato passa a valer, em vez de descrever
            # uma coisa e o codigo fazer outra.
            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json={"params":{"preco":"1,00"},"overrides":[]})
            ck("params parcial da 422 nomeando o que falta",
               r.status_code==422 and "params.tarr" in r.text, f"{r.status_code} {r.text[:110]}")

            r=await c.put("/api/unidades/u1/sub-bacias/b38_1",
                json=ficha(params={"preco":"1.234,00","tarr":"3"}, db={"ligU":"9"}, overrides=[]))
            ck("ficha inteira grava", r.status_code==200, f"{r.status_code} {r.text[:70]}")

            r=await c.put("/api/unidades/u1/sub-bacias/b38_1", json={"overrides":[]})
            ck("so overrides, sem bloco de ficha, continua passando",
               r.status_code==200, f"{r.status_code} {r.text[:70]}")

            sb=(await c.get("/api/unidades/u1/sub-bacias")).json()["subs"]["b38_1"]
            ck("campo enviado vazio LIMPA a coluna",
               sb["params"]["ramp"] in (None,""), repr(sb["params"]["ramp"]))
            ck("industrial esta no bloco db, e nao em params",
               "ligUInd" in sb["db"] and "ligUInd" not in sb["params"], str(sorted(sb["db"])))
            ck("vazInd fica em params (e estimativa, nao medida)",
               "vazInd" in sb["params"], str(sorted(sb["params"])))

    print("\nFALHAS:", f or "nenhuma"); raise SystemExit(1 if f else 0)
# RODA COMO SCRIPT, e só como script.
#
# Sem este guarda, importar o arquivo — o que o pytest faz ao COLETAR — dispara
# a bateria inteira contra a API e termina o processo num `SystemExit`. Eles não
# são testes de pytest: são programas que falam com um serviço de pé.
if __name__ == "__main__":
    asyncio.run(main())
