"""Acesso a `controle.*` e ao que a simulacao precisa de `input.*`.

Nomes de schema vem da config, e nao literais no SQL, porque `input`/`controle`
sao os nomes de producao mas o smoke test roda contra schemas de teste.
"""

import hashlib
import json
from typing import Any

from app.config import config
from app.dominio.status import Status
from app.infra import db
from app.infra.repositorios import pendencias


def _c() -> str:
    return config().schema_controle


def _i() -> str:
    return config().schema_input


async def unidade(unidade_id: str) -> dict[str, Any] | None:
    return await db.buscar_um(
        f"""SELECT unidade_id, unidade_name AS nome
             FROM {_i()}.unidade_regional WHERE unidade_id = $1""",
        unidade_id,
    )


async def pendencias_do_cadastro(unidade_id: str) -> int:
    """Quantos campos obrigatorios do cadastro ainda estao vazios.

    A conta vive em `repositorios/pendencias.py`, porque ela e a mesma da tela e
    precisa dar o MESMO numero: divergir faria o usuario ver "completo", apertar
    Iniciar e o servidor recusar sem dizer o que falta.
    """
    return (await pendencias.contar(unidade_id))["pendencias"]


def digest(params: dict[str, Any]) -> str:
    """A identidade do PEDIDO — dois pedidos iguais dão o mesmo digest.

    `sort_keys` porque a ordem das chaves num JSON não significa nada, e sem ele
    dois pedidos idênticos vindos de dois clientes dariam digests diferentes.

    `USUARIO` fica FORA da conta: dois analistas pedindo a mesma simulação da mesma
    unidade estão pedindo a mesma coisa, e rodar duas vezes gastaria cluster para
    produzir dois resultados idênticos. Quem pediu primeiro assina; o segundo é
    levado para a rodada que já existe.
    """
    limpo = {k: v for k, v in params.items() if k != "USUARIO"}
    bruto = json.dumps(limpo, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


async def rodada_em_voo(con: Any, unidade_id: str, params: dict[str, Any]) -> str | None:
    """Já existe uma rodada IGUAL desta unidade esperando ou executando?

    "Igual" é pelo conteúdo do pedido, não pela unidade: rodar a mesma unidade com
    parâmetros diferentes é o uso normal do produto — a tela de histórico existe
    para comparar cenários. O que não pode é o mesmo pedido virar duas execuções.
    """
    alvo = digest(params)
    linhas = await con.fetch(
        f"""SELECT r.run_id, r.params
              FROM {_c()}.run_request r
              JOIN {_c()}.run_status s USING (run_id)
             WHERE r.unidade = $1 AND s.status = ANY($2::text[])""",
        unidade_id,
        [Status.PENDENTE.value, Status.RODANDO.value],
    )
    for l in linhas:
        if digest(l["params"] or {}) == alvo:
            return l["run_id"]
    return None


async def abrir_rodada(
    *,
    run_id: str,
    unidade_id: str,
    params: dict[str, Any],
    usuario: str,
    rotulo: str | None,
) -> str:
    """`run_request` + `run_status` PENDENTE numa transacao so.

    Juntas porque o front consulta o status logo depois do 201: se houvesse um
    instante com request gravada e status ausente, a primeira consulta daria 404 e
    a tela mostraria "rodada não encontrada" para uma rodada que acabou de criar.

    Devolve o `run_id` que o chamador deve usar — que **pode não ser o que entrou**:
    se um pedido idêntico já estiver em voo, devolve o dele e não grava nada.

    O `pg_advisory_xact_lock` é o que torna isso correto sob concorrência. Sem ele,
    duas requisições simultâneas fazem a busca, nenhuma acha nada, e as duas
    inserem — que foi exatamente o que uma revisão reproduziu com dois `POST` em
    paralelo. O lock serializa POR UNIDADE (e não globalmente), então unidades
    diferentes seguem em paralelo, e ele cai sozinho no fim da transação.
    """
    async with db.transacao() as con:
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", unidade_id)

        existente = await rodada_em_voo(con, unidade_id, params)
        if existente:
            return existente

        await con.execute(
            f"""INSERT INTO {_c()}.run_request (run_id, unidade, params, solicitado_por)
                VALUES ($1, $2, $3, $4)""",
            run_id,
            unidade_id,
            # O dict vai CRU. O pool registra um codec de json/jsonb (ver
            # `infra/db.py`), entao o proprio asyncpg serializa. Passar
            # `json.dumps(...)` aqui serializava DUAS vezes e gravava um escalar
            # JSON — uma string — no lugar do objeto. O job leria `params` como
            # texto e nenhuma rodada funcionaria. So apareceu contra banco real.
            params,
            usuario,
        )
        await con.execute(
            f"""INSERT INTO {_c()}.run_status (run_id, status) VALUES ($1, $2)""",
            run_id,
            Status.PENDENTE.value,
        )
        # O `rotulo` (o nome que o usuario deu a rodada) NAO entra no `params`.
        #
        # Ele viajava ali ate a revisao mostrar o estrago: o job valida `params`
        # contra `MAPA_PARAMS` + `CHAVES_DO_JOB` e levanta ValueError em chave
        # desconhecida — `ROTULO` nao esta em nenhum dos dois. Ou seja, TODA rodada
        # com nome morria em ERRO, e a mensagem falaria de `params`, sem relacao
        # visivel com o campo "nome" que o usuario preencheu.
        #
        # Enquanto `controle.run_request` nao ganhar a coluna `rotulo` (esta na
        # lista de migracoes do README), o nome se perde no caminho: `otim_meta.rotulo`
        # sai vazio e o historico mostra a rodada sem nome. Perder o nome e ruim;
        # perder a rodada inteira e pior.
    return run_id


async def status(run_id: str) -> dict[str, Any] | None:
    return await db.buscar_um(
        f"""SELECT s.run_id, s.status, s.erro, s.atualizado_em, r.unidade
              FROM {_c()}.run_status s
              JOIN {_c()}.run_request r USING (run_id)
             WHERE s.run_id = $1""",
        run_id,
    )


async def marcar(run_id: str, novo: Status, erro: str | None = None) -> None:
    async with db.pool().acquire() as con:
        await con.execute(
            f"""INSERT INTO {_c()}.run_status (run_id, status, erro, atualizado_em)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (run_id) DO UPDATE
                  SET status = EXCLUDED.status,
                      erro = EXCLUDED.erro,
                      atualizado_em = now()""",
            run_id,
            novo.value,
            erro,
        )
