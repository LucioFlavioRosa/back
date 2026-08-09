"""O consumidor da fila — faz localmente o que o job do Databricks faz.

Sem ele o disparo pela tela fica pela metade: `POST /runs` grava a rodada,
publica na fila e responde `PENDENTE` — e nada mais acontece, porque em producao
quem tira da fila e o job. O modal do front fica girando para sempre, e ninguem
consegue ver a dinamica real (fila, paralelismo, rodada em voo).

O QUE ELE FAZ, na ordem que o job faz:

  1. recebe a mensagem            (o corpo so tem `run_id` e `unidade_id`)
  2. le `controle.run_request`    (a fonte de verdade dos parametros — a mensagem
                                   nao os carrega de proposito, para nao
                                   envelhecer em relacao ao banco)
  3. marca RODANDO
  4. roda o motor COM ESSES parametros
  5. publica em `public.otim_*`
  6. marca SUCESSO — ou ERRO, com a causa

O passo 4 usa os parametros da rodada, e nao os fixos de
`rodar_simulacao_real.py`. E o que faz "testar parametros diferentes pela tela"
significar alguma coisa: mudar o orcamento ou desligar CTS no formulario muda o
resultado que volta.

  python dev/worker.py                 # 1 por vez: da para VER a fila enfileirar
  python dev/worker.py --paralelo 3    # tres ao mesmo tempo
  python dev/worker.py --tempo 20      # corta o tempo do solver (demo mais rapida)

Ctrl+C encerra. Rodada interrompida no meio fica RODANDO no banco — igual a um
job que morre, e e proposital: mentir "ERRO" para algo que talvez tenha
terminado seria pior.
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, ".")

import rodar_simulacao_real as R  # noqa: E402
from azure.servicebus.aio import ServiceBusClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

CONN = os.environ.get(
    "SERVICE_BUS_CONN",
    "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;",
)
FILA = os.environ.get("FILA_SIMULACOES", "otimizacoes")


def agora() -> str:
    from datetime import datetime

    return datetime.now().strftime("%H:%M:%S")


def log(run: str, msg: str) -> None:
    print(f"  [{agora()}] {(run[-8:] if run else '--------'):<8}  {msg}", flush=True)


#: As faixas que o FRONT usa para NOMEAR a etapa
#: (`src/simulacao/domain/simulacao.ts`, ETAPAS). Os numeros aqui tem de cair
#: dentro delas, senao a barra anda enquanto o texto mente:
#:   <20  "Lendo dados da unidade..."
#:   <45  "Montando o modelo de otimizacao..."
#:   <90  "Resolvendo (solver)..."
#:   <100 "Materializando as tabelas de resultado..."
LENDO, MODELO, SOLVER, MATERIALIZANDO, PRONTO = 10, 30, 60, 92, 100
#: Teto da barra ENQUANTO o solver roda. Tem de ficar ABAIXO de 90, que e onde
#: o front troca o texto para "Materializando": chegar a 90 antes da hora fazia
#: a tela anunciar uma etapa que ainda nao comecou. Barra que anda enquanto o
#: texto mente e pior que barra parada.
SOLVER_TETO = 89


def marcar(run_id: str, status: str, erro: str | None = None, progresso: int | None = None) -> None:
    with R.eng.begin() as con:
        con.execute(
            text(
                """INSERT INTO controle.run_status
                       (run_id, status, erro, progresso, atualizado_em)
                   VALUES (:r, :s, :e, coalesce(:p, 0), now())
                   ON CONFLICT (run_id) DO UPDATE
                     SET status = EXCLUDED.status, erro = EXCLUDED.erro,
                         progresso = coalesce(:p, run_status.progresso),
                         atualizado_em = now()"""
            ),
            {"r": run_id, "s": status, "e": erro, "p": progresso},
        )


def andar(run_id: str, progresso: int) -> None:
    """So o progresso, sem mexer no status — e SO PARA A FRENTE.

    As duas condicoes do `WHERE` sao guarda-corpo contra escrita atrasada:

      `status = 'RODANDO'`  a thread que acompanha o solver espera ate
                            `tempo // 12` segundos entre passos, e o `join` que a
                            encerra nao cobre essa espera. Ela pode acordar depois
                            do SUCESSO; aqui a escrita simplesmente nao acontece.
      `progresso < :p`      nenhum caminho faz a barra voltar. Vale tambem se duas
                            atualizacoes chegarem fora de ordem.

    Poderia ser resolvido acertando o timeout do `join`, mas isso e apostar em
    tempo — e a aposta se perde no dia em que alguem mexer no intervalo.
    """
    with R.eng.begin() as con:
        con.execute(
            text(
                "UPDATE controle.run_status SET progresso = :p, atualizado_em = now()"
                " WHERE run_id = :r AND status = 'RODANDO' AND progresso < :p"
            ),
            {"p": progresso, "r": run_id},
        )


def pedido(run_id: str) -> dict | None:
    with R.eng.begin() as con:
        linha = con.execute(
            text("SELECT unidade, params FROM controle.run_request WHERE run_id = :r"),
            {"r": run_id},
        ).first()
    if not linha:
        return None
    params = linha[1]
    return {"unidade": linha[0], "params": json.loads(params) if isinstance(params, str) else params}


def executar(run_id: str, tempo: int) -> None:
    """Motor + publicacao, com os parametros DA RODADA. Bloqueante de proposito:
    e trabalho de CPU, e roda em thread propria (ver `processar`)."""
    ped = pedido(run_id)
    if not ped:
        raise RuntimeError(f"{run_id} nao esta em controle.run_request")

    andar(run_id, LENDO)
    p = ped["params"]
    unidade = p.get("UNIDADE") or ped["unidade"]
    orc = {int(k): float(v) for k, v in (p.get("ORCAMENTO") or {}).items()}
    log(run_id, f"unidade={unidade}  anos={sorted(orc)}  total={sum(orc.values()):,.0f}")

    import dashboard_otimizador_v2 as D
    import otimizador_capex_cpsat63 as CP
    import otimizador_capex_v62 as M
    import persistencia as P
    import publicacao as PUB

    P.set_engine(M, D)

    cen = M.ler_banco(
        R.BANCO,
        unidade=unidade,
        orcamento=orc,
        base_receita=p.get("BASE_RECEITA", "arrecadada"),
        usar_cts=bool(p.get("USAR_CTS", True)),
        incluir_industrial=bool(p.get("INCLUIR_INDUSTRIAL", True)),
        curva_adocao=p.get("CURVA_ADOCAO", "scurve"),
        foco_cobertura=float(p.get("FOCO_COBERTURA", 1.0)),
        penalidade_cobertura=p.get("PENALIDADE_COBERTURA", "meta+cobertura"),
        anos_extra_conclusao=int(p.get("ANOS_EXTRA_CONCLUSAO", 3)),
        ete_faseada=bool(p.get("ETE_FASEADA", True)),
    )
    log(run_id, f"cenario: {len(cen.obras)} obras, {len(cen.sistemas)} sistemas")
    andar(run_id, MODELO)

    # O solver e a etapa longa. Uma thread empurra a barra dentro da faixa dele
    # enquanto ele trabalha: sem isso a tela fica parada nos 30% pelo tempo todo,
    # e "parado" e o sinal universal de travado.
    import threading

    parar = threading.Event()

    def acompanhar() -> None:
        passo = SOLVER
        while not parar.wait(max(2, tempo // 12)):
            passo = min(passo + 2, SOLVER_TETO)
            andar(run_id, passo)

    batedor = threading.Thread(target=acompanhar, daemon=True)
    andar(run_id, SOLVER)
    batedor.start()
    try:
        res = CP.resolver_por_sistema(cen, max_time_s=tempo, workers=8)
    finally:
        parar.set()
        batedor.join(timeout=2)
    log(run_id, f"solver: {res.get('milp_status')}  VPL={res.get('vpl'):,.0f}")

    # `run_id` E o da fila: publicar com outro id faria a tela perder a rodada
    # que ela esta acompanhando.
    andar(run_id, MATERIALIZANDO)
    tabs = P.materializar(cen, res, banco=R.BANCO, run_id=run_id, params=p)
    PUB.publicar(
        tabs,
        pg=R.PG,
        criar_schema=False,
        verbose=False,
        rotulo=p.get("ROTULO") or f"{unidade} — pela tela",
        usuario=p.get("USUARIO") or "dev@local",
    )
    log(run_id, f"publicado: {len(tabs)} tabelas")


async def processar(msg, tempo: int) -> None:
    corpo = json.loads(str(msg))
    run_id = corpo["run_id"]
    log(run_id, f"RECEBIDA (unidade {corpo.get('unidade_id')})")
    marcar(run_id, "RODANDO", progresso=1)
    try:
        # `to_thread` porque o solver segura a CPU: no laco async ele travaria o
        # recebimento das outras mensagens e o paralelismo seria de mentira.
        await asyncio.to_thread(executar, run_id, tempo)
        marcar(run_id, "SUCESSO", progresso=PRONTO)
        log(run_id, "SUCESSO")
    except Exception as e:  # noqa: BLE001 — a causa vai para o banco e para a tela
        marcar(run_id, "ERRO", f"{type(e).__name__}: {e}"[:500])
        log(run_id, f"ERRO: {type(e).__name__}: {e}")
        traceback.print_exc()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paralelo", type=int, default=1, help="rodadas simultaneas (default 1)")
    ap.add_argument("--tempo", type=int, default=45, help="segundos de solver por rodada")
    a = ap.parse_args()

    print(f"worker: fila `{FILA}`, {a.paralelo} em paralelo, {a.tempo}s de solver")
    print("Ctrl+C encerra.\n")

    limite = asyncio.Semaphore(a.paralelo)
    vivos: set[asyncio.Task] = set()
    espera = 1

    while True:
        try:
            async with ServiceBusClient.from_connection_string(CONN) as cli:
                async with cli.get_queue_receiver(FILA, max_wait_time=5) as rx:
                    espera = 1  # conectou: zera o recuo
                    while True:
                        for msg in await rx.receive_messages(
                            max_message_count=10, max_wait_time=5
                        ):
                            # Completa ANTES de processar: o emulador tem lock
                            # curto, e uma rodada de 45s estouraria o lock e a
                            # mensagem voltaria para a fila — a mesma simulacao
                            # rodando duas vezes. O estado real da rodada vive em
                            # `controle.run_status`, nao na mensagem.
                            await rx.complete_message(msg)

                            async def tarefa(m=msg):
                                async with limite:
                                    await processar(m, a.tempo)

                            tk = asyncio.create_task(tarefa())
                            vivos.add(tk)
                            tk.add_done_callback(vivos.discard)
                        await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # Queda de conexao NAO e fim de vida. Antes era: um `WinError 64` do
            # emulador derrubava o laco, o consumo parava, e as rodadas
            # disparadas pela tela empilhavam em PENDENTE sem nenhum sinal de que
            # nao havia mais ninguem consumindo.
            #
            # As tarefas em voo seguem nas threads delas e terminam normalmente —
            # o que reconecta e so o receptor.
            log("", f"fila caiu ({type(e).__name__}), reconectando em {espera}s")
            await asyncio.sleep(espera)
            espera = min(espera * 2, 30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nencerrado.")
