"""A cascata: global → cidade → sistema → sub-bacia → elemento.

Mesmos cinco níveis do `leitor_v2.py` e da PARTE IV do notebook, com uma diferença
de fonte: lá o dado vinha de um dict de DataFrames em memória, aqui vem das 14
`public.otim_*` no Postgres. As agregações são as mesmas — o que o pandas fazia com
`groupby`, aqui é `GROUP BY`.

Três regras do contrato moldam tudo neste arquivo:

  - **`null` é "não existe", nunca 0** (§2.3). Divisão usa `NULLIF(divisor, 0)`,
    e não `COALESCE(..., 0)`: ocupação de ETE com capacidade zero vira `null`, e a
    tela mostra "—". Um `0%` ali afirmaria que a ETE está vazia, quando o fato é
    que a conta não existe.
  - **os totais já vêm reconciliados** (§2.2). O front não soma as parcelas da
    cascata para conferir o VPL. Quem garante o fechamento é o portão de qualidade
    da rodada, antes de publicar.
  - **`obraId` só quando existe ficha** (§3.8). Prometer um id que dá 404 é pior
    que não prometer nada — foi bug real nos módulos da ETE.

Nota sobre identificadores: as tabelas de resultado guardam cidade e sistema pelo
NOME (`otim_cidade.cidade`, `otim_sistema.sistema`), não por id. O contrato pede
`id` e `nome`; enquanto o job não publicar os ids do cadastro, os dois carregam o
mesmo texto. Está explícito aqui porque parece descuido e não é.
"""

from typing import Any

from app.config import config
from app.infra import db

#: `otim_obra.componente` guarda o CÓDIGO (`lig`, `rede`, `tro`...), e o contrato
#: mostra o nome por extenso. Descobri isso rodando uma simulação de verdade: com
#: dado semeado à mão eu tinha escrito o nome longo direto na tabela, e a tradução
#: nunca foi exercitada.
#:
#: A ordem é a canônica, de montante para jusante — e ela também dá o índice do
#: override de obra, que o front indexa por posição.
NOME_DO_COMPONENTE = {
    "lig": "Ligação de esgoto",
    "rede": "Rede coletora",
    "cts": "Coletor de tempo seco",
    "tro": "Tronco",
    "eee": "EEE",
    "lr": "Linha de recalque",
    "ete": "ETE",
    "ete_mod": "ETE (módulo)",
}

#: Ordem canônica, de montante para jusante. O contrato é explícito: Tronco, EEE e
#: Linha de recalque NUNCA são agrupados num "Transporte" — agrupar esconde
#: justamente o elo que costuma travar a cadeia.
ORDEM_COMPONENTES = [
    "Ligação de esgoto",
    "Rede coletora",
    "Coletor de tempo seco",
    "Tronco",
    "EEE",
    "Linha de recalque",
    "ETE",
    "ETE (módulo)",
]


def nome_componente(codigo: str | None) -> str:
    """Código -> nome de tela. Código desconhecido passa como veio: é melhor a tela
    mostrar `xyz` do que um vazio que parece dado faltando."""
    return NOME_DO_COMPONENTE.get(codigo or "", codigo or "")


def _p() -> str:
    return config().schema_resultado


def _pct(parte: float | None, total: float | None) -> float | None:
    if parte is None or not total:
        return None
    return round(parte / total * 100, 1)


def _escala_pct(fracao: float | None) -> float | None:
    """Fracao do motor (0.4) -> percentual do contrato (40.0). None continua None."""
    return None if fracao is None else round(fracao * 100, 1)


def _realizado_pct(cobertura: float | None, alvo_lig: float | None, pct_alvo: float | None) -> float | None:
    """Quanto se cobriu, na mesma escala do alvo.

    None quando a conta nao existe (cidade sem alvo de ligacoes), e nao 0 — que
    afirmaria cobertura nula onde nao ha meta com que comparar.
    """
    if cobertura is None or not alvo_lig or pct_alvo is None:
        return None
    return round(cobertura / alvo_lig * pct_alvo * 100, 1)


def _meta(m: dict[str, Any]) -> dict[str, Any]:
    """Uma linha de `otim_meta_cobertura` no formato do contrato.

    Fica em funcao propria porque DUAS telas a pedem: o cartao-grafico do nivel 1
    (`cidades`) e a cidade aberta (`cidade`). Duas copias da mesma conversao
    divergiriam no dia em que uma das duas ganhasse um campo.

    `pct_alvo` vem do motor como FRACAO (0.4); o contrato pede percentual (40.0) —
    campos `Pct` vao de 0 a 100. E o realizado e o alvo reescalado pela razao entre
    o que se cobriu e o que se devia cobrir: 524 de 400 ligacoes com alvo de 40%
    da 52,4%.
    """
    return {
        "ano": m["ano"],
        "alvoPct": _escala_pct(m["pct_alvo"]),
        "realizadoPct": _realizado_pct(
            m["cobertura_ligacoes"], m["alvo_ligacoes"], m["pct_alvo"]
        ),
        "atingida": m["atingida"],
        "dentroDaJanela": m["dentro_janela_capex"],
    }


def _situacao(linha: dict[str, Any]) -> str:
    """`construida | nao-construida | terceiro | sem-obra`.

    A ordem dos testes importa: uma obra de terceiro que já existe é `terceiro`, e
    não `construida` — quem paga muda a leitura do plano.
    """
    if linha.get("obra_id") is None:
        return "sem-obra"
    if (linha.get("responsavel") or "").lower().startswith("terceiro"):
        return "terceiro"
    return "construida" if linha.get("construida") else "nao-construida"


async def _fim_capex(run_id: str) -> int | None:
    """Último ano da janela de CAPEX — vira linha de referência em vários gráficos."""
    linha = await db.buscar_um(
        f"SELECT ano_base, anos_capex FROM {_p()}.otim_meta WHERE run_id = $1", run_id
    )
    if not linha or not linha.get("ano_base") or not linha.get("anos_capex"):
        return None
    return int(linha["ano_base"]) + int(linha["anos_capex"]) - 1


async def _cascata(run_id: str, cidade: str | None = None, sub_bacia: str | None = None) -> list[dict[str, Any]]:
    """As parcelas que somam o VPL, no mesmo recorte pedido.

    `tipo` é SEMÂNTICO e não o sinal: `total` é o VPL e é desenhado do zero;
    `entra`/`sai` acumulam. Mandar o VPL como `entra` desenharia uma barra
    flutuando no ar em vez do valor final (§3.4).
    """
    linha = await db.buscar_um(
        f"""SELECT COALESCE(SUM(vp_receita_direta), 0)   AS direta,
                   COALESCE(SUM(vp_receita_indireta), 0) AS indireta,
                   COALESCE(SUM(vp_efeito_base), 0)      AS efeito,
                   COALESCE(SUM(vp_capex_rateado), 0)    AS capex,
                   COALESCE(SUM(vp_opex_rateado), 0)     AS opex,
                   COALESCE(SUM(vpl), 0)                 AS vpl
              FROM {_p()}.otim_subbacia
             WHERE run_id = $1
               AND ($2::text IS NULL OR cidade = $2)
               AND ($3::text IS NULL OR sub_bacia = $3)""",
        run_id,
        cidade,
        sub_bacia,
    )
    if not linha:
        return []
    return [
        {"rotulo": "Receita direta", "valor": linha["direta"], "tipo": "entra"},
        {"rotulo": "Receita indireta", "valor": linha["indireta"], "tipo": "entra"},
        {"rotulo": "Efeito-base paridade", "valor": linha["efeito"], "tipo": "entra"},
        {"rotulo": "CAPEX", "valor": -abs(linha["capex"]), "tipo": "sai"},
        {"rotulo": "OPEX", "valor": -abs(linha["opex"]), "tipo": "sai"},
        {"rotulo": "VPL", "valor": linha["vpl"], "tipo": "total"},
    ]


async def _obras_do_plano_por_ano(
    run_id: str,
    cidade: str | None = None,
    sistema: str | None = None,
    sub_bacia: str | None = None,
) -> list[dict[str, Any]]:
    """As obras EXECUTADAS, agrupadas por ano e componente, no recorte pedido.

    `construida AND data_inicio IS NOT NULL` é o mesmo recorte que o painel global
    sempre usou: obra fora do plano não tem ano de execução, e colocá-la num ano
    qualquer inventaria cronograma.

    Devolve a linha crua de propósito — `obrasPorAno` (do painel) conta OBRAS e
    `elementosPorAno` (de todos os níveis) soma QUANTIDADE FÍSICA. São perguntas
    diferentes sobre a mesma linha, e uma consulta só evita que os dois recortes
    divirjam no dia em que um deles mudar de filtro.
    """
    onde = ["o.run_id = $1", "o.construida", "o.data_inicio IS NOT NULL"]
    args: list[Any] = [run_id]
    if cidade:
        args.append(cidade)
        onde.append(f"o.cidade = ${len(args)}")
    if sistema:
        # Ver a nota da consulta dos elos: `otim_obra.sistema` só vem preenchido em
        # parte das obras, e quem sabe o sistema das demais é a sub-bacia.
        args.append(sistema)
        onde.append(f"COALESCE(o.sistema, s.sistema) = ${len(args)}")
    if sub_bacia:
        args.append(sub_bacia)
        onde.append(f"o.no = ${len(args)}")

    return await db.buscar(
        f"""SELECT LEFT(o.data_inicio, 4)::int AS ano,
                   o.componente,
                   COUNT(*) AS obras,
                   SUM(o.quantidade) AS quantidade,
                   COUNT(DISTINCT o.unidade) AS unidades_distintas,
                   MIN(o.unidade) AS unidade,
                   COALESCE(SUM(o.capex), 0) AS capex
              FROM {_p()}.otim_obra o
              LEFT JOIN {_p()}.otim_subbacia s
                     ON s.run_id = o.run_id AND s.sub_bacia = o.no
             WHERE {' AND '.join(onde)}
             GROUP BY 1, 2
             ORDER BY 1, 2""",
        *args,
    )


def _elementos_por_ano(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """As linhas acima no formato `ElementoDoAno` do contrato.

    A REGRA DA UNIDADE FÍSICA, que é a razão de este formato existir: metro não
    soma com unidade. Quando o mesmo componente aparece no ano com mais de uma
    unidade, `quantidade` e `unidade` saem `null` — e `preco` sai junto, porque
    não há por que dividir. `capex` continua: reais somam entre unidades
    diferentes, e é o que faz a leitura por CAPEX ser a única que nunca esconde
    um ano de obra.

    `null` e não 0, pelo mesmo motivo de sempre: 0 afirmaria "não se construiu
    nada", e o fato é que a conta não existe. A ETE cai aqui naturalmente — o
    módulo não tem quantidade nem unidade, e chega com capex.
    """
    por_ano: dict[int, list[dict[str, Any]]] = {}
    for l in linhas:
        unica = l["unidades_distintas"] == 1
        quantidade = l["quantidade"] if unica else None
        capex = l["capex"]
        por_ano.setdefault(l["ano"], []).append(
            {
                "componente": nome_componente(l["componente"]),
                "quantidade": quantidade,
                "unidade": l["unidade"] if unica else None,
                # Preço unitário MÉDIO do ano — a tela o rotula "R$/{unidade}".
                # Vem da divisão, e não da coluna `preco_unitario`: o que importa
                # é o preço do que se construiu, ponderado pela quantidade.
                "precoUnitario": (capex / quantidade) if quantidade else None,
                "capex": capex,
            }
        )
    return [
        {"ano": ano, "porComponente": comps} for ano, comps in sorted(por_ano.items())
    ]


async def existe(run_id: str) -> bool:
    """A rodada foi publicada?

    `otim_meta` e a mesma fonte que `/runs/{id}/meta` usa para responder 404, e
    por isso e a definicao certa de "existe" para as telas de resultado: rodada
    ainda na fila nao tem linha ali, e nao tem resultado nenhum a mostrar.
    """
    return bool(
        await db.buscar_um(f"SELECT 1 FROM {_p()}.otim_meta WHERE run_id = $1", run_id)
    )


# ---------------------------------------------------------------- nível global
async def painel(run_id: str) -> dict[str, Any]:
    anos = await db.buscar(
        f"""SELECT ano, capex, opex, receita_total AS receita,
                   CASE WHEN dentro_janela_capex THEN teto_capex END AS teto_capex
              FROM {_p()}.otim_ano WHERE run_id = $1 ORDER BY ano""",
        run_id,
    )
    curva = await db.buscar(
        f"""SELECT competencia, capex_mes, capex_acumulado
              FROM {_p()}.otim_mes WHERE run_id = $1 ORDER BY mes_indice""",
        run_id,
    )
    # As tres leituras do elemento saem DESTA consulta, sobre a mesma populacao — obras
    # CONSTRUIDAS. Vir de queries separadas deixaria os graficos discordarem sobre quais
    # obras entraram, e discordancia entre dois quadros da mesma tela e pior que qualquer
    # um dos dois estar errado sozinho.
    #
    # COM UMA EXCECAO, e ela esta declarada porque a garantia acima nao a cobre: a
    # quantidade da ETE vem de OUTRA consulta (`otim_sistema`, mais abaixo), porque a
    # unidade construida dela e a capacidade dos modulos, e isso nao existe em
    # `otim_obra`. As duas fontes descrevem a mesma rodada e hoje batem (um modulo
    # construido = uma obra `ete_mod`), mas nada aqui IMPOE isso — se um dia divergirem,
    # a linha da ETE vai misturar as duas sem avisar.
    #
    #   capex        quanto custou
    #   obras        quantas obras
    #   quantidade   quanto foi CONSTRUIDO, na unidade fisica do elemento
    #                (126.807 ligacoes, 1.042.571 m de rede, 252 unidades de EEE)
    #
    # A UNIDADE so viaja quando e UNICA no elemento. Se um mesmo componente aparecer com
    # `m` e `un` no cadastro, somar as duas produziria um numero sem significado — nesse
    # caso a quantidade sai nula e a tela mostra travessao. ETE e modulo de ETE nao tem
    # quantidade nenhuma: o CAPEX delas vem de pacote, nao de quantidade x preco.
    por_componente = await db.buscar(
        f"""SELECT componente,
                   SUM(capex) AS capex,
                   COUNT(*) AS obras,
                   SUM(quantidade) AS quantidade,
                   MIN(unidade) FILTER (WHERE COALESCE(unidade, '') <> '') AS unidade,
                   COUNT(DISTINCT unidade) FILTER (WHERE COALESCE(unidade, '') <> '')
                       AS unidades_distintas
              FROM {_p()}.otim_obra
             WHERE run_id = $1 AND construida
             GROUP BY componente HAVING SUM(capex) > 0""",
        run_id,
    )
    total_capex = sum(c["capex"] for c in por_componente) or None

    # A ETE NAO TEM QUANTIDADE EM `otim_obra`: o CAPEX dela vem de pacote (ou de modulo),
    # nao de quantidade x preco unitario. Mas ela TEM uma unidade construida com
    # significado — a CAPACIDADE ACRESCENTADA pelos modulos, que e o que a estacao passa a
    # tratar a mais. Ela sai de `otim_sistema`, onde o executor ja publica quantos modulos
    # foram construidos e quanto cada um vale.
    #
    # `ete` (estacao NOVA, obra de pacote unico) fica de fora desta conta: o executor nao
    # publica a capacidade dela por sistema, e inventar aqui seria pior que o travessao.
    # A nota do grafico diz isso.
    # A UNIDADE VEM DO BANCO, e do SNAPSHOT da rodada — nunca de constante no codigo.
    # Trocar a medida de capacidade e mudanca de cadastro; a soma nao muda com ela, so a
    # leitura do numero. E ela sai de `otim_sistema` (o snapshot), e nao de `input`,
    # porque o cadastro muda e a rodada e imutavel: uma rodada de 2026 tem de continuar
    # dizendo a unidade que ELA usou.
    #
    # Mesma regra das obras: unidade so viaja quando e UNICA entre os sistemas que
    # construiram modulo. Duas unidades diferentes somadas dariam um numero sem
    # significado — nesse caso a quantidade sai sem sufixo.
    cap_ete = await db.buscar_um(
        f"""SELECT SUM(modulos_construidos * capacidade_modulo)::float8 AS capacidade,
                   MIN(unidade_capacidade) FILTER (
                       WHERE COALESCE(unidade_capacidade, '') <> '') AS unidade,
                   COUNT(DISTINCT unidade_capacidade) FILTER (
                       WHERE COALESCE(unidade_capacidade, '') <> '') AS unidades_distintas
              FROM {_p()}.otim_sistema
             WHERE run_id = $1 AND COALESCE(modulos_construidos, 0) > 0""",
        run_id,
    )
    capacidade_modulos = (cap_ete or {}).get("capacidade")
    unidade_capacidade = (
        (cap_ete or {}).get("unidade") if (cap_ete or {}).get("unidades_distintas") == 1 else None
    )

    obras_ano = await _obras_do_plano_por_ano(run_id)
    por_ano: dict[int, list[dict[str, Any]]] = {}
    for o in obras_ano:
        # Aqui `quantidade` e a CONTAGEM DE OBRAS, e nao a quantidade fisica:
        # `obrasPorAno` sempre significou isso, e o front do :8080 o le assim
        # (`GraficoObrasPorAno`). A quantidade fisica vai em `elementosPorAno`,
        # logo abaixo, montada da MESMA consulta para os dois nao divergirem.
        por_ano.setdefault(o["ano"], []).append(
            {"componente": nome_componente(o["componente"]), "quantidade": o["obras"]}
        )

    return {
        "anos": [
            {
                "ano": a["ano"],
                "capex": a["capex"],
                "opex": a["opex"],
                "receita": a["receita"],
                "tetoCapex": a["teto_capex"],
            }
            for a in anos
        ],
        "curvaS": [
            {
                "mes": c["competencia"],
                "capexMes": c["capex_mes"],
                "capexAcumulado": c["capex_acumulado"],
            }
            for c in curva
        ],
        "cascata": await _cascata(run_id),
        "capexPorComponente": sorted(
            (
                {
                    "componente": nome_componente(c["componente"]),
                    "capex": c["capex"],
                    "pctDoTotal": _pct(c["capex"], total_capex),
                    "obras": c["obras"],
                    # Ausentes quando nao ha o que contar (ETE) ou quando o elemento
                    # aparece com mais de uma unidade. `None` e a tela mostra travessao —
                    # zero seria lido como "nada construido".
                    "unidadesConstruidas": (
                        capacidade_modulos
                        if c["componente"] == "ete_mod"
                        else (c["quantidade"] if c["unidades_distintas"] == 1 else None)
                    ),
                    "unidade": (
                        unidade_capacidade
                        if c["componente"] == "ete_mod"
                        else (c["unidade"] if c["unidades_distintas"] == 1 else None)
                    ),
                }
                for c in por_componente
            ),
            key=lambda c: ORDEM_COMPONENTES.index(c["componente"])
            if c["componente"] in ORDEM_COMPONENTES
            else len(ORDEM_COMPONENTES),
        ),
        "obrasPorAno": [
            {"ano": ano, "porComponente": comps} for ano, comps in sorted(por_ano.items())
        ],
        "elementosPorAno": _elementos_por_ano(obras_ano),
        "fimCapex": await _fim_capex(run_id),
    }


async def ebitda(run_id: str, cidade: str | None = None) -> dict[str, Any]:
    """EBITDA = receita operacional − OPEX, nominal, ano a ano.

    É **saída calculada** e não entra na função objetivo — a tela diz isso ao
    usuário, e o número precisa ser coerente com essa definição.

    Sem cidade sai de `otim_ano` (já consolidado pelo job); com cidade, agrega
    `otim_subbacia_ano`, porque não existe tabela de EBITDA por cidade.
    """
    if cidade is None:
        linhas = await db.buscar(
            f"""SELECT ano, ebitda, ebitda_margem_pct AS margem
                  FROM {_p()}.otim_ano WHERE run_id = $1 ORDER BY ano""",
            run_id,
        )
    else:
        linhas = await db.buscar(
            f"""SELECT ano, SUM(ebitda) AS ebitda,
                       CASE WHEN SUM(receita_direta + receita_indireta + efeito_base) > 0
                            THEN ROUND((SUM(ebitda) / NULLIF(SUM(receita_direta
                                 + receita_indireta + efeito_base), 0) * 100)::numeric, 1)
                       END AS margem
                  FROM {_p()}.otim_subbacia_ano
                 WHERE run_id = $1 AND cidade = $2
                 GROUP BY ano ORDER BY ano""",
            run_id,
            cidade,
        )

    positivo = next((l["ano"] for l in linhas if (l["ebitda"] or 0) > 0), None)
    return {
        "anos": [
            {
                "ano": l["ano"],
                "ebitda": l["ebitda"],
                # `margemPct` é null no ano sem receita — e não 0, que afirmaria
                # margem nula onde a conta não existe.
                "margemPct": float(l["margem"]) if l["margem"] is not None else None,
            }
            for l in linhas
        ],
        "total": sum(l["ebitda"] or 0 for l in linhas),
        "anoViraPositivo": positivo,
        "fimCapex": await _fim_capex(run_id),
    }


async def cidades(run_id: str) -> list[dict[str, Any]]:
    linhas = await db.buscar(
        f"""SELECT c.cidade, c.vpl, c.capex_total, c.cobertura_final_pct,
                   c.metas_atingidas, c.metas_total,
                   (SELECT COUNT(*) FROM {_p()}.otim_sistema s
                     WHERE s.run_id = c.run_id AND s.cidade = c.cidade) AS sistemas
              FROM {_p()}.otim_cidade c
             WHERE c.run_id = $1 ORDER BY c.vpl DESC""",
        run_id,
    )

    # A SERIE DE COBERTURA E AS METAS VEM JUNTO, em duas consultas para a lista
    # inteira. O cartao-grafico do nivel 1 desenha o par cobertura x meta de cada
    # cidade; buscar por cidade seria N+1 — 141 idas ao banco numa unidade grande,
    # e o front teria de abrir 141 requisicoes para montar uma grade de cartoes.
    cobertura = await db.buscar(
        f"""SELECT cidade, ano, cobertura_pct FROM {_p()}.otim_cobertura
             WHERE run_id = $1 ORDER BY cidade, ano""",
        run_id,
    )
    metas = await db.buscar(
        f"""SELECT cidade, ano, pct_alvo, cobertura_ligacoes, alvo_ligacoes,
                   atingida, dentro_janela_capex
              FROM {_p()}.otim_meta_cobertura
             WHERE run_id = $1 ORDER BY cidade, ano""",
        run_id,
    )
    por_cidade_cobertura: dict[str, list[dict[str, Any]]] = {}
    for c in cobertura:
        por_cidade_cobertura.setdefault(c["cidade"], []).append(
            {"ano": c["ano"], "coberturaPct": c["cobertura_pct"]}
        )
    por_cidade_metas: dict[str, list[dict[str, Any]]] = {}
    for m in metas:
        por_cidade_metas.setdefault(m["cidade"], []).append(_meta(m))

    return [
        {
            "id": l["cidade"],
            "nome": l["cidade"],
            "vpl": l["vpl"],
            "capex": l["capex_total"],
            "coberturaFimPct": l["cobertura_final_pct"],
            "metasAtingidas": l["metas_atingidas"],
            "metasTotal": l["metas_total"],
            "sistemas": l["sistemas"],
            # Lista vazia, e nao ausencia: a cidade sem serie publicada existe, e o
            # front distingue "nao tem curva" de "campo que nao veio".
            "cobertura": por_cidade_cobertura.get(l["cidade"], []),
            "metas": por_cidade_metas.get(l["cidade"], []),
        }
        for l in linhas
    ]


# ------------------------------------------- nível global: por que não fatura
#
# A pergunta que estes dois endpoints respondem — "por que o plano não conecta
# 100%?" — já tinha resposta no nível 4, uma sub-bacia por vez (`subbacia`, campo
# `explicacao`). O que faltava era chegar nela SEM já saber qual sub-bacia abrir.


async def explicabilidade(run_id: str, cidade: str | None = None) -> dict[str, Any] | None:
    """Por que as sub-bacias que não faturam não faturam, agregado por motivo.

    Com `cidade`, o mesmo recorte dentro de uma cidade — é o bloco "sub-bacias
    fora do plano" do nível 2. Devolve `None` quando a cidade não existe naquela
    rodada, para o endpoint responder 404 em vez de zeros que parecem dado.

    ## A categoria de uma sub-bacia é a da sua OBRA DE COLETA

    Um nó tem várias obras (ligação, rede, tronco, EEE...) e elas discordam de
    categoria com frequência — em `run_20260812_000112_0ba066`, 2065 dos 2269 nós
    que não faturam têm obras de categorias diferentes. "A primeira que aparecer"
    daria uma resposta que muda entre duas execuções da mesma consulta.

    `otim_subbacia.obra_coleta` nomeia A obra que coleta daquele nó, e é ela que
    decide se a sub-bacia entra ou não no plano. A regra é determinística e
    completa: no run acima ela classifica exatamente as 2269, sem sobra.

    ## Por que a lista de sub-bacias vem inteira

    Cada categoria carrega TODAS as suas sub-bacias, não uma amostra. O total de
    itens é exatamente `naoFaturando` — no maior run publicado, 2269 objetos
    pequenos. Uma amostra silenciosa seria pior que o peso: a tela mostra a
    contagem verdadeira no cabeçalho e a lista logo abaixo, e quem abrisse uma
    lista de 20 sob um título de "1142 sub-bacias" leria uma como a outra.
    """
    filtro_cidade = " AND s.cidade = $2" if cidade else ""
    args: tuple = (run_id, cidade) if cidade else (run_id,)

    if cidade:
        existe = await db.buscar_um(
            f"SELECT 1 FROM {_p()}.otim_cidade WHERE run_id = $1 AND cidade = $2",
            run_id,
            cidade,
        )
        if not existe:
            return None

    totais = await db.buscar_um(
        f"""SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE NOT s.faturando) AS nao_fatura
              FROM {_p()}.otim_subbacia s
             WHERE s.run_id = $1{filtro_cidade}""",
        *args,
    )

    presas = await db.buscar(
        f"""SELECT s.sub_bacia, s.cidade, s.sistema,
                   COALESCE(s.vazao_marginal, 0) AS vazao,
                   oc.categoria_motivo
              FROM {_p()}.otim_subbacia s
              LEFT JOIN {_p()}.otim_obra oc
                     ON oc.run_id = s.run_id AND oc.obra_id = s.obra_coleta
             WHERE s.run_id = $1 AND NOT s.faturando{filtro_cidade}
             ORDER BY COALESCE(s.vazao_marginal, 0) DESC, s.sub_bacia""",
        *args,
    )

    categorias: dict[str, dict[str, Any]] = {}
    for linha in presas:
        # Sem categoria a sub-bacia continua contando: some do agrupamento e o
        # cabecalho ("185 de 1047 nao faturam") deixaria de fechar com as parcelas.
        chave = linha["categoria_motivo"] or "Sem motivo registrado"
        c = categorias.setdefault(
            chave, {"categoria": chave, "subbacias": 0, "vazaoPresa": 0.0, "itens": []}
        )
        c["subbacias"] += 1
        c["vazaoPresa"] += linha["vazao"]
        c["itens"].append(
            {
                "subBaciaId": linha["sub_bacia"],
                "cidadeId": linha["cidade"],
                "sistemaId": linha["sistema"],
                "vazaoPresa": linha["vazao"],
            }
        )

    # O ELO: a obra que, nao construida, tira OUTRAS sub-bacias do plano.
    #
    # `DISTINCT` no par (elo, no) antes de somar: um no com tres obras travadas
    # pelo mesmo elo apareceria tres vezes, e a vazao dele entraria tres vezes na
    # soma. E a ordenacao e pela VAZAO liberada, nao pela contagem — decidir onde
    # investir depende de QUEM esta preso, nao de quantos.
    elos = await db.buscar(
        f"""WITH presos AS (
                SELECT DISTINCT o.elo_que_trava AS elo, o.no AS sub_bacia
                  FROM {_p()}.otim_obra o
                 WHERE o.run_id = $1
                   AND o.elo_que_trava IS NOT NULL
                   AND o.no IS NOT NULL
            )
            SELECT p.elo AS obra_id, e.componente, e.cidade, e.no,
                   -- `otim_obra.sistema` vem VAZIO nas obras de transporte (6695
                   -- das 8079 do maior run): o motor so o preenche onde a obra
                   -- pertence a um sistema por si. Quem sabe o sistema e a
                   -- sub-bacia em que a obra esta, e o contrato promete um
                   -- `sistemaId` de verdade — a tela monta `/sistemas/{id}` com
                   -- ele, e um `null` viraria link para lugar nenhum.
                   COALESCE(e.sistema, se.sistema) AS sistema,
                   COUNT(*) AS bloqueia,
                   COALESCE(SUM(s.vazao_marginal), 0) AS vazao_liberada
              FROM presos p
              JOIN {_p()}.otim_subbacia s
                ON s.run_id = $1 AND s.sub_bacia = p.sub_bacia
              LEFT JOIN {_p()}.otim_obra e
                     ON e.run_id = $1 AND e.obra_id = p.elo
              LEFT JOIN {_p()}.otim_subbacia se
                     ON se.run_id = $1 AND se.sub_bacia = e.no
             WHERE TRUE{filtro_cidade}
             GROUP BY p.elo, e.componente, e.cidade, e.sistema, se.sistema, e.no
             ORDER BY vazao_liberada DESC, bloqueia DESC, p.elo""",
        *args,
    )

    return {
        "naoFaturando": (totais or {}).get("nao_fatura") or 0,
        "totalSubbacias": (totais or {}).get("total") or 0,
        "categorias": sorted(
            categorias.values(), key=lambda c: c["vazaoPresa"], reverse=True
        ),
        "elos": [
            {
                "obraId": e["obra_id"],
                "componente": nome_componente(e["componente"]),
                "cidadeId": e["cidade"],
                "sistemaId": e["sistema"],
                "subBaciaId": e["no"],
                "bloqueia": e["bloqueia"],
                "vazaoLiberada": e["vazao_liberada"],
            }
            for e in elos
        ],
    }


# ------------------------------------------- nível global: o plano de execução
#
# A mesma `otim_obra` que o nível 5 lê uma linha por vez, agora vista do topo: a
# lista do plano e o cronograma por ano.

#: A linha de ETE com `status = 'N/A'` NÃO É OBRA. Ela existe para a ETE ter ficha
#: (capex 0, sem quantidade, sem data), e o que de fato se constrói é o módulo
#: (`tipo = 'ete_mod'`). Deixá-la na lista poria 474 linhas de capex zero no meio
#: do plano de execução.
_SO_OBRA = "o.status <> 'N/A'"

#: `_situacao()` (Python) e este CASE dizem a MESMA coisa, na mesma ordem — obra de
#: terceiro que já existe é `terceiro`, e não `construida`, porque quem paga muda a
#: leitura do plano. A duplicação é consciente: a lista é paginada, e filtrar por
#: situação depois de trazer a página daria um `total` que não bate com o filtro.
_SITUACAO_SQL = (
    "CASE WHEN LOWER(COALESCE(o.responsavel, '')) LIKE 'terceiro%' THEN 'terceiro'"
    "     WHEN o.construida THEN 'construida'"
    "     ELSE 'nao-construida' END"
)

#: Whitelist — o `ordenar` da querystring escolhe uma ENTRADA, nunca compõe SQL.
_ORDENS = {
    "inicio": "o.data_inicio NULLS LAST, o.obra_id",
    "capex": "o.capex DESC NULLS LAST, o.obra_id",
    "cidade": "o.cidade, o.data_inicio NULLS LAST, o.obra_id",
}

#: Teto de página. O front pede 200 ao abrir um ano do cronograma. Pela rota o
#: valor já chega validado (`Query(le=500)`, que recusa o excesso com 422 em vez
#: de fingir que atendeu); este `min` é o teto para quem chamar a função direto —
#: um script de conferência, um teste —, onde nada mais impede pedir as 8 mil
#: obras da rodada de uma vez.
_TAMANHO_MAX = 500


async def obras(
    run_id: str,
    situacao: str | None = None,
    cidade: str | None = None,
    ano: int | None = None,
    pagina: int = 1,
    tamanho: int = 50,
    ordenar: str = "inicio",
) -> dict[str, Any]:
    """A lista de obras do plano, paginada.

    Paginada de propósito: uma unidade grande publica milhares de linhas em
    `otim_obra` — 8079 no maior run de hoje. `total` é o tamanho do resultado
    FILTRADO, e não o da rodada: é o número de que a tela precisa para paginar.
    """
    onde = ["o.run_id = $1", _SO_OBRA]
    args: list[Any] = [run_id]

    if situacao:
        args.append(situacao)
        onde.append(f"{_SITUACAO_SQL} = ${len(args)}")
    if cidade:
        args.append(cidade)
        onde.append(f"o.cidade = ${len(args)}")
    if ano:
        # `data_inicio` e texto 'AAAA-MM': comparar o prefixo dispensa converter a
        # coluna, e o ano sem obra nenhuma devolve pagina vazia, nao erro.
        args.append(str(ano))
        onde.append(f"LEFT(o.data_inicio, 4) = ${len(args)}")

    tamanho = max(1, min(tamanho, _TAMANHO_MAX))
    pagina = max(1, pagina)

    # O TOTAL VEM DE UMA CONSULTA PROPRIA, e nao de um `COUNT(*) OVER ()` na
    # pagina. A janela so devolve valor quando ha linha: com `pagina=9999` a
    # resposta saia `{"total": 0, "itens": []}` numa rodada com 7605 obras, e o
    # front nao tem como distinguir "acabou" de "nao ha nada". Uma rodada
    # publicada e imutavel, entao as duas consultas nao podem discordar.
    filtros = list(args)
    total = await db.buscar_um(
        f"""SELECT COUNT(*) AS total
              FROM {_p()}.otim_obra o
              LEFT JOIN {_p()}.otim_subbacia s
                     ON s.run_id = o.run_id AND s.sub_bacia = o.no
             WHERE {' AND '.join(onde)}""",
        *filtros,
    )

    args.extend([tamanho, (pagina - 1) * tamanho])
    linhas = await db.buscar(
        f"""SELECT o.obra_id, o.componente, o.responsavel, o.construida,
                   o.cidade, o.no, o.capex, o.quantidade, o.unidade,
                   o.data_inicio, o.prazo_meses,
                   -- Ver a nota da consulta dos elos: `otim_obra.sistema` so vem
                   -- preenchido em parte das obras, e quem sabe o sistema das
                   -- demais e a sub-bacia em que elas estao.
                   COALESCE(o.sistema, s.sistema) AS sistema
              FROM {_p()}.otim_obra o
              LEFT JOIN {_p()}.otim_subbacia s
                     ON s.run_id = o.run_id AND s.sub_bacia = o.no
             WHERE {' AND '.join(onde)}
             ORDER BY {_ORDENS.get(ordenar, _ORDENS['inicio'])}
             LIMIT ${len(args) - 1} OFFSET ${len(args)}""",
        *args,
    )

    return {
        "total": (total or {}).get("total") or 0,
        "itens": [
            {
                "obraId": l["obra_id"],
                "componente": nome_componente(l["componente"]),
                "situacao": _situacao(l),
                "cidadeId": l["cidade"],
                "sistemaId": l["sistema"],
                # `null` para ETE e modulo de ETE: eles nao tem sub-bacia propria.
                "subBaciaId": l["no"],
                "capex": l["capex"],
                "quantidade": l["quantidade"],
                "unidade": l["unidade"],
                "anoInicio": int(str(l["data_inicio"])[:4]) if l["data_inicio"] else None,
                "prazoMeses": l["prazo_meses"],
            }
            for l in linhas
        ],
    }


async def cronograma_de_obras(run_id: str) -> dict[str, Any]:
    """Quantas obras de cada componente começam em cada ano, e quanto custam.

    Só o que ENTRA no plano: `data_inicio` só existe para obra que a rodada
    agendou. Obra fora do plano não tem ano de execução, e empurrá-la para um ano
    qualquer inventaria cronograma.
    """
    linhas = await db.buscar(
        f"""SELECT LEFT(o.data_inicio, 4)::int AS ano,
                   o.componente,
                   COUNT(*) AS obras,
                   COALESCE(SUM(o.capex), 0) AS capex,
                   COUNT(*) FILTER (
                       WHERE LOWER(COALESCE(o.responsavel, '')) LIKE 'terceiro%'
                   ) AS terceiro
              FROM {_p()}.otim_obra o
             WHERE o.run_id = $1 AND o.data_inicio IS NOT NULL AND {_SO_OBRA}
             GROUP BY 1, 2
             ORDER BY 1, 2""",
        run_id,
    )

    anos: dict[int, dict[str, Any]] = {}
    for l in linhas:
        a = anos.setdefault(
            l["ano"],
            {"ano": l["ano"], "obras": 0, "capex": 0.0, "obrasTerceiro": 0, "porComponente": []},
        )
        a["obras"] += l["obras"]
        a["capex"] += l["capex"]
        a["obrasTerceiro"] += l["terceiro"]
        a["porComponente"].append(
            {
                "componente": nome_componente(l["componente"]),
                "obras": l["obras"],
                "capex": l["capex"],
            }
        )

    return {"anos": [anos[a] for a in sorted(anos)]}


# ---------------------------------------------------------------- nível cidade
async def cidade(run_id: str, cidade_id: str) -> dict[str, Any] | None:
    base = await db.buscar_um(
        f"""SELECT * FROM {_p()}.otim_cidade WHERE run_id = $1 AND cidade = $2""",
        run_id,
        cidade_id,
    )
    if not base:
        return None

    cobertura = await db.buscar(
        f"""SELECT ano, cobertura_pct FROM {_p()}.otim_cobertura
             WHERE run_id = $1 AND cidade = $2 ORDER BY ano""",
        run_id,
        cidade_id,
    )
    metas = await db.buscar(
        f"""SELECT ano, pct_alvo, cobertura_ligacoes, alvo_ligacoes, atingida,
                   dentro_janela_capex
              FROM {_p()}.otim_meta_cobertura
             WHERE run_id = $1 AND cidade = $2 ORDER BY ano""",
        run_id,
        cidade_id,
    )
    sistemas = await db.buscar(
        f"""SELECT sistema, sub_bacias, sub_bacias_faturando,
                   capex_modulos_construidos, ocupacao_pct
              FROM {_p()}.otim_sistema
             WHERE run_id = $1 AND cidade = $2 ORDER BY sistema""",
        run_id,
        cidade_id,
    )
    horizonte = await db.buscar_um(
        f"""SELECT MAX(ano_fim_concessao) AS fim FROM {_p()}.otim_sistema
             WHERE run_id = $1 AND cidade = $2""",
        run_id,
        cidade_id,
    )
    ef = await db.buscar_um(
        f"""SELECT COALESCE(SUM(vp_efeito_base), 0) AS efeito
              FROM {_p()}.otim_subbacia WHERE run_id = $1 AND cidade = $2""",
        run_id,
        cidade_id,
    )
    efeito = (ef or {}).get("efeito") or 0

    return {
        "id": cidade_id,
        "nome": cidade_id,
        "fimConcessao": (horizonte or {}).get("fim"),
        "fimCapex": await _fim_capex(run_id),
        "capexTotal": base["capex_total"],
        "vpl": base["vpl"],
        "ligacoesNovas": base["ligacoes_novas"],
        "coberturaBasePct": base["cobertura_base_pct"],
        "coberturaFinalPct": base["cobertura_final_pct"],
        "cobertura": [
            {"ano": c["ano"], "coberturaPct": c["cobertura_pct"]} for c in cobertura
        ],
        "metas": [_meta(m) for m in metas],
        "cascata": await _cascata(run_id, cidade=cidade_id),
        "elementosPorAno": _elementos_por_ano(
            await _obras_do_plano_por_ano(run_id, cidade=cidade_id)
        ),
        "paridade": {
            # PENDENTE — as FAIXAS de paridade (cobertura → fator) vivem em
            # `input.fator_esgoto`, e não nas tabelas de resultado: o job publica a
            # paridade REALIZADA por ano (`otim_paridade`), não a tabela de faixas
            # que a produziu. A tela precisa das faixas para explicar a causalidade
            # do degrau. Ou o job passa a publicá-las, ou este endpoint lê o
            # cadastro — e aí o número deixa de ser o da rodada, o que é pior.
            "faixas": [],
            "paridadeInicial": base["paridade_inicial"],
            "paridadeFinal": base["paridade_final"],
            "houveDegrau": (base["paridade_final"] or 0) > (base["paridade_inicial"] or 0),
            "vpEfeitoBase": efeito,
            "pctDoVplDaCidade": _pct(efeito, base["vpl"]),
        },
        "sistemas": [
            {
                "id": s["sistema"],
                "nome": s["sistema"],
                "subbacias": s["sub_bacias"],
                "faturando": s["sub_bacias_faturando"],
                "capex": s["capex_modulos_construidos"],
                "ocupacaoPct": s["ocupacao_pct"],
            }
            for s in sistemas
        ],
    }


# --------------------------------------------------------------- nível sistema
async def topologia(run_id: str, sistema_id: str) -> dict[str, Any] | None:
    sistema = await db.buscar_um(
        f"SELECT * FROM {_p()}.otim_sistema WHERE run_id = $1 AND sistema = $2",
        run_id,
        sistema_id,
    )
    if not sistema:
        return None

    nos = await db.buscar(
        f"""SELECT sub_bacia, is_cts, vazao_marginal, faturando, jusante
              FROM {_p()}.otim_subbacia
             WHERE run_id = $1 AND sistema = $2 ORDER BY sub_bacia""",
        run_id,
        sistema_id,
    )
    # As obras saem pelos NÓS do sistema, e não por `otim_obra.sistema`: numa
    # rodada real essa coluna vem NULL em 395 de 480 obras, e o filtro por ela
    # devolvia ZERO componentes — a topologia desenhava caixas vazias enquanto a
    # ficha da sub-bacia listava as quatro obras dela. Com dado semeado à mão eu
    # preenchia `sistema`, então o defeito só apareceu na primeira simulação de
    # verdade. `no` é confiável: é a chave que liga a obra ao seu nó.
    # As obras da ETE nao tem `no`: elas se identificam pelo proprio `obra_id`,
    # que e o id da ETE (ou dele derivado, no caso dos modulos). Entao o filtro
    # olha os dois lados — `no` para os nos da rede, `obra_id` para a ETE.
    ete_id = sistema["ete_id"]
    obras = await db.buscar(
        f"""SELECT obra_id, no, componente, capex, preco_unitario, quantidade,
                   unidade, data_inicio, prazo_meses, construida, responsavel
              FROM {_p()}.otim_obra
             WHERE run_id = $1
               AND (no = ANY($2::text[])
                    OR ($3::text IS NOT NULL
                        AND (obra_id = $3 OR obra_id LIKE $3 || '_' || '%')))""",
        run_id,
        [n["sub_bacia"] for n in nos],
        ete_id,
    )
    por_no: dict[str, list[dict[str, Any]]] = {}
    for o in obras:
        # obra sem `no` e obra da ETE — agrupada sob o id dela.
        por_no.setdefault(o["no"] or ete_id, []).append(o)

    ids = {n["sub_bacia"] for n in nos}
    # A CTS é pareada 1:1 com a sub-bacia; o pareamento vive no cadastro, mas o
    # resultado guarda `jusante`, e para a CTS ele é a própria sub-bacia irmã.
    pareada = {n["sub_bacia"]: n["jusante"] for n in nos if n["is_cts"]}

    return {
        "sistemaId": sistema_id,
        "sistemaNome": sistema_id,
        "cidadeId": sistema["cidade"],
        "cidadeNome": sistema["cidade"],
        "subbacias": sistema["sub_bacias"],
        "faturando": sistema["sub_bacias_faturando"],
        "capexConstruido": sistema["capex_modulos_construidos"],
        "elementosPorAno": _elementos_por_ano(
            await _obras_do_plano_por_ano(run_id, sistema=sistema_id)
        ),
        "nos": [
            {
                "id": n["sub_bacia"],
                "tipo": "cts" if n["is_cts"] else "subbacia",
                "vazao": n["vazao_marginal"],
                "fatura": n["faturando"],
                "pareadaCom": pareada.get(n["sub_bacia"]),
                # `jusante` fora de `nos` é tratado pelo front como "liga direto na
                # ETE" — mandamos como veio, sem inventar um id que não existe.
                "jusante": n["jusante"] if n["jusante"] in ids else None,
                "componentes": [_componente(o) for o in por_no.get(n["sub_bacia"], [])],
            }
            for n in nos
        ],
        "ete": {
            "id": sistema["ete_id"],
            "nome": f"ETE · {sistema_id}",
            "capacidade": sistema["capacidade_instalada"],
            "vazaoConectada": sistema["vazao_conectada"],
            "ocupacaoPct": sistema["ocupacao_pct"],
            "vazaoNaoAtendida": sistema["vazao_nao_atendida"],
            "modulos": [
                _componente(o) for o in por_no.get(sistema["ete_id"], [])
            ],
        },
    }


def _componente(o: dict[str, Any]) -> dict[str, Any]:
    ano = None
    if o.get("data_inicio"):
        ano = int(str(o["data_inicio"])[:4])
    return {
        "nome": nome_componente(o["componente"]),
        "obraId": o["obra_id"],
        "situacao": _situacao(o),
        "capex": o["capex"],
        "precoUnitario": o["preco_unitario"],
        "quantidade": o["quantidade"],
        "unidade": o["unidade"],
        "anoInicio": ano,
        "prazoMeses": o["prazo_meses"],
    }


# -------------------------------------------------------------- nível sub-bacia
async def subbacia(run_id: str, sub_id: str) -> dict[str, Any] | None:
    base = await db.buscar_um(
        f"SELECT * FROM {_p()}.otim_subbacia WHERE run_id = $1 AND sub_bacia = $2",
        run_id,
        sub_id,
    )
    if not base:
        return None

    # `receita: []` é o sinal de "não fatura": a tela troca o gráfico por uma
    # mensagem. Um eixo com zeros pareceria dado.
    receita = (
        await db.buscar(
            f"""SELECT ano, receita_direta, receita_indireta
                  FROM {_p()}.otim_subbacia_ano
                 WHERE run_id = $1 AND sub_bacia = $2 AND faturando
                 ORDER BY ano""",
            run_id,
            sub_id,
        )
        if base["faturando"]
        else []
    )

    elementos = await db.buscar(
        f"""SELECT obra_id, componente, quantidade, unidade, preco_unitario, capex,
                   data_inicio, prazo_meses, construida, responsavel, elo_que_trava,
                   categoria_motivo, motivo
              FROM {_p()}.otim_obra
             WHERE run_id = $1 AND no = $2""",
        run_id,
        sub_id,
    )

    # O caminho até a ETE é o encadeamento de `jusante`. Iterativo com conjunto de
    # visitados: um ciclo no cadastro (b → c → b) travaria uma recursão, e cadastro
    # com ciclo é erro plausível.
    caminho: list[str] = []
    vistos = {sub_id}
    atual = base["jusante"]
    while atual and atual not in vistos:
        caminho.append(atual)
        vistos.add(atual)
        prox = await db.buscar_um(
            f"SELECT jusante FROM {_p()}.otim_subbacia WHERE run_id = $1 AND sub_bacia = $2",
            run_id,
            atual,
        )
        atual = prox["jusante"] if prox else None

    # O elo tem de ser obra DESTA sub-bacia: a tela o oferece como link, e um elo
    # apontando para obra de outro nó levaria a uma ficha plausível e errada.
    ids_daqui = {e["obra_id"] for e in elementos}
    elo = next((e["elo_que_trava"] for e in elementos if e.get("elo_que_trava")), None)

    return {
        "id": sub_id,
        "tipo": "cts" if base["is_cts"] else "subbacia",
        "pareadaCom": base["jusante"] if base["is_cts"] else None,
        "cidadeId": base["cidade"],
        "cidadeNome": base["cidade"],
        "sistemaId": base["sistema"],
        "sistemaNome": base["sistema"],
        "fatura": base["faturando"],
        "vazao": base["vazao_marginal"],
        "vpl": base["vpl"],
        "cascata": await _cascata(run_id, sub_bacia=sub_id),
        "elementosPorAno": _elementos_por_ano(
            await _obras_do_plano_por_ano(run_id, sub_bacia=sub_id)
        ),
        "receita": [
            {"ano": r["ano"], "direta": r["receita_direta"], "indireta": r["receita_indireta"]}
            for r in receita
        ],
        "explicacao": {
            # A MESMA REGRA DA EXPLICABILIDADE AGREGADA: a categoria da sub-bacia e
            # a da sua OBRA DE COLETA (ver `explicabilidade`, acima). Era "a
            # primeira obra do no que tivesse categoria", numa consulta sem
            # `ORDER BY` — e as obras de um no discordam de categoria em 2148 dos
            # 2269 nos que nao faturam. O nivel 1 dizia "Nao se paga" para
            # `c1b3_1_3` enquanto esta tela dizia "Compartilhada nao acionada",
            # sobre a mesma sub-bacia, na mesma rodada.
            #
            # O fallback ordenado so existe para a sub-bacia sem obra de coleta
            # com categoria: nao acontece nos dados de hoje, e se acontecer e
            # melhor uma resposta estavel que uma sorteada.
            "categoria": next(
                (
                    e["categoria_motivo"]
                    for e in elementos
                    if e["obra_id"] == base.get("obra_coleta") and e.get("categoria_motivo")
                ),
                next(
                    (
                        e["categoria_motivo"]
                        for e in sorted(elementos, key=lambda e: e["obra_id"])
                        if e.get("categoria_motivo")
                    ),
                    None,
                ),
            ),
            "elo": elo if elo in ids_daqui else None,
            "narrativa": base.get("motivo_sem_receita"),
            "seFosseLigada": None
            if base["faturando"]
            else {
                "receita": base["pot_vp_receita"],
                "capexSozinha": base["pot_vp_capex_solo"],
                "opex": base["pot_vp_opex"],
                "saldoSozinha": base["pot_saldo_solo"],
                "saldoComRateio": base["pot_saldo_rateado"],
            },
        },
        "caminho": caminho,
        "elementos": [
            {
                "obraId": e["obra_id"],
                "componente": nome_componente(e["componente"]),
                "situacao": _situacao(e),
                "quantidade": e["quantidade"],
                "unidade": e["unidade"],
                "precoUnitario": e["preco_unitario"],
                "capex": e["capex"],
                "anoInicio": int(str(e["data_inicio"])[:4]) if e.get("data_inicio") else None,
                "prazoMeses": e["prazo_meses"],
            }
            for e in elementos
        ],
    }


# --------------------------------------------------------------- nível elemento
async def obra(run_id: str, obra_id: str) -> dict[str, Any] | None:
    o = await db.buscar_um(
        f"SELECT * FROM {_p()}.otim_obra WHERE run_id = $1 AND obra_id = $2",
        run_id,
        obra_id,
    )
    if not o:
        return None

    deps = await db.buscar(
        f"""SELECT sub_bacia, vazao_sub_bacia, fracao_rateio, capex_rateado,
                   sub_bacia_faturando
              FROM {_p()}.otim_dependencia
             WHERE run_id = $1 AND obra_id = $2 ORDER BY sub_bacia""",
        run_id,
        obra_id,
    )

    # `capexConstruido` / `capexQueFalta` são da CADEIA da sub-bacia, não da obra:
    # respondem "quão longe ela está de faturar".
    cadeia = await db.buscar_um(
        f"""SELECT COALESCE(SUM(capex) FILTER (WHERE construida), 0)     AS feito,
                   COALESCE(SUM(capex) FILTER (WHERE NOT construida), 0) AS falta
              FROM {_p()}.otim_obra WHERE run_id = $1 AND no = $2""",
        run_id,
        o["no"],
    )

    return {
        "obraId": obra_id,
        "componente": nome_componente(o["componente"]),
        "rotulo": f"{obra_id} (CTS)" if o["is_cts"] else obra_id,
        "situacao": _situacao(o),
        "cidadeId": o["cidade"],
        "cidadeNome": o["cidade"],
        "sistemaId": o["sistema"],
        "sistemaNome": o["sistema"],
        "subbaciaId": o["no"],
        "responsavel": o["responsavel"],
        "obrigatoria": o["obrigatoria"],
        "quantidade": o["quantidade"],
        "unidade": o["unidade"],
        "precoUnitario": o["preco_unitario"],
        "capex": o["capex"],
        "opexAno": o["opex_ano"],
        "prazoMeses": o["prazo_meses"],
        "mesMaisCedo": o["inicio_min_mes"],
        # WACC vai em PONTOS PERCENTUAIS (9.45), não em fração — o contrato diz que
        # campos `Pct` vão de 0 a 100, e o motor guarda fração.
        "wacc": round(o["wacc"] * 100, 2) if o.get("wacc") is not None else None,
        # `proprio` = financiamento contratado para a obra; `medio` = o campo veio
        # vazio e herdou o wacc_medio da unidade. São coisas economicamente
        # diferentes, e a tela mostra qual é.
        "waccOrigem": o["wacc_origem"],
        "ligacoesNovas": o["ligacoes"],
        "ticketMedio": o["ticket_mes"],
        "precoPorLigacao": o["preco_ligacao"],
        "capexConstruido": (cadeia or {}).get("feito"),
        "capexQueFalta": (cadeia or {}).get("falta"),
        "dataInicio": o["data_inicio"],
        "dataPronta": o["data_pronta"],
        "categoria": o["categoria_motivo"],
        "elo": o["elo_que_trava"],
        "narrativa": o["motivo"],
        "dependencias": [
            {
                "subbaciaId": d["sub_bacia"],
                "vazao": d["vazao_sub_bacia"],
                "fracaoRateio": d["fracao_rateio"],
                "capexRateado": d["capex_rateado"],
                "fatura": d["sub_bacia_faturando"],
            }
            for d in deps
        ],
    }
