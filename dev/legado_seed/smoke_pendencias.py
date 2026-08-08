"""A conta de pendencias — e a guarda que ela alimenta.

O numero daqui tem de ser o MESMO que a tela mostra. Divergir faz o usuario ver
"cadastro completo", apertar Iniciar e o servidor recusar sem dizer o que falta.

Exige `dev/seed.sql` recem-aplicado (o teste altera dados).
"""
import asyncio, os, sys
os.environ["POSTGRES_URL"]="postgresql://otim:otim@localhost:55432/otimizador"; os.environ["SERVICE_BUS_CONN"]=""
sys.path.insert(0,".")
import logging, httpx; logging.disable(logging.WARNING)
from main import app
from app.infra import db
falhas=[]
def ck(n,c,d=""):
    print(f"  {'ok  ' if c else 'FALHA'} {n}{'' if c else '  <- '+d}"); (falhas.append(n) if not c else None)
async def main():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            p=(await c.get("/api/unidades/u1/prontidao")).json()
            print("  seed cru:", p["pendencias"], "pendencias", p["porGrupo"])
            ck("cadastro incompleto BLOQUEIA a rodada",
               (await c.post("/api/runs", json={"unidade_id":"u1","orcamento":{"2026":1e6}})).status_code==422)
            # preenche TUDO: 6 params da sub-bacia, a cidade, meta, faixa, a ETE
            await db.buscar("""UPDATE input.subbacia_operacional SET tempo_arrecadacao=3,
                tempo_ramp_up=6, vazao_contribuicao=41.2, vazao_contribuicao_industrial=0,
                potencial_crescimento=0.05 WHERE sub_bacia='b38_1'""")
            await db.buscar("UPDATE input.cidade_operacional SET data_fim_concessao=2049 WHERE cidade_id='c_rio'")
            await db.buscar("""UPDATE input.ete_capex SET opex_por_modulo=0, tempo_de_execucao=12,
                capacidade_nominal_atual=270, vazao_de_operacao_atual=209, wacc=0.09, nova='Nao'
                WHERE ete_id='ete_s38'""")
            p=(await c.get("/api/unidades/u1/prontidao")).json()
            ck("cadastro completo zera as pendencias", p["pendencias"]==0, str(p))
            u=(await c.get("/api/unidades/u1")).json()
            ck("completude vai a 100", u["completude"]==100, str(u["completude"]))
            r=await c.post("/api/runs", json={"unidade_id":"u1","orcamento":{"2026":1e6}})
            ck("agora a rodada passa da guarda", r.status_code==503, f"deu {r.status_code} {r.text[:60]}")
            # esvaziar UM campo tem de voltar a bloquear
            await db.buscar("UPDATE input.subbacia_operacional SET tempo_ramp_up=NULL WHERE sub_bacia='b38_1'")
            p=(await c.get("/api/unidades/u1/prontidao")).json()
            ck("um campo vazio = uma pendencia", p["pendencias"]==1, str(p))
            ck("e volta a bloquear",
               (await c.post("/api/runs", json={"unidade_id":"u1","orcamento":{"2026":1e6}})).status_code==422)
            # a REGUA da cidade muda a conta da sub-bacia na hora
            await db.buscar("UPDATE input.subbacia_operacional SET tempo_ramp_up=6 WHERE sub_bacia='b38_1'")
            await db.buscar("UPDATE input.cidade_operacional SET unidade_cobertura='populacao' WHERE cidade_id='c_rio'")
            p=(await c.get("/api/unidades/u1/prontidao")).json()
            ck("regua populacao cobra os 2 campos de populacao", p["pendencias"]==2, str(p))
            # obra com campo vazio conta
            await db.buscar("""INSERT INTO input.componentes_subbacias_capex
                (sub_bacia, componente, quantidade) VALUES ('b38_1','Tronco',NULL)""")
            p=(await c.get("/api/unidades/u1/prontidao")).json()
            ck("obra com 7 campos vazios soma 7", p["pendencias"]==9, str(p))
    print("\nFALHAS:", falhas or "nenhuma"); raise SystemExit(1 if falhas else 0)
asyncio.run(main())
