"""Acesso a `controle.*` e ao que a simulacao precisa de `input.*`.

Nomes de schema vem da config, e nao literais no SQL, porque `input`/`controle`
sao os nomes de producao mas o smoke test roda contra schemas de teste.
"""

import json
from typing import Any

from app.config import config
from app.dominio.status import Status
from app.infra import db


def _c() -> str:
    return config().schema_controle


def _i() -> str:
    return config().schema_input


async def unidade(unidade_id: str) -> dict[str, Any] | None:
    return await db.buscar_um(
        f"SELECT unidade_id, nome FROM {_i()}.unidade_regional WHERE unidade_id = $1",
        unidade_id,
    )


async def pendencias_do_cadastro(unidade_id: str) -> int:
    """Quantos campos obrigatorios do cadastro ainda estao vazios.

    PENDENTE — hoje devolve 0, o que deixa QUALQUER rodada passar.

    A conta de verdade e a mesma que o front faz em `cadastro/domain` (é ela que
    acende os contadores do hub), e precisa ser reproduzida aqui em SQL, ficha por
    ficha: sub-bacias sem ligações/receita/vazão, ETEs sem capacidade, CTS sem
    componente, cidade sem régua de cobertura, metas sem alvo.

    Está explicito como zero em vez de "esquecido" porque o efeito de errar aqui é
    invisível: a rodada roda, o solver aceita cadastro incompleto, e o plano sai
    com números que ninguém sabe que estão errados.
    """
    return 0


async def abrir_rodada(
    *,
    run_id: str,
    unidade_id: str,
    params: dict[str, Any],
    usuario: str,
    rotulo: str | None,
) -> None:
    """`run_request` + `run_status` PENDENTE numa transacao so.

    Juntas porque o front consulta o status logo depois do 201: se houvesse um
    instante com request gravada e status ausente, a primeira consulta daria 404 e
    a tela mostraria "rodada não encontrada" para uma rodada que acabou de criar.
    """
    async with db.transacao() as con:
        await con.execute(
            f"""INSERT INTO {_c()}.run_request (run_id, unidade, params, solicitado_por)
                VALUES ($1, $2, $3::jsonb, $4)""",
            run_id,
            unidade_id,
            json.dumps(params, ensure_ascii=False),
            usuario,
        )
        await con.execute(
            f"""INSERT INTO {_c()}.run_status (run_id, status) VALUES ($1, $2)""",
            run_id,
            Status.PENDENTE.value,
        )
        # `rotulo` e o nome que o usuario deu a rodada. Ele vive em `otim_meta`, que
        # so existe depois da publicacao — entao viaja dentro do params, de onde o
        # job o repassa. Guardado aqui tambem seria uma segunda verdade.
        if rotulo:
            await con.execute(
                f"""UPDATE {_c()}.run_request
                       SET params = params || jsonb_build_object('ROTULO', $2::text)
                     WHERE run_id = $1""",
                run_id,
                rotulo,
            )


async def status(run_id: str) -> dict[str, Any] | None:
    return await db.buscar_um(
        f"""SELECT s.run_id, s.status, s.erro, s.atualizado_em, r.unidade
              FROM {_c()}.run_status s
              JOIN {_c()}.run_request r USING (run_id)
             WHERE s.run_id = $1""",
        run_id,
    )


async def marcar(run_id: str, novo: Status, erro: str | None = None) -> None:
    await db.buscar(
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
