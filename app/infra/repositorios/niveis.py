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
    por_componente = await db.buscar(
        f"""SELECT componente, SUM(capex) AS capex
              FROM {_p()}.otim_obra
             WHERE run_id = $1 AND construida
             GROUP BY componente HAVING SUM(capex) > 0""",
        run_id,
    )
    total_capex = sum(c["capex"] for c in por_componente) or None

    # Histograma do VPL por sub-bacia: `width_bucket` deixaria as faixas dependendo
    # do min/max da rodada, e duas rodadas ficariam incomparáveis. Faixa fixa de
    # 1 milhão mantém o eixo estável entre rodadas.
    histograma = await db.buscar(
        f"""SELECT (floor(vpl / 1e6) * 1e6)::float8 AS de,
                   (floor(vpl / 1e6) * 1e6 + 1e6)::float8 AS ate,
                   COUNT(*) AS quantidade
              FROM {_p()}.otim_subbacia WHERE run_id = $1
             GROUP BY 1, 2 ORDER BY 1""",
        run_id,
    )
    sinal = await db.buscar_um(
        f"""SELECT COUNT(*) FILTER (WHERE vpl > 0) AS positivas,
                   COUNT(*) FILTER (WHERE vpl <= 0) AS negativas
              FROM {_p()}.otim_subbacia WHERE run_id = $1""",
        run_id,
    )
    obras_ano = await db.buscar(
        f"""SELECT EXTRACT(YEAR FROM to_date(data_inicio, 'YYYY-MM'))::int AS ano,
                   componente, COUNT(*) AS quantidade
              FROM {_p()}.otim_obra
             WHERE run_id = $1 AND construida AND data_inicio IS NOT NULL
             GROUP BY 1, 2 ORDER BY 1""",
        run_id,
    )
    por_ano: dict[int, list[dict[str, Any]]] = {}
    for o in obras_ano:
        por_ano.setdefault(o["ano"], []).append(
            {"componente": nome_componente(o["componente"]), "quantidade": o["quantidade"]}
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
                }
                for c in por_componente
            ),
            key=lambda c: ORDEM_COMPONENTES.index(c["componente"])
            if c["componente"] in ORDEM_COMPONENTES
            else len(ORDEM_COMPONENTES),
        ),
        "histogramaVpl": [
            {"de": h["de"], "ate": h["ate"], "quantidade": h["quantidade"]} for h in histograma
        ],
        "subbaciasPositivas": (sinal or {}).get("positivas", 0),
        "subbaciasNegativas": (sinal or {}).get("negativas", 0),
        "obrasPorAno": [
            {"ano": ano, "porComponente": comps} for ano, comps in sorted(por_ano.items())
        ],
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
        }
        for l in linhas
    ]


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
        "metas": [
            {
                "ano": m["ano"],
                # `pct_alvo` vem do motor como FRACAO (0.4); o contrato pede
                # percentual (40.0) — campos `Pct` vao de 0 a 100. E o realizado e
                # o alvo reescalado pela razao entre o que se cobriu e o que se
                # devia cobrir: 524 de 400 ligacoes com alvo de 40% da 52,4%.
                "alvoPct": _escala_pct(m["pct_alvo"]),
                "realizadoPct": _realizado_pct(
                    m["cobertura_ligacoes"], m["alvo_ligacoes"], m["pct_alvo"]
                ),
                "atingida": m["atingida"],
                "dentroDaJanela": m["dentro_janela_capex"],
            }
            for m in metas
        ],
        "cascata": await _cascata(run_id, cidade=cidade_id),
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
        "receita": [
            {"ano": r["ano"], "direta": r["receita_direta"], "indireta": r["receita_indireta"]}
            for r in receita
        ],
        "explicacao": {
            "categoria": next(
                (e["categoria_motivo"] for e in elementos if e.get("categoria_motivo")), None
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
