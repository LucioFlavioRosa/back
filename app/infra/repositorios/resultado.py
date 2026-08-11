"""Leitura das 14 `public.otim_*`.

Duas regras do contrato moldam todo SELECT daqui:

  - **`null` significa "não existe", e nunca 0.** Ocupacao de ETE com capacidade 0
    vai como `null`, e a tela mostra "—". Mandar 0 faria a tela afirmar que a ETE
    esta vazia, quando o fato e que a conta nao existe. Por isso as divisoes usam
    `NULLIF(divisor, 0)` em vez de `COALESCE(..., 0)`.
  - **os totais ja vem reconciliados.** O front nao recomputa nada — nao soma as
    parcelas da cascata para conferir o VPL. Quem garante o fechamento e o portao
    de qualidade da rodada, antes de publicar.
"""

from typing import Any

from app.config import config
from app.infra import db


def _p() -> str:
    return config().schema_resultado


def _c() -> str:
    return config().schema_controle


def _i() -> str:
    return config().schema_input


# `otim_meta.regional` NAO guarda a regional: guarda o NOME DA UNIDADE.
#
# Vem do motor (`otimizador_capex_v62.py:1117`), onde o no e construido com
# `No(sb, cidade, sistema, uni_name[...], jusante)` — o quarto argumento se chama
# `regional` e recebe o nome da unidade. O nome da coluna e heranca de quando o
# escopo da analise era a regional; a analise virou por unidade e o nome ficou.
#
# Consequencias que isso teve aqui, as duas corrigidas nesta leva:
#   - `unidadeId` devolvia um NOME, entao `GET /runs?unidade=<id>` nunca casava:
#     comparava id contra nome e o filtro do historico voltava vazio;
#   - `unidadeNome` estava certo por acidente.
#
# O join com `input.unidade_regional` resolve o id a partir do nome. E remendo, e
# tem duas fragilidades que precisam estar escritas: depende de o nome da unidade
# ser unico, e renomear uma unidade desliga as rodadas antigas dela. A correcao
# durável e o job publicar `unidade_id` em `otim_meta` — se a rodada e imutavel, a
# identidade dela deveria ser congelada junto, e nao reconstruida por nome a cada
# leitura. Esta pedido no README.
_ID_DA_UNIDADE = """
    LEFT JOIN {i}.unidade_regional u ON u.unidade_name = m.regional
"""


async def historico(
    unidade: str | None = None,
    usuario: str | None = None,
    favoritas: set[str] | None = None,
) -> list[dict[str, Any]]:
    linhas = await db.buscar(
        f"""SELECT h.run_id, h.rotulo, h.usuario, h.data_hora, h.milp_status,
                   h.anos_capex, h.orcamento_total, h.vpl, h.capex_total,
                   h.obras_construidas, h.obras_total, h.cobertura_final_pct,
                   h.metas_total, h.metas_nao_atingidas, h.tempo_s,
                   m.receita_total, m.opex_total,
                   m.regional, m.unidade_id, m.base_receita_param, m.usar_cts,
                   m.foco_cobertura, m.incluir_industrial,
                   -- O PEDIDO, como ele foi feito. Ver `_pedido`.
                   -- LEFT JOIN: rodada publicada direto pelo pacote (sem passar
                   -- pela fila) nao tem `run_request`, e continua aparecendo na
                   -- lista — sem o pedido, e nao ausente da tela.
                   rq.params AS pedido
              FROM {_p()}.otim_vw_historico h
              LEFT JOIN {_c()}.run_request rq ON rq.run_id = h.run_id
              JOIN LATERAL (
                   SELECT regional,
                          (SELECT u.unidade_id FROM {_i()}.unidade_regional u
                            WHERE u.unidade_name = otim_meta.regional) AS unidade_id,
                          receita_total, opex_total,
                          params_extra->>'BASE_RECEITA'      AS base_receita_param,
                          (params_extra->>'USAR_CTS')::bool  AS usar_cts,
                          (params_extra->>'FOCO_COBERTURA')::float AS foco_cobertura,
                          (params_extra->>'INCLUIR_INDUSTRIAL')::bool AS incluir_industrial
                     FROM {_p()}.otim_meta WHERE run_id = h.run_id
              ) m ON true
             -- casa por id OU por nome: o front manda o id, mas um script de
             -- operacao provavelmente manda o nome, que e o que esta na coluna.
             WHERE ($1::text IS NULL OR m.unidade_id = $1 OR m.regional = $1)
               AND ($2::text IS NULL OR h.usuario  = $2)
             ORDER BY h.data_hora DESC""",
        unidade,
        usuario,
    )
    return [_resumo(l, favoritas or set()) for l in linhas]


async def em_voo(
    unidade: str | None, usuario: str | None, favoritas: set[str] | None = None
) -> list[dict[str, Any]]:
    """As rodadas PEDIDAS que ainda nao publicaram resultado.

    O historico saia so de `otim_vw_historico`, que le `public.otim_*` — ou seja,
    so aparecia depois de PUBLICAR. Enquanto a rodada estava PENDENTE ou RODANDO
    ela nao existia para a lista, e uma que morreu em ERRO nunca aparecia.

    O efeito pratico: quem fechava o modal de acompanhamento perdia a rodada de
    vista. Nao havia onde ver "o que esta rodando agora" nem "o que falhou hoje" —
    a tela mais operacional do produto era cega justamente para o estado
    operacional.

    Sai de `controle.*`, que e onde a rodada nasce, e traz os campos que a lista
    consegue mostrar. `metricas` fica AUSENTE, pela mesma razao que na rodada
    inviavel: nao ha plano ainda, e um bloco de zeros seria lido como um plano que
    nao construiu nada.
    """
    linhas = await db.buscar(
        f"""SELECT r.run_id, r.unidade, r.solicitado_por, r.solicitado_em,
                   r.rotulo, r.params AS pedido,
                   s.status, s.progresso, s.erro,
                   u.unidade_name
              FROM {_c()}.run_request r
              JOIN {_c()}.run_status  s USING (run_id)
              LEFT JOIN {_i()}.unidade_regional u ON u.unidade_id = r.unidade
             WHERE NOT EXISTS (
                   SELECT 1 FROM {_p()}.otim_meta m WHERE m.run_id = r.run_id)
               AND ($1::text IS NULL OR r.unidade = $1)
               AND ($2::text IS NULL OR r.solicitado_por = $2)
             ORDER BY r.solicitado_em DESC""",
        unidade,
        usuario,
    )
    return [
        {
            "runId": l["run_id"],
            "nome": l.get("rotulo"),
            "unidadeId": l["unidade"],
            "unidadeNome": l.get("unidade_name") or l["unidade"],
            "dataHora": l["solicitado_em"].isoformat() if l.get("solicitado_em") else None,
            "autor": l.get("solicitado_por"),
            "duracaoS": None,
            "status": l["status"],
            "progresso": l.get("progresso") or 0,
            "erro": l.get("erro"),
            "favorita": l["run_id"] in (favoritas or set()),
            "publicada": False,
            # A rodada em voo tem pedido desde o `POST` — e e a UNICA coisa que
            # da para mostrar dela: metricas e parametros so existem depois da
            # publicacao. Sem isto, abrir os detalhes de uma rodada em execucao
            # nao mostraria nada alem do nome.
            "pedido": _pedido(l.get("pedido")),
        }
        for l in linhas
    ]


#: Chaves do pedido que a resposta JA expoe como campo proprio. Repeti-las dentro
#: de `pedido` faria a tela mostrar a unidade duas vezes, e o autor duas vezes —
#: com nomes tecnicos na segunda, que e pior que nao mostrar.
_PEDIDO_REDUNDANTE = frozenset({"UNIDADE", "USUARIO", "REGIONAL"})


def _pedido(bruto: Any) -> dict[str, Any] | None:
    """As variaveis com que a rodada foi PEDIDA.

    ## Por que ela existe, alem de `parametros`

    `parametros` traz seis campos tipados, que sao os que o card do historico
    mostra e a tela sabe formatar. Mas o formulario de simulacao tem mais de vinte
    (`dominio/parametros.CHAVES_ACEITAS`): penalidade de cobertura, curva de
    adocao, peso por cidade, anos extras de conclusao, teto de execucao, solver,
    workers. Nenhum deles aparecia em lugar nenhum depois de a rodada existir.

    Quem abre "o que foi usado nesta simulacao" e alguem tentando reproduzir ou
    explicar um resultado. Seis de vinte e tres responde a pergunta errada.

    ## De onde sai, e o que isso implica

    De `controle.run_request.params` — o PEDIDO, e nao o que o motor ecoou. Sao
    coisas diferentes: `otim_meta.params_extra` guarda cinco chaves que o job
    escolheu devolver, enquanto o pedido e o que a pessoa mandou.

    Consequencia honesta: rodada publicada sem passar pela fila (o pacote de
    producao publica direto) nao tem pedido, e o campo vem `null`. A tela diz
    isso em vez de inventar.
    """
    if not isinstance(bruto, dict):
        return None
    return {k: v for k, v in bruto.items() if k not in _PEDIDO_REDUNDANTE} or None


def _resumo(l: dict[str, Any], favoritas: set[str]) -> dict[str, Any]:
    """Molda uma linha para o `RunResumo` do front.

    `metricas` fica AUSENTE quando a rodada e INFEASIBLE — nao vazia, nem zerada.
    A tela usa a ausencia para dizer "não houve plano", e um bloco de zeros ali
    seria lido como um plano que nao construiu nada, que e outra coisa.

    `favoritas` sao as de QUEM PEDIU a lista, e nao as do dono da rodada. A
    diferenca so aparece no `admin`, que ve as rodadas dos outros: a estrela na
    tela dele e a dele.
    """
    situacao = _status_do_solver(l.get("milp_status"))
    inviavel = situacao == "INFEASIBLE"
    resumo: dict[str, Any] = {
        "runId": l["run_id"],
        "nome": l.get("rotulo"),
        # Sem cadastro correspondente, o id cai para o nome: e melhor um id feio
        # que um `null` que a tela usaria para montar um link quebrado.
        "unidadeId": l.get("unidade_id") or l.get("regional"),
        "unidadeNome": l.get("regional"),
        "dataHora": l["data_hora"].isoformat() if l.get("data_hora") else None,
        "autor": l.get("usuario"),
        "duracaoS": l.get("tempo_s"),
        "status": situacao,
        "favorita": l["run_id"] in favoritas,
        # A tela precisa distinguir "terminou" de "ainda esta acontecendo" sem
        # deduzir pelo status: `em_voo` devolve `publicada: False`, e so a rodada
        # publicada tem drill-down para oferecer.
        "publicada": True,
        "pedido": _pedido(l.get("pedido")),
        "parametros": {
            "baseReceita": l.get("base_receita_param"),
            "usarCts": l.get("usar_cts"),
            "janelaCapex": l.get("anos_capex"),
            "orcamento": l.get("orcamento_total"),
            "focoCobertura": l.get("foco_cobertura"),
            "incluirIndustrial": l.get("incluir_industrial"),
        },
    }
    if not inviavel:
        atingidas = (l.get("metas_total") or 0) - (l.get("metas_nao_atingidas") or 0)
        resumo["metricas"] = {
            "vpl": l.get("vpl"),
            "capex": l.get("capex_total"),
            "usoOrcamentoPct": _pct(l.get("capex_total"), l.get("orcamento_total")),
            "obrasConstruidas": l.get("obras_construidas"),
            "obrasTotal": l.get("obras_total"),
            "coberturaFimPct": l.get("cobertura_final_pct"),
            "metasAtingidas": atingidas,
            "metasTotal": l.get("metas_total"),
            # EBITDA nominal do plano: receita operacional menos OPEX. Sai do
            # proprio `otim_meta` para nao precisar somar `otim_ano` por rodada
            # numa listagem que pode ter centenas de linhas.
            "ebitdaTotal": (l.get("receita_total") or 0) - (l.get("opex_total") or 0),
        }
    return resumo


def _status_do_solver(milp: str | None) -> str:
    """`OTIMO` / `VIAVEL(...)` / `SEM SOLUCAO(...)`  ->  o vocabulario do front.

    O CP-SAT deste pacote NUNCA devolve 'OPTIMAL'/'FEASIBLE': ele devolve
    `OTIMO`, `OTIMO | OBRIG 3/3`, `VIAVEL(limite de tempo)` e `SEM SOLUCAO(3)`.
    Tratar tudo que nao e "sem solucao" como OPTIMAL apagava a distincao que mais
    importa ao usuario — uma rodada que parou no limite de tempo tem plano VIAVEL,
    e nao otimo, e a tela precisa dizer isso antes de alguem aprovar o numero.
    """
    s = (milp or "").upper()
    if s.startswith("SEM SOLUCAO"):
        return "INFEASIBLE"
    if s.startswith("VIAVEL"):
        return "FEASIBLE"
    return "OPTIMAL"


def _pct(parte: float | None, total: float | None) -> float | None:
    """Divisao que devolve None quando a conta nao existe — nunca 0. Ver §2.3."""
    if parte is None or not total:
        return None
    return round(parte / total * 100, 1)


async def meta(run_id: str) -> dict[str, Any] | None:
    linha = await db.buscar_um(
        f"""SELECT m.*, u.unidade_id
              FROM {_p()}.otim_meta m
              {_ID_DA_UNIDADE.format(i=_i())}
             WHERE m.run_id = $1""",
        run_id,
    )
    if not linha:
        return None
    return {
        "runId": linha["run_id"],
        "nome": linha.get("rotulo"),
        "unidadeId": linha.get("unidade_id") or linha.get("regional"),
        "unidadeNome": linha.get("regional"),
        "dataHora": linha["data_hora"].isoformat() if linha.get("data_hora") else None,
        "autor": linha.get("usuario"),
        "status": _status_do_solver(linha.get("milp_status")),
        "statusTexto": linha.get("milp_status"),
        # `kpis` alimenta a faixa de numeros do nivel global. O contrato exige o
        # bloco inteiro; faltando um campo, a tela mostra "—" onde ha dado.
        "kpis": {
            "vpl": linha.get("vpl"),
            "capexTotal": linha.get("capex_total"),
            "opexTotal": linha.get("opex_total"),
            "receitaTotal": linha.get("receita_total"),
            "obrasConstruidas": linha.get("obras_construidas"),
            "obrasTotal": linha.get("obras_total"),
            "obrigatoriasConstruidas": linha.get("obrig_construidas"),
            "obrigatoriasTotal": linha.get("obrig_total"),
            "subbaciasFaturando": linha.get("subbacias_faturando"),
            "subbaciasTotal": linha.get("subbacias_total"),
            "coberturaFimPct": linha.get("cobertura_final_pct"),
            "metasAtingidas": (linha.get("metas_total") or 0)
            - (linha.get("metas_nao_atingidas") or 0),
            "metasTotal": linha.get("metas_total"),
        },
        "parametros": {
            "baseReceita": (linha.get("params_extra") or {}).get("BASE_RECEITA"),
            "usarCts": (linha.get("params_extra") or {}).get("USAR_CTS"),
            "janelaCapex": linha.get("anos_capex"),
            "orcamento": linha.get("orcamento_total"),
            "focoCobertura": linha.get("foco_cobertura"),
            "incluirIndustrial": (linha.get("params_extra") or {}).get("INCLUIR_INDUSTRIAL"),
        },
    }


async def excluir(run_id: str) -> bool:
    """`ON DELETE CASCADE` leva as 13 tabelas de detalhe junto — por isso o DELETE
    e so em `otim_meta`."""
    async with db.transacao() as con:
        r = await con.execute(f"DELETE FROM {_p()}.otim_meta WHERE run_id = $1", run_id)
        # O CONTROLE sai junto. Sem isto, `GET /runs/{id}/status` seguia
        # respondendo SUCESSO para uma rodada cujo resultado nao existe mais — o
        # front pararia o polling satisfeito e mandaria o usuario para uma tela
        # 404. Duas fontes discordando sobre a mesma rodada e pior que qualquer
        # uma das duas sozinha.
        ctrl = config().schema_controle
        await con.execute(f"DELETE FROM {ctrl}.run_diagnostico WHERE run_id = $1", run_id)
        await con.execute(f"DELETE FROM {ctrl}.run_status WHERE run_id = $1", run_id)
        await con.execute(f"DELETE FROM {ctrl}.run_request WHERE run_id = $1", run_id)
        # As marcas de favorita de TODOS os usuarios. Elas nao tem FK — nao ha uma
        # tabela unica com todas as rodadas para apontar (ver `009_favoritas.sql`)
        # —, entao a limpeza e aqui ou nao acontece.
        await con.execute(f"DELETE FROM {ctrl}.run_favorita WHERE run_id = $1", run_id)
    return r != "DELETE 0"


async def favoritas_de(usuario: str) -> set[str]:
    """Os `run_id` que ESTA pessoa marcou.

    Um `SELECT` por listagem, e nao um join na consulta do historico: sao duas
    consultas diferentes (`historico` e `em_voo`) alimentando a mesma lista, e o
    conjunto serve as duas. Ele tambem e pequeno por natureza — favorita e curadoria
    manual, nao acumulo.
    """
    linhas = await db.buscar(
        f"SELECT run_id FROM {_c()}.run_favorita WHERE usuario = $1", usuario
    )
    return {l["run_id"] for l in linhas}


async def favoritar(run_id: str, usuario: str) -> None:
    """Marca. Idempotente pela chave composta: duplo clique nao vira erro."""
    async with db.pool().acquire() as con:
        await con.execute(
            f"""INSERT INTO {_c()}.run_favorita (run_id, usuario) VALUES ($1, $2)
                ON CONFLICT (run_id, usuario) DO NOTHING""",
            run_id,
            usuario,
        )


async def desfavoritar(run_id: str, usuario: str) -> None:
    """Desmarca. Tambem idempotente: desmarcar o que nao esta marcado e sucesso,
    porque o estado que o usuario pediu e o estado em que ele fica."""
    async with db.pool().acquire() as con:
        await con.execute(
            f"DELETE FROM {_c()}.run_favorita WHERE run_id = $1 AND usuario = $2",
            run_id,
            usuario,
        )
