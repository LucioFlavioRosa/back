"""Roda a simulacao de verdade sobre o CADASTRO e publica no banco da aplicacao.

O motor le `input.*` — o mesmo schema que as telas de cadastro escrevem — e o
resultado vira `public.otim_*`, que as telas de resultado leem. Uma fonte so, ponta
a ponta: o que a Regional preenche e o que a rodada calcula.

NAO EXISTE MAIS CARGA DE PLANILHA AQUI. A versao anterior fazia `TRUNCATE input.*
CASCADE` e recarregava de um .xlsx congelado — o que, depois que o cadastro passou a
ser preenchido pela tela, APAGAVA o trabalho de quem preencheu. O cadastro e a fonte;
nada o sobrescreve.
"""

import json
import os
import sys
from pathlib import Path

#: Onde mora o pacote do otimizador, em layout PLANO (os modulos no topo, sem o
#: pacote `otimizador.`). `OTIMIZADOR_PACOTE` aponta para outra copia quando se quer
#: medir uma alteracao sem tocar na de trabalho.
PACOTE = Path(
    os.environ.get("OTIMIZADOR_PACOTE")
    or r"C:\Users\LúcioFláviodosSantos\projetos\pacote-motor-main"
)
sys.path.insert(0, str(PACOTE))

from sqlalchemy import create_engine, text  # noqa: E402

PG = "postgresql://otim:otim@localhost:55432/otimizador"

#: O teto de CAPEX por ano da rodada de desenvolvimento.
#:
#: CONSTANTE DE MODULO, e nao um literal dentro de `ler_banco`: ele precisa ir
#: TAMBEM para `controle.run_request.params`, porque e de la que a analise de
#: sensibilidade escala o orcamento. Sem isso a rodada nasce sem `ORCAMENTO` no
#: pedido, e pedir uma variacao dela responde 422 — "a rodada de origem nao tem
#: orcamento gravado" — com o resultado publicado e correto no banco.
ORCAMENTO = {2026: 60e6, 2027: 60e6, 2028: 50e6, 2029: 50e6, 2030: 40e6,
             2031: 40e6, 2032: 30e6, 2033: 30e6}
UNIDADE = sys.argv[1] if len(sys.argv) > 1 else "uA1"
MAX_TIME_S = int(sys.argv[2]) if len(sys.argv) > 2 else 90

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


def rodar_e_publicar() -> str:
    import carregar_postgres as C
    import otimizador_capex_cpsat63 as CP
    import otimizador_capex_v62 as M
    import persistencia as P
    import publicacao as PUB

    import dashboard_otimizador_v2 as D

    P.set_engine(M, D)

    print(f"\ncarregando o cenario da unidade {UNIDADE} a partir de input.*...")
    abas = C.abas_do_postgres(PG)
    cen = M.ler_banco(
        abas,
        unidade=UNIDADE,
        orcamento=ORCAMENTO,
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
        cen, res, banco="postgres://input", run_id=run_id, abas_fonte=abas,
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
                "p": json.dumps(
                    {
                        "UNIDADE": UNIDADE,
                        # As chaves que `POST /runs/{id}/variacao` reconstroi para
                        # montar a rodada escalada. `ORCAMENTO` com a chave em texto:
                        # JSON nao tem chave inteira, e o servidor le assim.
                        "ORCAMENTO": {str(a): v for a, v in ORCAMENTO.items()},
                        "BASE_RECEITA": "arrecadada",
                        "USAR_CTS": True,
                        "FOCO_COBERTURA": 1.0,
                        "MAX_TIME_S": MAX_TIME_S,
                        "origem": "dev/rodar_simulacao_real.py",
                    }
                ),
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
    rid = rodar_e_publicar()
    print(f"\nPRONTO. run_id publicado: {rid}")
