"""Roda a simulacao de verdade e publica no banco da aplicacao.

O `.sql` do pacote e o DDL, nao um dump de saida. Mas o pacote tem a planilha de
entrada e o motor inteiro — entao em vez de inventar dado, rodamos o que o Colab
roda e publicamos o resultado real.

Duas metades:
  1. a PLANILHA vira o schema `input` (cada aba, uma tabela) — e o que as telas de
     cadastro leem;
  2. o MOTOR roda sobre a planilha e o resultado vira `public.otim_*` — e o que as
     telas de resultado leem.

As duas precisam da MESMA unidade, senao a tela de cadastro mostra uma coisa e a
de resultado outra.
"""

import json
import os
import sys
from pathlib import Path

#: Onde mora o pacote do otimizador. `OTIMIZADOR_PACOTE` aponta para uma COPIA
#: modificada sem tocar no original — foi assim que a fase de desempate por
#: retorno foi medida antes de existir no pacote instalado.
PACOTE = Path(
    os.environ.get("OTIMIZADOR_PACOTE")
    or (
        r"C:\Users\LúcioFláviodosSantos\OneDrive - Peers Consulting\Área de Trabalho"
        r"\aegea\Otimizador_CAPEX_v62_pacote_rev11\Otimizador_CAPEX_v62_pacote"
    )
)
sys.path.insert(0, str(PACOTE))

import pandas as pd  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

BANCO = str(PACOTE / "banco_dados_regional_v29_completo.xlsx")
PG = "postgresql://otim:otim@localhost:55432/otimizador"
UNIDADE = sys.argv[1] if len(sys.argv) > 1 else "uA1"
MAX_TIME_S = int(sys.argv[2]) if len(sys.argv) > 2 else 90

# Aba da planilha -> tabela de `input`. Copiado de
# `carregar_postgres.ABAS_INPUT` no pacote de producao.
ABAS = {
    "unidade-regional": "unidade_regional",
    "empresa": "empresa",
    "cidade-empresa": "cidade_empresa",
    "cidade-sistema": "cidade_sistema",
    "sistema-topologia": "sistema_topologia",
    "cidade-operacional": "cidade_operacional",
    "subbacia-operacional": "subbacia_operacional",
    "componentes-subbacias-capex": "componentes_subbacias_capex",
    "ete-capex": "ete_capex",
    "regional-operacional": "regional_operacional",
    "metas-cobertura": "metas_cobertura",
    "fator-esgoto": "fator_esgoto",
    "subbacia-cts": "subbacia_cts",
    "cts-operacional": "cts_operacional",
    "componentes-cts-capex": "componentes_cts_capex",
    "orcamento": "orcamento",
}

# Ordem de carga EXPLICITA: as FKs exigem o pai antes do filho, e a ordem do
# dicionario acima e a de leitura (agrupada por assunto), nao a de dependencia.
# `subbacia_cts` referencia `cts_operacional`, entao a CTS vem primeiro.
ORDEM = [
    "unidade_regional",
    "empresa",
    "cidade_empresa",
    "cidade_sistema",
    "sistema_topologia",
    "cidade_operacional",
    "subbacia_operacional",
    "componentes_subbacias_capex",
    "ete_capex",
    "regional_operacional",
    "metas_cobertura",
    "fator_esgoto",
    "cts_operacional",
    "subbacia_cts",
    "componentes_cts_capex",
    "orcamento",
]

# `pool_pre_ping` NAO e zelo excessivo: sem ele, o pool entrega uma conexao que
# morreu enquanto estava ociosa — e a primeira escrita nela BLOQUEIA, sem timeout,
# para sempre. Medido: reiniciar o container do Postgres sob um worker vivo fez a
# rodada seguinte parar logo depois de "RECEBIDA", com 0% de CPU e 116 MB de RAM,
# antes de carregar dado nenhum. O `pre_ping` custa um `SELECT 1` por checkout e
# devolve uma conexao nova quando a velha morreu.
#
# `pool_recycle` fecha conexao velha por idade, que e a defesa contra o mesmo
# problema quando quem derruba e um firewall ou o proprio Postgres.
#
# `connect_timeout` e `statement_timeout` sao o ultimo anteparo: sem eles, um
# socket pendurado nao vira erro — vira espera eterna, que e o pior modo de falha
# porque nao aparece em lugar nenhum.
eng = create_engine(
    PG,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        "connect_timeout": 10,
        # 10 min: a materializacao da maior unidade leva ~9,5 min de relogio, e um
        # teto menor mataria trabalho legitimo.
        "options": "-c statement_timeout=600000",
    },
)


def colunas(tabela: str) -> list[str]:
    with eng.begin() as con:
        return [
            r[0]
            for r in con.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema='input' AND table_name=:t"
                ),
                {"t": tabela},
            )
        ]


def carregar_input() -> None:
    """Planilha -> `input.*`. Só as colunas que a tabela tem: a planilha traz
    colunas de trabalho que o DDL não modela, e mandá-las quebraria o INSERT."""
    xl = pd.ExcelFile(BANCO)
    print(f"planilha: {len(xl.sheet_names)} abas")

    with eng.begin() as con:
        for tab in reversed(ORDEM):
            con.execute(text(f"TRUNCATE input.{tab} CASCADE"))

    tabela_para_aba = {v: k for k, v in ABAS.items()}
    for tabela in ORDEM:
        aba = tabela_para_aba[tabela]
        if aba not in xl.sheet_names:
            print(f"  {aba:<30} (ausente na planilha)")
            continue
        df = pd.read_excel(BANCO, sheet_name=aba)
        cols = colunas(tabela)
        manter = [c for c in df.columns if c in cols]
        fora = [c for c in df.columns if c not in cols]
        df = df[manter].where(pd.notna(df[manter]), None)
        df.to_sql(tabela, eng, schema="input", if_exists="append", index=False, chunksize=500)
        extra = f"  (fora: {', '.join(fora[:3])}{'...' if len(fora) > 3 else ''})" if fora else ""
        print(f"  {aba:<30} -> input.{tabela:<28} {len(df):>6} linhas{extra}")


def rodar_e_publicar() -> str:
    import otimizador_capex_cpsat63 as CP
    import otimizador_capex_v62 as M
    import persistencia as P
    import publicacao as PUB

    import dashboard_otimizador_v2 as D

    P.set_engine(M, D)

    print(f"\ncarregando o cenario da unidade {UNIDADE}...")
    cen = M.ler_banco(
        BANCO,
        unidade=UNIDADE,
        orcamento={2026: 60e6, 2027: 60e6, 2028: 50e6, 2029: 50e6, 2030: 40e6,
                   2031: 40e6, 2032: 30e6, 2033: 30e6},
        base_receita="arrecadada",
        usar_cts=True,
        cobertura_so_residencial=False,
        curva_adocao="scurve",
        foco_cobertura=1.0,
        penalidade_cobertura="meta+cobertura",
        anos_extra_conclusao=3,
        ete_faseada=True,
    )
    print(f"  obras={len(cen.obras)}  sistemas={len(cen.sistemas)}  nos={len(cen.nos)}")

    print(f"otimizando (max {MAX_TIME_S}s)...")
    res = CP.resolver_por_sistema(cen, max_time_s=MAX_TIME_S, workers=8)
    print(f"  status={res.get('milp_status')}  VPL={res.get('vpl'):,.0f}")

    run_id = P.novo_run_id("run")
    tabs = P.materializar(
        cen, res, banco=BANCO, run_id=run_id,
        params={"UNIDADE": UNIDADE, "BASE_RECEITA": "arrecadada", "USAR_CTS": True,
                "FOCO_COBERTURA": 1.0, "INCLUIR_INDUSTRIAL": True},
    )
    print(f"  materializado: {len(tabs)} tabelas, run_id={run_id}")

    # `rotulo` e `usuario` sao o que o historico mostra. O backend ainda nao tem
    # onde guarda-los na run_request (migracao pendente), entao aqui vao direto.
    PUB.publicar(
        tabs, pg=PG, criar_schema=False, verbose=True,
        rotulo=f"{UNIDADE} — janela 8a, foco cobertura", usuario="lucio.rosa",
    )
    registrar_no_controle(run_id)
    return run_id


def registrar_no_controle(run_id: str) -> None:
    """Registra a rodada em `controle.*`, como o job faria em producao.

    Este script publica direto em `public.otim_*` e pula a fila — e por isso as
    rodadas apareciam no historico mas `GET /runs/{id}/status` devolvia 404: a
    tela de acompanhamento nao achava a rodada que a tela de resultados mostrava.

    A alternativa seria o endpoint inventar o status a partir de `otim_meta`
    quando a rodada existisse. Seria pior: colocaria no codigo de PRODUCAO um
    remendo para um buraco que so existe porque um script de DEV atalha a fila.
    Aqui o ambiente local passa a se parecer com producao, que e o que se quer de
    um ambiente local.
    """
    with eng.begin() as con:
        con.execute(
            text(
                """INSERT INTO controle.run_request (run_id, unidade, params, solicitado_por)
                   VALUES (:r, :u, CAST(:p AS jsonb), :q)
                   ON CONFLICT (run_id) DO NOTHING"""
            ),
            {
                "r": run_id,
                "u": UNIDADE,
                "p": json.dumps({"UNIDADE": UNIDADE, "origem": "dev/rodar_simulacao_real.py"}),
                "q": "lucio.rosa",
            },
        )
        con.execute(
            text(
                """INSERT INTO controle.run_status (run_id, status)
                   VALUES (:r, 'SUCESSO')
                   ON CONFLICT (run_id) DO UPDATE SET status = 'SUCESSO'"""
            ),
            {"r": run_id},
        )
    print(f"  controle.run_request/run_status: {run_id} = SUCESSO")


if __name__ == "__main__":
    carregar_input()
    rid = rodar_e_publicar()
    print(f"\nPRONTO. run_id publicado: {rid}")
