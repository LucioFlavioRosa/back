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
import concurrent.futures
import json
import os
import socket
import sys
import threading
import traceback
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, ".")

import rodar_simulacao_real as R  # noqa: E402
from app.dominio.parametros import mes_ano  # noqa: E402
from azure.servicebus.aio import ServiceBusClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

CONN = os.environ.get(
    "SERVICE_BUS_CONN",
    "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;",
)
FILA = os.environ.get("FILA_SIMULACOES", "otimizacoes")

#: Identidade DESTE processo. Entra em `run_status.worker_id` e em
#: `controle.executor`, e é o que permite dizer "esta rodada é de alguém que
#: ainda está vivo" em vez de deduzir vida por silêncio.
WORKER_ID = f"{socket.gethostname()}/{os.getpid()}/{uuid.uuid4().hex[:6]}"

#: A batida. A cada `BATIDA` segundos o executor renova o lease das rodadas dele e
#: se anuncia em `controle.executor`.
BATIDA = 10

#: Quanto o lease dura além da batida. Três batidas de folga: uma pode falhar por
#: soluço de rede sem que o watchdog declare morto quem está vivo — e declarar
#: morto quem trabalha é pior que demorar meio minuto a mais para notar.
LEASE_SEGUNDOS = BATIDA * 3

#: Que código este processo carrega. Um executor iniciado antes de uma correção
#: segue com o código velho em memória: foi assim que uma rodada nomeada "teste"
#: apareceu no histórico como "uA2 — pela tela", e levou meia hora para se
#: descobrir que havia DOIS workers, de versões diferentes, disputando a fila.
def _versao() -> str:
    import subprocess

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
        ).stdout.strip()
        return sha or "desconhecida"
    except Exception:  # noqa: BLE001
        return "desconhecida"


VERSAO = _versao()


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


#: Preenchido em `main`: um processo por rodada em voo. Global porque o
#: `run_in_executor` precisa alcançá-lo de dentro de `processar`.
POOL: concurrent.futures.ProcessPoolExecutor | None = None


def _carimbar_data_hora(run_id: str) -> None:
    """Corrige `otim_meta.data_hora` para o instante REAL, em UTC.

    O pacote do otimizador grava nessa coluna o relógio LOCAL. A coluna é
    `timestamptz`, então o Postgres assume UTC e o instante fica deslocado pelo
    fuso da máquina — medido em BRT: três horas.

    Quem lê a tela não vê mais isso (o histórico usa `run_request.solicitado_em`),
    mas quem consulta `otim_meta` direto — BI, SQL, outro consumidor — via. E
    rodada publicada por fora da fila não tem pedido para compensar.

    A correção pertence ao pacote do otimizador. Enquanto ela não vem, este
    executor conserta o que publicou: é o dado dele, e deixá-lo errado de
    propósito seria escolher a inconsistência.
    """
    try:
        with R.eng.begin() as con:
            con.execute(
                text(
                    "UPDATE public.otim_meta SET data_hora = now() WHERE run_id = :r"
                ),
                {"r": run_id},
            )
    except Exception as e:  # noqa: BLE001
        log(run_id, f"nao consegui corrigir data_hora: {type(e).__name__}: {e}")


def reivindicar(run_id: str) -> None:
    """Marca a rodada como MINHA, com prazo.

    O prazo é o que distingue "trabalhando" de "morto no meio" — as duas coisas
    se pareciam no banco, e por isso rodada de executor morto ficava `RODANDO`
    para sempre sem ninguém notar.
    """
    with R.eng.begin() as con:
        con.execute(
            text(
                """UPDATE controle.run_status
                      SET worker_id = :w,
                          lease_ate = now() + make_interval(secs => :s)
                    WHERE run_id = :r"""
            ),
            {"r": run_id, "w": WORKER_ID, "s": LEASE_SEGUNDOS},
        )


def soltar(run_id: str) -> None:
    """Devolve a rodada: ela terminou, e o lease não tem mais o que proteger."""
    with R.eng.begin() as con:
        con.execute(
            text(
                "UPDATE controle.run_status SET worker_id = NULL, lease_ate = NULL"
                " WHERE run_id = :r AND worker_id = :w"
            ),
            {"r": run_id, "w": WORKER_ID},
        )


def bater(capacidade: int, em_execucao: int, memoria_mb: int | None) -> None:
    """Anuncia que este executor está vivo, e renova o lease do que ele segura.

    As duas coisas na mesma batida de propósito: um executor que consegue se
    anunciar mas não renovar (ou o contrário) descreveria um estado que não
    existe. Ou está vivo para tudo, ou para nada.
    """
    with R.eng.begin() as con:
        con.execute(
            text(
                """INSERT INTO controle.executor
                       (worker_id, visto_em, capacidade, em_execucao, memoria_mb, versao)
                   VALUES (:w, now(), :c, :e, :m, :v)
                   ON CONFLICT (worker_id) DO UPDATE
                     SET visto_em = now(), capacidade = EXCLUDED.capacidade,
                         em_execucao = EXCLUDED.em_execucao,
                         memoria_mb = EXCLUDED.memoria_mb, versao = EXCLUDED.versao"""
            ),
            {"w": WORKER_ID, "c": capacidade, "e": em_execucao,
             "m": memoria_mb, "v": VERSAO},
        )
        con.execute(
            text(
                """UPDATE controle.run_status
                      SET lease_ate = now() + make_interval(secs => :s)
                    WHERE worker_id = :w AND status = 'RODANDO'"""
            ),
            {"w": WORKER_ID, "s": LEASE_SEGUNDOS},
        )


def despedir() -> None:
    """Sai da lista de executores ao encerrar por Ctrl+C.

    Encerramento LIMPO tira o executor da conta na hora, em vez de deixar a tela
    contando por meio minuto uma capacidade que já não existe. Morte súbita não
    passa por aqui — para essa, quem responde é o `visto_em` parando de andar.
    """
    try:
        with R.eng.begin() as con:
            con.execute(
                text("DELETE FROM controle.executor WHERE worker_id = :w"),
                {"w": WORKER_ID},
            )
    except Exception:  # noqa: BLE001, S110 — encerrando; erro aqui não ajuda ninguém
        pass


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


class Cancelada(Exception):
    """O status desta rodada deixou de ser `RODANDO` enquanto ela executava.

    Carrega o estado ENCONTRADO, e nao so o `run_id`: quase sempre e `CANCELADA`
    (alguem clicou), mas o vigia de lease vencido tambem escreve `ERRO` por cima
    de uma rodada que este executor ainda julga sua. Logar "cancelada a pedido"
    nos dois casos apagaria a distincao justamente no caso raro que interessa.
    """

    def __init__(self, run_id: str, encontrado: str | None) -> None:
        super().__init__(f"{run_id}: status virou {encontrado}")
        self.encontrado = encontrado


def conferir_cancelamento(run_id: str) -> None:
    """Levanta `Cancelada` se o status ja nao for `RODANDO`.

    E a metade do cancelamento que mora AQUI. `POST /runs/{id}/cancelar` grava
    `CANCELADA`; sozinho, isso so faria o front parar de perguntar enquanto o
    processo continuaria consumindo CPU e — o que importa — publicando no fim.

    Chamado nos pontos em que a rodada respira, e o ultimo deles e imediatamente
    antes de `PUB.publicar`: e o unico passo irreversivel. Cancelar e nao publicar
    e o que quem clicou pediu; matar o processo no meio de uma chamada nativa nao
    e possivel, e nao muda esse resultado.
    """
    encontrado = _desfecho(run_id)
    if encontrado != "RODANDO":
        raise Cancelada(run_id, encontrado)


def anotar_solver(run_id: str, res: dict) -> None:
    """Registra em `controle.run_diagnostico` o que o solver devolveu.

    A tabela ja existia para as checagens de qualidade do pacote de producao, e a
    forma dela serve: uma linha por checagem, com nivel e detalhe. O solver e uma
    checagem como outra qualquer — a diferenca e que esta roda sempre.

    `ok` distingue OTIMO de VIAVEL: os dois produzem plano, mas so o primeiro tem
    prova de que nao ha melhor. A tela usa isso para nao dizer "pronto" sobre um
    resultado que parou no limite de tempo.

    Falha aqui NAO derruba a rodada: e registro, nao resultado. Perder a anotacao e
    ruim; perder a rodada por causa da anotacao seria pior.
    """
    milp = str(res.get("milp_status") or "")
    vpl = res.get("vpl")
    detalhe = milp + (f"  VPL={vpl:,.0f}" if isinstance(vpl, (int, float)) else "")
    try:
        with R.eng.begin() as con:
            con.execute(
                text(
                    """INSERT INTO controle.run_diagnostico
                           (run_id, checagem, nivel, ok, detalhe)
                       VALUES (:r, 'solver', :n, :ok, :d)"""
                ),
                {
                    "r": run_id,
                    "n": "info" if milp.upper().startswith("OTIMO") else "aviso",
                    "ok": milp.upper().startswith("OTIMO"),
                    "d": detalhe[:500],
                },
            )
    except Exception as e:  # noqa: BLE001
        log(run_id, f"nao consegui anotar o desfecho do solver: {type(e).__name__}: {e}")


def pedido(run_id: str) -> dict | None:
    with R.eng.begin() as con:
        linha = con.execute(
            text(
                # `rotulo` e `solicitado_por` vem daqui, e nao de `params`: os dois
                # tem COLUNA PROPRIA em `run_request`, e nenhum dos dois pode
                # entrar em `params` — o job valida `params` contra `MAPA_PARAMS` +
                # `CHAVES_DO_JOB` e uma chave desconhecida mata a rodada.
                # Ver `migracoes/004_run_request_rotulo.sql`.
                "SELECT unidade, params, rotulo, solicitado_por"
                "  FROM controle.run_request WHERE run_id = :r"
            ),
            {"r": run_id},
        ).first()
    if not linha:
        return None
    params = linha[1]
    return {
        "unidade": linha[0],
        "params": json.loads(params) if isinstance(params, str) else params,
        "rotulo": linha[2],
        "solicitado_por": linha[3],
    }


def executar(run_id: str, tempo: int) -> None:
    """Motor + publicacao, com os parametros DA RODADA. Bloqueante de proposito:
    e trabalho de CPU, e roda em thread propria (ver `processar`)."""
    ped = pedido(run_id)
    if not ped:
        raise RuntimeError(f"{run_id} nao esta em controle.run_request")

    andar(run_id, LENDO)
    p = ped["params"]
    unidade = p.get("UNIDADE") or ped["unidade"]

    # `ORCAMENTO` tem DUAS formas, e o worker so entendia uma. A tela oferece
    # "cronograma por ano" (dict {ano: valor}) e "valor anual unico" (escalar +
    # HORIZONTE_CAPEX) — ver `app/dominio/parametros.py`. Com o escalar, o worker
    # estourava `AttributeError: 'float' object has no attribute 'items'` e a
    # rodada ia para ERRO: metade dos modos da tela nao funcionava, e o teste de
    # dinamica seria falso justamente onde parecia funcionar.
    bruto = p.get("ORCAMENTO")
    if isinstance(bruto, dict):
        orc = {int(k): float(v) for k, v in bruto.items()}
        descricao = f"anos={sorted(orc)} total={sum(orc.values()):,.0f}"
    elif bruto is not None:
        # Escalar: o motor recebe o numero e o horizonte, e distribui.
        orc = float(bruto)
        descricao = f"anual={orc:,.0f} horizonte={p.get('HORIZONTE_CAPEX')}"
    else:
        raise RuntimeError("a rodada nao tem ORCAMENTO em run_request.params")
    log(run_id, f"unidade={unidade}  {descricao}")

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
        # SEMPRE True, e a linha NAO pode sumir. O default de `ete_faseada` no
        # motor e False, entao omitir o argumento desligaria o tratamento por
        # modulos em silencio — o oposto do que a regra pede. Aqui a receita das
        # metas (apagar o parametro e deixar o default agir) faria o contrario.
        #
        # `True` nao impoe faseamento a ETE NOVA: dentro deste modo o motor separa
        # os dois casos por ETE. Nova (terreno + modulos informados) vira UMA obra
        # de pacote unico; existente vira K modulos incrementais conforme a vazao
        # passa da folga.
        ete_faseada=True,
        # OS SEIS ABAIXO NAO ERAM REPASSADOS. A tela os oferece, o corpo os envia,
        # o banco os grava — e este worker os descartava, entao o motor rodava com
        # o default. `ete_fixo` e `peso_cidade` eram escolha do usuario que nao
        # mudava nada; e a pior forma disso, porque ele ajusta, o numero muda por
        # outro motivo, e ele aprende uma relacao que nao existe. E a mesma licao
        # que `MAX_TIME_S`/`WORKERS` ja tinham dado aqui.
        horizonte_capex=p.get("HORIZONTE_CAPEX"),
        orcamento_total=p.get("ORCAMENTO_TOTAL"),
        # `{}` do payload e "nenhuma prioridade", que e o mesmo que ausencia — mas
        # o motor multiplica por `_pc.get(g, 1.0)`, entao dict vazio ja seria
        # inofensivo. `or None` mantem o default explicito.
        peso_cidade=p.get("PESO_CIDADE") or None,
        data_inicio=mes_ano(p.get("DATA_INICIO")),
        # `metas_cobertura` NAO e repassado, de proposito: as metas vem sempre da
        # base, e o default do motor (None) e exatamente isso. O unico descarte
        # legitimo e por ANO — meta fora da janela de CAPEX nao e cobrada —, e ele
        # ja acontece na avaliacao (`idx >= anos_capex -> continue`).
        #
        # Repassar o que estivesse gravado seria pior que ignorar: rodada criada na
        # janela em que a tela ainda oferecia "ignorar as metas" tem `{}` no
        # `params`, e reexecuta-la produziria um plano sem meta nenhuma. Ver
        # `app/dominio/parametros.py`.
    )
    log(run_id, f"cenario: {len(cen.obras)} obras, {len(cen.sistemas)} sistemas")
    andar(run_id, MODELO)
    # Antes do solver, que e a etapa cara: cancelar durante a leitura do banco tem
    # de evitar os minutos seguintes, e nao so o passo final.
    conferir_cancelamento(run_id)

    # O solver e a etapa longa. Uma thread empurra a barra dentro da faixa dele
    # enquanto ele trabalha: sem isso a tela fica parada nos 30% pelo tempo todo,
    # e "parado" e o sinal universal de travado.
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
        # MAX_TIME_S e WORKERS vem da RODADA quando a tela os manda. A tela
        # oferece os dois como controle, o corpo os envia e o banco os grava —
        # e este worker rodava `workers=8` fixo, ignorando o que o usuario
        # escolheu. Controle que a tela promete e o motor descarta e pior que
        # controle ausente: o usuario ajusta, o numero muda por outro motivo, e
        # ele aprende uma relacao que nao existe.
        #
        # `--tempo` da linha de comando continua valendo como TETO da demo: nao
        # adianta a tela pedir 600s num worker de laptop.
        segundos = min(int(p.get("MAX_TIME_S") or tempo), tempo)
        nucleos = int(p.get("WORKERS") or 8)
        log(run_id, f"solver: max_time_s={segundos} workers={nucleos}")
        try:
            res = CP.resolver_por_sistema(cen, max_time_s=segundos, workers=nucleos)
        except KeyError as e:
            # `KeyError: 'Araruama Leste1'` — o nome de uma cidade, cru, e nada
            # mais. Chegava assim ate a tela, que nao tem como saber que o defeito
            # e do motor nem que a rodada e reexecutavel.
            #
            # SO TRADUZ quando a chave E UMA CIDADE do cenario. Sem esse teste,
            # qualquer `KeyError` interno do motor — outra causa, outro lugar —
            # viraria "cidade sem coluna", e o proximo defeito chegaria disfarcado
            # do anterior. Fora dessa forma, o erro segue cru: melhor um traceback
            # honesto que uma explicacao errada.
            chave = e.args[0] if len(e.args) == 1 else None
            if chave not in {n.cidade for n in cen.nos.values()}:
                raise
            # O que se sabe do defeito, e ele e do PACOTE, em
            # `otimizador_capex_cpsat63.resolver_por_sistema`: o reparo do teto
            # anual percorre TODAS as cidades (`for g in grupos`) indexando
            # `sel[g]`, enquanto o laco logo acima percorre `sel.items()`. Com a
            # selecao incompleta, a primeira cidade ausente estoura — o nome e so
            # a ordem de iteracao, nao ha nada de errado com a cidade.
            #
            # POR QUE a selecao fica incompleta e HIPOTESE, nao fato: `_extrai` e
            # chamado a partir de solves que so filtram `INFEASIBLE`, e o modelo
            # usa `AddExactlyOne`, entao uma solucao valida nao produziria selecao
            # parcial. Ver a ressalva em `dev/patches/motor_status_do_solver.md`.
            #
            # Nao da para consertar daqui: o estouro acontece dentro do motor,
            # antes de ele devolver qualquer coisa. O que da e nao repassar um
            # `KeyError` nu.
            raise RuntimeError(
                f"O solver falhou ao reparar o teto anual: a cidade '{chave}' ficou "
                "sem coluna selecionada. É um defeito conhecido do motor "
                "(otimizador_capex_cpsat63), observado quando o tempo de solver não "
                "basta para a janela pedida. Tente de novo com MAX_TIME_S maior ou "
                "janela de CAPEX menor. A rodada pode ser reexecutada."
            ) from e
    finally:
        parar.set()
        batedor.join(timeout=2)
    log(run_id, f"solver: {res.get('milp_status')}  VPL={res.get('vpl'):,.0f}")
    # GRAVA O DESFECHO DO SOLVER ANTES DE MATERIALIZAR.
    #
    # Aconteceu: 68 minutos de solver, o plano pronto, e o processo morreu na
    # materializacao. `otim_*` ficou vazio e a tela mostrou so "ERRO" — o VPL e as
    # obrigatorias existiam apenas numa linha de log, num terminal. Fechar a janela
    # apagava o unico registro do que a rodada tinha achado.
    #
    # Aqui o numero sobrevive a falha do passo seguinte. Nao substitui o resultado
    # publicado (que tem a cascata inteira); e o que da para dizer quando a
    # publicacao nao acontece.
    anotar_solver(run_id, res)
    # O solver e uma chamada nativa: um cancelamento durante ele so pode ser
    # atendido quando ele volta. E por isso que a espera maxima do cancelamento e
    # o `MAX_TIME_S` da rodada, e nao um numero que este worker escolha.
    conferir_cancelamento(run_id)

    # `run_id` E o da fila: publicar com outro id faria a tela perder a rodada
    # que ela esta acompanhando.
    # A materializacao levou 86s numa medicao, com a barra parada em 92: "parado"
    # e o sinal universal de travado, e numa demonstracao alguem recarrega a
    # pagina. Mesmo batedor do solver, na faixa que sobra.
    andar(run_id, MATERIALIZANDO)
    parar_mat = threading.Event()

    def acompanhar_mat() -> None:
        passo = MATERIALIZANDO
        while not parar_mat.wait(4):
            passo = min(passo + 1, PRONTO - 1)
            andar(run_id, passo)

    bat_mat = threading.Thread(target=acompanhar_mat, daemon=True)
    bat_mat.start()
    try:
        tabs = P.materializar(cen, res, banco=R.BANCO, run_id=run_id, params=p)
    finally:
        parar_mat.set()
        bat_mat.join(timeout=2)
    # O ULTIMO ponto de conferencia, e o que justifica os outros: `publicar` e o
    # unico passo irreversivel desta funcao. Cancelar depois dele nao seria
    # cancelar — seria publicar e depois dizer que nao publicou.
    conferir_cancelamento(run_id)
    # O NOME E O AUTOR VEM DE `run_request`, e nao de `params`.
    #
    # Estava `p.get("ROTULO") or f"{unidade} — pela tela"`, e o efeito era pior
    # que perder o nome: `ROTULO` nunca esta em `params` — ele foi tirado de la
    # de proposito, porque o job valida `params` contra `MAPA_PARAMS` +
    # `CHAVES_DO_JOB` e uma chave desconhecida mata a rodada
    # (`migracoes/004_run_request_rotulo.sql`). Entao o `or` disparava SEMPRE, e
    # toda rodada era publicada com o rotulo generico "uA1 — pela tela".
    #
    # O nome que a pessoa digitou sumia, e no lugar dele aparecia um texto
    # plausivel que ninguem escreveu — no historico, que existe justamente para
    # distinguir uma rodada da outra. Medido: as 27 rodadas publicadas do banco
    # local tinham so tres rotulos diferentes, todos no formato do fallback.
    #
    # `usuario` idem: `USUARIO` ESTA em `params`, mas `solicitado_por` e a coluna
    # que o backend usa para a posse da rodada (`app/api/deps.py`). Ler as duas
    # fontes para o mesmo fato e como elas divergirem um dia.
    PUB.publicar(
        tabs,
        pg=R.PG,
        criar_schema=False,
        verbose=False,
        rotulo=ped["rotulo"],
        usuario=ped["solicitado_por"],
    )
    log(run_id, f"publicado: {len(tabs)} tabelas")


#: Renova o lock a cada 20s. O lock dura 1 minuto (`dev/servicebus.json`), entao
#: tres tentativas cabem antes de ele expirar — margem para uma falhar sem que a
#: mensagem volte para a fila com a rodada ainda em execucao.
INTERVALO_RENOVACAO = 20


async def _renovar(rx, msg) -> None:
    """Segura o lock da mensagem enquanto a rodada processa.

    Sem isto, completar no fim seria impossivel: o lock expiraria no meio da
    materializacao e a mesma simulacao rodaria duas vezes.

    Falha de renovacao nao interrompe a rodada — ela segue, e o pior caso vira o
    caso antigo (mensagem reentregue). Derrubar o trabalho por causa do lock seria
    trocar um problema raro por um certo.
    """
    while True:
        await asyncio.sleep(INTERVALO_RENOVACAO)
        try:
            await rx.renew_message_lock(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log("", f"nao consegui renovar o lock da mensagem: {type(e).__name__}: {e}")
            return


#: Estados em que a rodada JA tem desfecho — reprocessa-la seria refazer trabalho
#: e, pior, sobrescrever um resultado publicado.
TERMINAIS = ("SUCESSO", "ERRO", "FALHOU_QUALIDADE", "CANCELADA")


def _desfecho(run_id: str) -> str | None:
    with R.eng.begin() as con:
        linha = con.execute(
            text("SELECT status FROM controle.run_status WHERE run_id = :r"),
            {"r": run_id},
        ).first()
    return linha[0] if linha else None


async def processar(msg, tempo: int) -> None:
    corpo = json.loads(str(msg))
    run_id = corpo["run_id"]

    # REENTREGA: a mensagem so volta para a fila quando o worker anterior morreu
    # sem completar. Se aquela execucao chegou ao fim mesmo assim, refazer o
    # trabalho sobrescreveria um resultado ja publicado.
    ja = _desfecho(run_id)
    if ja in TERMINAIS:
        log(run_id, f"reentregue, mas ja esta {ja} — ignorada")
        return

    # A RODADA AINDA EXISTE? Isto precisa vir antes de QUALQUER escrita de status.
    #
    # `run_status` tem FK para `run_request`, entao um `run_id` excluido faz
    # estourar tudo que escreve status — inclusive o `marcar(ERRO)` do `except`
    # abaixo, que seria a saida natural. A excecao escapa de `processar`, o laco
    # nao chega ao `complete_message`, o lock expira, e a mensagem volta a cada
    # minuto ate o `MaxDeliveryCount` manda-la para dead-letter: tres tracebacks e
    # uma vaga do executor ocupada em cada tentativa. Visto ao vivo com uma
    # mensagem orfa que sobrou de uma rodada apagada.
    #
    # `executar()` ja faz esta checagem, mas ela e inalcancavel — `marcar(RODANDO)`
    # morre antes de chegar la. Ela fica onde esta: protege quem chamar `executar`
    # por outro caminho.
    #
    # `return`, e nao `raise`: sair normalmente e o que faz o laco COMPLETAR a
    # mensagem, como no `ignorada` acima. Reentregar nunca vai ajudar — a
    # `run_request` nao volta a existir.
    if pedido(run_id) is None:
        log(run_id, "nao esta em controle.run_request (rodada excluida?) — descartada")
        return

    log(run_id, f"RECEBIDA (unidade {corpo.get('unidade_id')})")
    marcar(run_id, "RODANDO", progresso=1)
    reivindicar(run_id)
    try:
        # CADA RODADA NUM PROCESSO PRÓPRIO, e não numa thread.
        #
        # Era `asyncio.to_thread`, e com `--paralelo 2` o processo morria com
        # SEGMENTATION FAULT. A causa não era memória — medi: a maior unidade
        # (11.525 obras) roda sozinha com pico de 372 MB, numa máquina de 31,7 GB.
        # Era o solver nativo e a materialização rodando em DUAS THREADS do mesmo
        # interpretador, sobre estado nativo que não é compartilhável.
        #
        # Processo separado dá as três coisas que faltavam:
        #
        #   isolamento    um segfault mata AQUELA rodada; o executor continua de
        #                 pé e as outras seguem
        #   paralelismo   sem GIL, quatro rodadas usam quatro núcleos de verdade
        #   contabilidade a memória de cada rodada é do processo dela
        #
        # O preço é o custo de subir o processo (no Windows, `spawn`: reimporta o
        # módulo), que some diante de uma rodada de minutos.
        laco = asyncio.get_running_loop()
        await laco.run_in_executor(POOL, executar, run_id, tempo)
        marcar(run_id, "SUCESSO", progresso=PRONTO)
        _carimbar_data_hora(run_id)
        log(run_id, "SUCESSO")
    except concurrent.futures.process.BrokenProcessPool as e:
        # O processo da rodada morreu de morte nativa (segfault, OOM do SO). Sem
        # isolamento isto teria derrubado o executor inteiro e as rodadas irmãs.
        marcar(run_id, "ERRO", "O processo desta rodada morreu (falha nativa no "
                               "solver ou na materialização).")
        log(run_id, f"ERRO: processo da rodada morreu ({type(e).__name__})")
    except Cancelada as c:
        # NAO marca nada: quem escreveu o status novo — a API no cancelamento, o
        # vigia no lease vencido — ja disse o que aconteceu, e sobrescrever aqui
        # apagaria o unico registro disso. O `finally` solta o lease e devolve a
        # vaga, que e o resto do que se deve a quem clicou.
        log(run_id, f"parou sem publicar: status virou {c.encontrado}")
    except Exception as e:  # noqa: BLE001 — a causa vai para o banco e para a tela
        marcar(run_id, "ERRO", f"{type(e).__name__}: {e}"[:500])
        log(run_id, f"ERRO: {type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        soltar(run_id)


def soltar_presas(limite_min: int = 30) -> int:
    """Marca ERRO nas rodadas RODANDO que este worker nao esta executando.

    A mensagem e completada ANTES de processar, de proposito: o lock do emulador
    e curto, e uma rodada de 45s o estouraria — a mensagem voltaria para a fila e
    a MESMA simulacao rodaria duas vezes. Rodar duas vezes e pior que nao rodar,
    porque as duas publicam.

    O preco dessa escolha e que morte do processo no meio deixa a rodada RODANDO
    para sempre, sem mensagem para reentregar. A tela fica girando e ninguem
    descobre. Isto varre esse resto na PARTIDA do worker: se ainda esta RODANDO
    quando um worker sobe, ninguem a esta executando.

    O conserto completo e lease no banco (`worker_id`, `lease_ate`) com renovacao
    — que e o que se faria em producao, onde ha varios consumidores. Aqui seria
    infraestrutura para um consumidor so: `limite_min` cobre o caso real (o
    processo morreu) sem inventar protocolo.
    """
    with R.eng.begin() as con:
        n = con.execute(
            text(
                """UPDATE controle.run_status
                      SET status = 'ERRO',
                          erro = 'Consumidor encerrou antes de terminar esta rodada '
                                 '(marcada na partida do worker seguinte).',
                          atualizado_em = now()
                    WHERE status = 'RODANDO'
                      AND atualizado_em < now() - make_interval(mins => :m)"""
            ),
            {"m": limite_min},
        ).rowcount
    return n or 0


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paralelo", type=int, default=1, help="rodadas simultaneas (default 1)")
    ap.add_argument("--tempo", type=int, default=45, help="segundos de solver por rodada")
    a = ap.parse_args()

    global POOL
    print(f"worker {WORKER_ID}")
    print(f"  fila `{FILA}`, {a.paralelo} em paralelo (1 processo cada), "
          f"{a.tempo}s de solver, codigo {VERSAO}")
    presas = soltar_presas()
    if presas:
        print(f"  {presas} rodada(s) presas em RODANDO de um worker anterior -> ERRO")
    print("Ctrl+C encerra.\n")

    POOL = concurrent.futures.ProcessPoolExecutor(max_workers=a.paralelo)
    limite = asyncio.Semaphore(a.paralelo)
    vivos: set[asyncio.Task] = set()
    espera = 1

    async def batida() -> None:
        """Anuncia o executor e renova os leases, para sempre.

        Fora do laço da fila de propósito: uma queda de conexão com o Service Bus
        não pode fazer o executor parecer morto. São duas dependências
        diferentes, e confundi-las faria a tela mentir sobre a capacidade
        disponível justo quando ela mais importa.
        """
        while True:
            try:
                bater(a.paralelo, len(vivos), None)
            except Exception as e:  # noqa: BLE001
                log("", f"batida falhou: {type(e).__name__}: {e}")
            await asyncio.sleep(BATIDA)

    coracao = asyncio.create_task(batida())
    bater(a.paralelo, 0, None)  # aparece na tela antes da primeira batida

    while True:
        try:
            async with ServiceBusClient.from_connection_string(CONN) as cli:
                async with cli.get_queue_receiver(FILA, max_wait_time=5) as rx:
                    espera = 1  # conectou: zera o recuo
                    while True:
                        for msg in await rx.receive_messages(
                            max_message_count=10, max_wait_time=5
                        ):
                            # COMPLETA NO FIM, e nao no comeco. Enquanto processa,
                            # um renovador segura o lock.
                            #
                            # Completar antes era o que perdia a rodada: no
                            # instante em que o worker pegava a mensagem, ela
                            # deixava de existir na fila. Worker que morresse
                            # depois disso — segmentation fault, conexao morta,
                            # Ctrl+C — deixava a rodada orfa em RODANDO ou
                            # PENDENTE, sem ninguem para retoma-la, e so um UPDATE
                            # a mao a tirava de la. Aconteceu tres vezes.
                            #
                            # A razao original era boa e continua valendo: o lock
                            # e curto (`dev/servicebus.json`: PT1M) e uma rodada
                            # leva minutos, entao sem renovacao a mensagem
                            # voltaria para a fila COM a rodada ainda em execucao,
                            # e a mesma simulacao rodaria duas vezes. Por isso a
                            # troca nao e mover a linha: e renovar o lock, e so
                            # entao completar.
                            async def tarefa(m=msg):
                                async with limite:
                                    renovador = asyncio.create_task(_renovar(rx, m))
                                    try:
                                        await processar(m, a.tempo)
                                    finally:
                                        renovador.cancel()
                                    # `processar` NUNCA levanta: ele grava SUCESSO
                                    # ou ERRO no banco. Chegar aqui significa que a
                                    # rodada tem desfecho, e completar e correto.
                                    # Morrer antes daqui deixa o lock expirar, e o
                                    # Service Bus reentrega (`MaxDeliveryCount: 3`,
                                    # depois dead-letter).
                                    try:
                                        await rx.complete_message(m)
                                    except Exception as e:  # noqa: BLE001
                                        # O receptor pode ter sido reciclado por
                                        # uma queda de conexao. A rodada ja tem
                                        # desfecho gravado; o guarda em `processar`
                                        # cuida de uma eventual reentrega.
                                        log("", f"nao consegui completar a mensagem: {e}")

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
    finally:
        # Sai da lista de executores. Sem isto, a tela contaria por meio minuto
        # uma capacidade que ja nao existe — e "ha 2 executores livres" quando nao
        # ha nenhum e pior que nao dizer nada.
        despedir()
