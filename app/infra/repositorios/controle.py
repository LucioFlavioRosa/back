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


async def acesso(login: str) -> list[dict[str, Any]]:
    """As concessoes deste login: papel + escopo (regional, unidade, ou total).

    Sem cache de proposito: e busca por indice numa tabela pequena, e cache aqui
    faria revogacao demorar a valer. Se um dia pesar, o lugar e um TTL curto —
    nunca cache eterno por processo, que e o jeito de alguem demitido continuar
    entrando ate o proximo deploy.
    """
    return await db.buscar(
        f"""SELECT papel, regional_id, unidade_id
              FROM {_c()}.usuario_acesso
             WHERE lower(login) = lower($1)""",
        login,
    )


async def unidades_da_regional(regional_id: str) -> list[str]:
    """Expande uma concessao por regional nas unidades dela."""
    linhas = await db.buscar(
        f"SELECT unidade_id FROM {_i()}.unidade_regional WHERE regional_id = $1",
        regional_id,
    )
    return [l["unidade_id"] for l in linhas]


async def de_quem(run_id: str) -> dict[str, Any] | None:
    """Dono E unidade da rodada — as duas coisas que decidem quem pode ve-la.

    Vinham separadas, e a unidade nao vinha: o guarda conferia so o dono. Um
    `admin` de uma regional abria rodada de outra, e qualquer pessoa abria uma
    rodada propria de unidade cuja concessao ja tinha sido revogada.
    """
    linha = await db.buscar_um(
        f"""SELECT coalesce(r.solicitado_por, m.usuario) AS dono,
                   coalesce(r.unidade, u.unidade_id)     AS unidade
              FROM (SELECT $1::text AS run_id) x
              LEFT JOIN {_c()}.run_request r USING (run_id)
              LEFT JOIN {config().schema_resultado}.otim_meta m USING (run_id)
              LEFT JOIN {_i()}.unidade_regional u ON u.unidade_name = m.regional""",
        run_id,
    )
    return dict(linha) if linha else None


async def dono(run_id: str) -> str | None:
    """Quem pediu esta rodada.

    Olha o PEDIDO primeiro e a publicacao depois. O pedido existe desde o instante
    do `POST` — inclusive enquanto a rodada esta em voo e ainda nao ha linha em
    `otim_meta` —, e e ele que registra quem apertou o botao. A publicacao serve de
    reserva para rodada carregada por script, que nasce publicada sem passar pela
    fila.

    `None` quando nao ha nem um nem outro: rodada inexistente, ou anterior ao
    registro de autoria.
    """
    linha = await db.buscar_um(
        f"""SELECT coalesce(r.solicitado_por, m.usuario) AS dono
              FROM (SELECT $1::text AS run_id) x
              LEFT JOIN {_c()}.run_request r USING (run_id)
              LEFT JOIN {config().schema_resultado}.otim_meta m USING (run_id)""",
        run_id,
    )
    return (linha or {}).get("dono")


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

    `USUARIO` ENTRA na conta, e isto já foi o contrário.

    A versão anterior o excluía com um argumento que era bom na época: dois
    analistas pedindo a mesma simulação da mesma unidade pedem a mesma coisa, e
    rodar duas vezes gasta cluster para produzir dois resultados idênticos — então
    o segundo era levado para a rodada que já existia.

    Isso dependia de as rodadas serem COMPARTILHADAS. Desde que a posse passou a
    ser por pessoa (cada um vê as suas; `admin` vê todas), "ser levado para a
    rodada que já existe" virou uma promessa que o serviço nega em seguida:

        Fulano   POST /runs  -> 201  run_X
        Ciclana  POST /runs  -> 200  run_X     (a rodada do Fulano)
        Ciclana  GET  run_X  -> 404            (que não é dela)

    Um `runId` que quem recebeu não pode abrir é pior que um 409: o serviço diz
    "pronto, é essa" e depois nega que exista.

    O preço de incluir: duas execuções do cluster quando duas pessoas pedem
    exatamente a mesma coisa ao mesmo tempo. É raro — e a alternativa que
    economizaria (uma execução com uma tabela de solicitantes, autorizando todos)
    resolve o gasto sem resolver a contradição, porque continua misturando o que a
    regra de visibilidade separou.

    A deduplicação continua fazendo o que ela existe para fazer: duplo clique,
    retry do navegador e reenvio do SDK são o MESMO usuário, e continuam caindo na
    mesma rodada.
    """
    bruto = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
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
            f"""INSERT INTO {_c()}.run_request
                    (run_id, unidade, params, solicitado_por, rotulo)
                VALUES ($1, $2, $3, $4, $5)""",
            run_id,
            unidade_id,
            # O dict vai CRU. O pool registra um codec de json/jsonb (ver
            # `infra/db.py`), entao o proprio asyncpg serializa. Passar
            # `json.dumps(...)` aqui serializava DUAS vezes e gravava um escalar
            # JSON — uma string — no lugar do objeto. O job leria `params` como
            # texto e nenhuma rodada funcionaria. So apareceu contra banco real.
            params,
            usuario,
            rotulo,
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
        # Ele vai em COLUNA PROPRIA (`migracoes/004_run_request_rotulo.sql`).
        # Antes se perdia entre o POST e a publicacao — o que so incomodou quando o
        # historico passou a mostrar as rodadas EM VOO: a lista exibia linhas sem
        # nome durante toda a execucao, justamente quando ha varias ao mesmo tempo
        # e o nome e a unica coisa que as distingue.
    return run_id


async def status(run_id: str) -> dict[str, Any] | None:
    return await db.buscar_um(
        # `progresso` EXIGE a coluna em `run_status` (migracoes/002_progresso.sql;
        # o /readyz recusa o pod se ela faltar).
        # O front ja tinha barra e nome de etapa por faixa; sem a coluna o
        # endpoint devolvia 0 sempre e a barra saltava de 0 a 100, prometendo um
        # acompanhamento que nao existia.
        f"""SELECT s.run_id, s.status, s.erro, s.progresso, s.atualizado_em, r.unidade
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
