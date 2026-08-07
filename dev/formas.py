import asyncio, os, sys
os.environ["POSTGRES_URL"]="postgresql://otim:otim@localhost:55432/otimizador"; os.environ["SERVICE_BUS_CONN"]=""
sys.path.insert(0,".")
import httpx, logging; logging.disable(logging.INFO)
from main import app
R="run_teste_1"
ESPERADO = {
 f"/api/runs/{R}/meta": ["runId","nome","unidadeId","dataHora","autor","status","statusTexto","kpis","parametros"],
 f"/api/runs/{R}/painel": ["anos","curvaS","cascata","capexPorComponente","histogramaVpl","subbaciasPositivas","subbaciasNegativas","obrasPorAno","fimCapex"],
 f"/api/runs/{R}/ebitda": ["anos","total","anoViraPositivo","fimCapex"],
 f"/api/runs/{R}/cidades/Rio Bonito": ["id","nome","fimConcessao","fimCapex","capexTotal","vpl","ligacoesNovas","coberturaBasePct","coberturaFinalPct","cobertura","metas","cascata","paridade","sistemas"],
 f"/api/runs/{R}/sistemas/Sistema 38/topologia": ["sistemaId","sistemaNome","cidadeId","cidadeNome","subbacias","faturando","capexConstruido","nos","ete"],
 f"/api/runs/{R}/subbacias/b38_1": ["id","tipo","pareadaCom","cidadeId","sistemaId","fatura","vazao","vpl","cascata","receita","explicacao","caminho","elementos"],
 f"/api/runs/{R}/obras/lig_b38_1": ["obraId","componente","rotulo","situacao","cidadeId","sistemaId","subbaciaId","responsavel","obrigatoria","quantidade","unidade","precoUnitario","capex","opexAno","prazoMeses","mesMaisCedo","wacc","waccOrigem","ligacoesNovas","ticketMedio","precoPorLigacao","capexConstruido","capexQueFalta","dataInicio","dataPronta","categoria","elo","narrativa","dependencias"],
}
KPIS = ["vpl","capexTotal","opexTotal","receitaTotal","obrasConstruidas","obrasTotal","obrigatoriasConstruidas","obrigatoriasTotal","subbaciasFaturando","subbaciasTotal","coberturaFimPct","metasAtingidas","metasTotal"]
METRICAS = ["vpl","capex","usoOrcamentoPct","obrasConstruidas","obrasTotal","coberturaFimPct","metasAtingidas","metasTotal","ebitdaTotal"]

async def main():
    problemas=[]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for url, campos in ESPERADO.items():
                d = (await c.get(url)).json()
                falta = [k for k in campos if k not in d]
                if falta: problemas.append(f"{url}: faltam {falta}")
            m = (await c.get(f"/api/runs/{R}/meta")).json()
            f = [k for k in KPIS if k not in (m.get("kpis") or {})]
            if f: problemas.append(f"meta.kpis: faltam {f}")
            h = (await c.get("/api/runs")).json()
            if h:
                f = [k for k in METRICAS if k not in (h[0].get("metricas") or {})]
                if f: problemas.append(f"runs[0].metricas: faltam {f}")
                print("  status do solver:", h[0]["status"], "(milp = VIAVEL(limite de tempo))")
                print("  ebitdaTotal:", h[0]["metricas"]["ebitdaTotal"])
            cid = (await c.get(f"/api/runs/{R}/cidades/Rio Bonito")).json()
            print("  metas[0]:", cid["metas"][0] if cid["metas"] else "vazio")
            top = (await c.get(f"/api/runs/{R}/sistemas/Sistema 38/topologia")).json()
            print("  ete.ocupacaoPct:", top["ete"]["ocupacaoPct"], "| nos:", len(top["nos"]), "| componentes do no 0:", len(top["nos"][0]["componentes"]))
            ob = (await c.get(f"/api/runs/{R}/obras/lig_b38_1")).json()
            print("  obra.wacc:", ob["wacc"], "| fracaoRateio:", ob["dependencias"][0]["fracaoRateio"])
            pa = (await c.get(f"/api/runs/{R}/painel")).json()
            print("  cascata:", [(x["rotulo"], x["tipo"]) for x in pa["cascata"]])
    print("\nPROBLEMAS DE FORMA:", *problemas, sep="\n  ") if problemas else print("\nnenhum campo faltando")
asyncio.run(main())
