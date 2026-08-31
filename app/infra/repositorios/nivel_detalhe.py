"""OS NÍVEIS 2 A 5 — cidade, sistema, sub-bacia e obra.

A descida: de uma cidade para os seus sistemas, de um sistema para o desenho do
escoamento, de uma sub-bacia para a ficha dela, e de uma obra para o detalhe.
Mais a lista paginada de obras, que é a mesma leitura sem o recorte da árvore.

Saiu de `niveis.py`, com o vocabulário comum em `cascata.py`.
"""

from typing import Any

from app.infra import db
from app.infra.repositorios import cascata as casc


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
    onde = ["o.run_id = $1", casc.SO_OBRA]
    args: list[Any] = [run_id]

    if situacao:
        args.append(situacao)
        onde.append(f"{casc.SITUACAO_SQL} = ${len(args)}")
    if cidade:
        args.append(cidade)
        onde.append(f"o.cidade = ${len(args)}")
    if ano:
        # `data_inicio` e texto 'AAAA-MM': comparar o prefixo dispensa converter a
        # coluna, e o ano sem obra nenhuma devolve pagina vazia, nao erro.
        args.append(str(ano))
        onde.append(f"LEFT(o.data_inicio, 4) = ${len(args)}")

    tamanho = max(1, min(tamanho, casc.TAMANHO_MAX))
    pagina = max(1, pagina)

    # O TOTAL VEM DE UMA CONSULTA PROPRIA, e nao de um `COUNT(*) OVER ()` na
    # pagina. A janela so devolve valor quando ha linha: com `pagina=9999` a
    # resposta saia `{"total": 0, "itens": []}` numa rodada com 7605 obras, e o
    # front nao tem como distinguir "acabou" de "nao ha nada". Uma rodada
    # publicada e imutavel, entao as duas consultas nao podem discordar.
    filtros = list(args)
    total = await db.buscar_um(
        f"""SELECT COUNT(*) AS total
              FROM {casc.esquema()}.otim_obra o
              LEFT JOIN {casc.esquema()}.otim_subbacia s
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
              FROM {casc.esquema()}.otim_obra o
              LEFT JOIN {casc.esquema()}.otim_subbacia s
                     ON s.run_id = o.run_id AND s.sub_bacia = o.no
             WHERE {' AND '.join(onde)}
             ORDER BY {casc.ORDENS.get(ordenar, casc.ORDENS['inicio'])}
             LIMIT ${len(args) - 1} OFFSET ${len(args)}""",
        *args,
    )

    return {
        "total": (total or {}).get("total") or 0,
        "itens": [
            {
                "obraId": l["obra_id"],
                "componente": casc.nome_componente(l["componente"]),
                "situacao": casc.situacao(l),
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

# ---------------------------------------------------------------- nível cidade
async def cidade(run_id: str, cidade_id: str) -> dict[str, Any] | None:
    base = await db.buscar_um(
        f"""SELECT * FROM {casc.esquema()}.otim_cidade WHERE run_id = $1 AND cidade = $2""",
        run_id,
        cidade_id,
    )
    if not base:
        return None

    cobertura = await db.buscar(
        f"""SELECT ano, cobertura_pct FROM {casc.esquema()}.otim_cobertura
             WHERE run_id = $1 AND cidade = $2 ORDER BY ano""",
        run_id,
        cidade_id,
    )
    metas = await db.buscar(
        f"""SELECT ano, pct_alvo, cobertura_ligacoes, alvo_ligacoes, atingida,
                   dentro_janela_capex
              FROM {casc.esquema()}.otim_meta_cobertura
             WHERE run_id = $1 AND cidade = $2 ORDER BY ano""",
        run_id,
        cidade_id,
    )
    sistemas = await db.buscar(
        f"""SELECT sistema, sub_bacias, sub_bacias_faturando,
                   capex_modulos_construidos, ocupacao_pct
              FROM {casc.esquema()}.otim_sistema
             WHERE run_id = $1 AND cidade = $2 ORDER BY sistema""",
        run_id,
        cidade_id,
    )
    horizonte = await db.buscar_um(
        f"""SELECT MAX(ano_fim_concessao) AS fim FROM {casc.esquema()}.otim_sistema
             WHERE run_id = $1 AND cidade = $2""",
        run_id,
        cidade_id,
    )
    ef = await db.buscar_um(
        f"""SELECT COALESCE(SUM(vp_efeito_base), 0) AS efeito
              FROM {casc.esquema()}.otim_subbacia WHERE run_id = $1 AND cidade = $2""",
        run_id,
        cidade_id,
    )
    efeito = (ef or {}).get("efeito") or 0

    return {
        "id": cidade_id,
        "nome": cidade_id,
        "fimConcessao": (horizonte or {}).get("fim"),
        "fimCapex": await casc.fim_capex(run_id),
        "capexTotal": base["capex_total"],
        "vpl": base["vpl"],
        "ligacoesNovas": base["ligacoes_novas"],
        "coberturaBasePct": base["cobertura_base_pct"],
        "coberturaFinalPct": base["cobertura_final_pct"],
        "cobertura": [
            {"ano": c["ano"], "coberturaPct": c["cobertura_pct"]} for c in cobertura
        ],
        "metas": [casc.meta_de_cobertura(m) for m in metas],
        "cascata": await casc.cascata_do_fluxo(run_id, cidade=cidade_id),
        "elementosPorAno": casc.elementos_por_ano(
            await casc.obras_do_plano_por_ano(run_id, cidade=cidade_id)
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
            "pctDoVplDaCidade": casc.pct(efeito, base["vpl"]),
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
        f"SELECT * FROM {casc.esquema()}.otim_sistema WHERE run_id = $1 AND sistema = $2",
        run_id,
        sistema_id,
    )
    if not sistema:
        return None

    nos = await db.buscar(
        f"""SELECT sub_bacia, is_cts, vazao_marginal, faturando, jusante
              FROM {casc.esquema()}.otim_subbacia
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
              FROM {casc.esquema()}.otim_obra
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
        "elementosPorAno": casc.elementos_por_ano(
            await casc.obras_do_plano_por_ano(run_id, sistema=sistema_id)
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
                "componentes": [casc.componente(o) for o in por_no.get(n["sub_bacia"], [])],
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
                casc.componente(o) for o in por_no.get(sistema["ete_id"], [])
            ],
        },
    }

# -------------------------------------------------------------- nível sub-bacia
async def subbacia(run_id: str, sub_id: str) -> dict[str, Any] | None:
    base = await db.buscar_um(
        f"SELECT * FROM {casc.esquema()}.otim_subbacia WHERE run_id = $1 AND sub_bacia = $2",
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
                  FROM {casc.esquema()}.otim_subbacia_ano
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
              FROM {casc.esquema()}.otim_obra
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
            f"SELECT jusante FROM {casc.esquema()}.otim_subbacia WHERE run_id = $1 AND sub_bacia = $2",
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
        "cascata": await casc.cascata_do_fluxo(run_id, sub_bacia=sub_id),
        "elementosPorAno": casc.elementos_por_ano(
            await casc.obras_do_plano_por_ano(run_id, sub_bacia=sub_id)
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
                "componente": casc.nome_componente(e["componente"]),
                "situacao": casc.situacao(e),
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
        f"SELECT * FROM {casc.esquema()}.otim_obra WHERE run_id = $1 AND obra_id = $2",
        run_id,
        obra_id,
    )
    if not o:
        return None

    deps = await db.buscar(
        f"""SELECT sub_bacia, vazao_sub_bacia, fracao_rateio, capex_rateado,
                   sub_bacia_faturando
              FROM {casc.esquema()}.otim_dependencia
             WHERE run_id = $1 AND obra_id = $2 ORDER BY sub_bacia""",
        run_id,
        obra_id,
    )

    # `capexConstruido` / `capexQueFalta` são da CADEIA da sub-bacia, não da obra:
    # respondem "quão longe ela está de faturar".
    cadeia = await db.buscar_um(
        f"""SELECT COALESCE(SUM(capex) FILTER (WHERE construida), 0)     AS feito,
                   COALESCE(SUM(capex) FILTER (WHERE NOT construida), 0) AS falta
              FROM {casc.esquema()}.otim_obra WHERE run_id = $1 AND no = $2""",
        run_id,
        o["no"],
    )

    return {
        "obraId": obra_id,
        "componente": casc.nome_componente(o["componente"]),
        "rotulo": f"{obra_id} (CTS)" if o["is_cts"] else obra_id,
        "situacao": casc.situacao(o),
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
