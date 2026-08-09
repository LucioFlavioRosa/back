"""Os payloads de RESULTADO contra o `CONTRATO.md`, campo a campo.

A rodada e os ids são DESCOBERTOS pela própria API, e não fixados: `run_teste_1`,
`Rio Bonito` e `b38_1` só existem no `dev/seed.sql`, e com o banco carregado por
`dev/rodar_simulacao_real.py` este teste quebrava com KeyError — parecendo
regressão do serviço quando era o teste olhando para dado que não está mais lá.

O par de `dev/formas_cadastro.py`, que faz o mesmo do lado do cadastro.
"""

import asyncio
import os
import sys
import urllib.parse

os.environ["POSTGRES_URL"] = "postgresql://otim:otim@localhost:55432/otimizador"
os.environ["SERVICE_BUS_CONN"] = ""
sys.path.insert(0, ".")

import logging  # noqa: E402

import httpx  # noqa: E402

logging.disable(logging.WARNING)
from main import app  # noqa: E402

CAMPOS = {
    "meta": ["runId", "nome", "unidadeId", "dataHora", "autor", "status", "statusTexto",
             "kpis", "parametros"],
    "painel": ["anos", "curvaS", "cascata", "capexPorComponente", "histogramaVpl",
               "subbaciasPositivas", "subbaciasNegativas", "obrasPorAno", "fimCapex"],
    "ebitda": ["anos", "total", "anoViraPositivo", "fimCapex"],
    "cidade": ["id", "nome", "fimConcessao", "fimCapex", "capexTotal", "vpl",
               "ligacoesNovas", "coberturaBasePct", "coberturaFinalPct", "cobertura",
               "metas", "cascata", "paridade", "sistemas"],
    "topologia": ["sistemaId", "sistemaNome", "cidadeId", "cidadeNome", "subbacias",
                  "faturando", "capexConstruido", "nos", "ete"],
    "subbacia": ["id", "tipo", "pareadaCom", "cidadeId", "sistemaId", "fatura", "vazao",
                 "vpl", "cascata", "receita", "explicacao", "caminho", "elementos"],
    "obra": ["obraId", "componente", "rotulo", "situacao", "cidadeId", "sistemaId",
             "subbaciaId", "responsavel", "obrigatoria", "quantidade", "unidade",
             "precoUnitario", "capex", "opexAno", "prazoMeses", "mesMaisCedo", "wacc",
             "waccOrigem", "ligacoesNovas", "ticketMedio", "precoPorLigacao",
             "capexConstruido", "capexQueFalta", "dataInicio", "dataPronta",
             "categoria", "elo", "narrativa", "dependencias"],
}
KPIS = ["vpl", "capexTotal", "opexTotal", "receitaTotal", "obrasConstruidas", "obrasTotal",
        "obrigatoriasConstruidas", "obrigatoriasTotal", "subbaciasFaturando",
        "subbaciasTotal", "coberturaFimPct", "metasAtingidas", "metasTotal"]
METRICAS = ["vpl", "capex", "usoOrcamentoPct", "obrasConstruidas", "obrasTotal",
            "coberturaFimPct", "metasAtingidas", "metasTotal", "ebitdaTotal"]

falhas: list[str] = []


def ck(nome: str, cond: bool, detalhe: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FALHA'} {nome}{'' if cond else '  <- ' + detalhe}")
    if not cond:
        falhas.append(nome)


async def main() -> None:
    q = urllib.parse.quote
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:

            async def g(p):
                return (await c.get(p)).json()

            runs = await g("/api/runs")
            if not runs:
                ck("ha ao menos uma rodada publicada", False, "historico vazio")
                return
            # SO as PUBLICADAS: desde que o historico passou a incluir as rodadas em voo
            # (PENDENTE/RODANDO/ERRO), `runs[0]` pode ser uma que ainda nao tem resultado
            # nenhum — e o drill-down abaixo estourava com IndexError. `publicada` existe
            # exatamente para essa distincao.
            publicadas = [r for r in runs if r.get("publicada")]
            if not publicadas:
                ck("ha rodada publicada", False, "so ha rodadas em voo")
                return
            rid = publicadas[0]["runId"]
            print(f"  (rodada {rid})")

            falta = [k for k in METRICAS if k not in (publicadas[0].get("metricas") or {})]
            ck("metricas da rodada publicada completas", falta == [], f"faltam {falta}")

            meta = await g(f"/api/runs/{rid}/meta")
            ck("meta completo", [k for k in CAMPOS["meta"] if k not in meta] == [],
               str([k for k in CAMPOS["meta"] if k not in meta]))
            ck("meta.kpis completo", [k for k in KPIS if k not in (meta.get("kpis") or {})] == [],
               str([k for k in KPIS if k not in (meta.get("kpis") or {})]))

            for nome, p in (("painel", f"/api/runs/{rid}/painel"),
                            ("ebitda", f"/api/runs/{rid}/ebitda")):
                d = await g(p)
                falta = [k for k in CAMPOS[nome] if k not in d]
                ck(f"{nome} completo", falta == [], f"faltam {falta}")

            cid = (await g(f"/api/runs/{rid}/cidades"))[0]["id"]
            cidade = await g(f"/api/runs/{rid}/cidades/{q(cid)}")
            falta = [k for k in CAMPOS["cidade"] if k not in cidade]
            ck("cidade completo", falta == [], f"faltam {falta}")

            sis = cidade["sistemas"][0]["id"]
            topo = await g(f"/api/runs/{rid}/sistemas/{q(sis)}/topologia")
            falta = [k for k in CAMPOS["topologia"] if k not in topo]
            ck("topologia completo", falta == [], f"faltam {falta}")
            ck("a topologia tem nos com componentes",
               bool(topo["nos"]) and bool(topo["nos"][0]["componentes"]),
               f"{len(topo['nos'])} nos")

            sb = await g(f"/api/runs/{rid}/subbacias/{q(topo['nos'][0]['id'])}")
            falta = [k for k in CAMPOS["subbacia"] if k not in sb]
            ck("subbacia completo", falta == [], f"faltam {falta}")

            obra_id = next((x["obraId"] for x in topo["nos"][0]["componentes"] if x["obraId"]), None)
            if not obra_id:
                ck("ha obra com ficha na topologia", False, "nenhum componente com obraId")
            else:
                ob = await g(f"/api/runs/{rid}/obras/{q(obra_id)}")
                falta = [k for k in CAMPOS["obra"] if k not in ob]
                ck("obra completo", falta == [], f"faltam {falta}")

            pa = await g(f"/api/runs/{rid}/painel")
            ck("cascata tem os 6 rotulos, com o VPL como total",
               [x["tipo"] for x in pa["cascata"]] == ["entra", "entra", "entra", "sai", "sai", "total"],
               str([(x["rotulo"], x["tipo"]) for x in pa["cascata"]]))

    print("\nFALHAS:", falhas or "nenhuma")
    raise SystemExit(1 if falhas else 0)


asyncio.run(main())
