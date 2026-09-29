"""OS NÍVEIS 2 A 5 — cidade, sistema, sub-bacia e obra.

A descida: de uma cidade para os seus sistemas, de um sistema para o desenho do
escoamento, de uma sub-bacia para a ficha dela, e de uma obra para o detalhe.
Mais a lista paginada de obras, que é a mesma leitura sem o recorte da árvore.

Saiu de `niveis.py`, com o vocabulário comum em `cascata.py`.
"""

import re
from typing import Any

from app.infra import db
from app.infra.repositorios import cascata as casc


#: A LINHA DO TEMPO DE UMA OBRA, em quatro fases:
#:
#:   predecessoras -> execucao -> espera ate a cobranca -> ramp-up da adesao
#:
#: Tres dessas datas o motor calcula e grava: o inicio da execucao (`data_inicio`), a
#: conclusao (`data_pronta`) e o inicio do faturamento (`data_inicio_faturamento`). A
#: quarta — o inicio das predecessoras — NAO existe no motor: `tempo_predecessoras` e um
#: PISO ("esta obra nao pode comecar antes do mes N"), e nao uma janela agendada. A data
#: que sai aqui e derivada, ancorando o fim do intervalo no inicio da execucao, que e a
#: leitura util para quem planeja: licenca e mobilizacao terminam quando a obra comeca.
#: Por isso ela vem com nome proprio (`inicioPredecessoras`) e nao se mistura com as
#: outras tres.
#: 'AAAA-MM' — o formato em que o motor grava mes. O mes vai de 01 a 12: sem isso,
#: '2035-13' viraria uma data normal, e errada.
_AAAA_MM = re.compile(r"(?P<ano>\d{4})-(?P<mes>0[1-9]|1[0-2])")


#: OS MÓDULOS DE UMA MESMA ETE VIRAM UMA LINHA SÓ.
#:
#: No modo faseado — o único que a tela dispara — cada módulo é uma OBRA própria
#: (`ete_x#m1`, `#m2`…), e a lista de obras mostrava a mesma ETE repetida três, quatro
#: vezes com "1 módulo" cada. Nos demais elementos uma obra traz a quantidade dela
#: (2.173,08 m de rede), e a ETE segue a mesma lógica: uma obra, N módulos.
#:
#: O pacote da ETE nova (`#nova`) e a expansão dela (`#x{k}`) NÃO se fundem: as datas
#: diferem por regra — a expansão só começa com o pacote pronto — e são decisões
#: distintas.
_CHAVE_DA_LINHA = (
    "CASE WHEN o.componente = 'ete_mod' THEN split_part(o.obra_id, '#', 1)"
    "     ELSE o.obra_id END"
)

#: O AGRUPAMENTO, UM SÓ PARA AS DUAS CONSULTAS da lista de obras — a da página e a do
#: total. Elas precisam contar a MESMA coisa: a rodada é imutável, e se discordassem a
#: tela pediria uma página que não existe ou esconderia obra que existe. Enquanto eram
#: dois `GROUP BY` escritos à mão, o da página tinha 15 colunas e o do total 3 — nenhuma
#: rodada do banco chegou a divergir, mas bastaria dois módulos da mesma ETE com
#: `lag_meses` diferente.
#:
#: Agrupa pelas DATAS também, e não só pela ETE: os módulos são obras independentes e o
#: otimizador PODE agendá-las em meses diferentes — hoje nunca o faz (conferido em 2.105
#: ETEs de vários módulos, nenhuma com datas distintas) —, e nesse dia as linhas se
#: separam sozinhas em vez de mentir uma data só.
AGRUPAMENTO = (
    f"{_CHAVE_DA_LINHA}, o.componente, o.responsavel, o.construida, o.cidade,"
    " o.no, o.unidade, o.data_inicio, o.data_pronta, o.prazo_meses,"
    " o.prazo_inicio_meses, o.lag_meses, o.maturacao_meses,"
    " o.data_inicio_faturamento, (o.faturando IS NOT NULL),"
    f" {casc.RECORTE_SQL}, COALESCE(o.sistema, s.sistema), o.status"
)


def _mes_antes(aaaa_mm: str | None, meses: int | None) -> str | None:
    """'2035-10' menos 7 meses -> '2035-03'. `None` quando nao da para calcular.

    RECUSA O QUE NAO E 'AAAA-MM'. A conversao ingenua (`int(s[:4])`, `int(s[5:7])`)
    aceitava '2035-13' e devolvia '2035-12' — uma data plausivel e errada, que e o
    pior desfecho possivel num numero de planejamento. E recusa o resultado fora do
    calendario: com um prazo maior que a ancora, a conta caia em ano negativo e
    devolvia '-001-12', texto que parece data e nao segue o contrato 'AAAA-MM'.

    Achado pela revisao do Codex em 28/09/2026.
    """
    if not aaaa_mm or not meses:
        return aaaa_mm
    m = _AAAA_MM.fullmatch(str(aaaa_mm).strip())
    if not m:
        return None
    total = int(m["ano"]) * 12 + (int(m["mes"]) - 1) - int(meses)
    if total < 0:
        return None
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _mes_depois(aaaa_mm: str | None, meses: int | None) -> str | None:
    """'2029-09' mais 7 meses -> '2030-04'. O par de `_mes_antes`, e recusa o mesmo
    que ela: o que nao e 'AAAA-MM' volta `None` em vez de virar data plausivel."""
    if not aaaa_mm or not meses:
        return aaaa_mm
    m = _AAAA_MM.fullmatch(str(aaaa_mm).strip())
    if not m:
        return None
    total = int(m["ano"]) * 12 + (int(m["mes"]) - 1) + int(meses)
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _capex_terreno(l: dict[str, Any]) -> float | None:
    """O que o CAPEX tem ALEM de `quantidade x preco_unitario` — na ETE, o terreno.

    E residual, e nao coluna: vale em todos os caminhos (pacote da ETE nova, modo
    modular) sem depender do nome que o motor deu a parcela no `capex_componentes`.
    Nas demais obras a conta fecha exata e isto sai `None`, que e o que a tela precisa
    para nao abrir uma coluna de zeros.
    """
    q, pu, cap = l.get("quantidade"), l.get("preco_unitario"), l.get("capex")
    if q is None or pu is None or cap is None:
        return None
    resto = float(cap) - float(q) * float(pu)
    return resto if abs(resto) > 0.01 else None


def _fases(l: dict[str, Any]) -> dict[str, Any]:
    """As quatro fases da linha de `otim_obra`.

    `lag_meses` e `maturacao_meses` SO SAEM NA OBRA DE COLETA. Nas demais eles carregam
    o default da classe `Obra` (1 e 2), que nao veio do cadastro e nao quer dizer nada:
    uma EEE nao tem "tempo ate a cobranca". Mostrar o default seria inventar dado de
    planejamento — e é o tipo de numero que alguem soma.
    """
    coleta = bool(l.get("eh_coleta"))
    #: O RAMP-UP COMECA COM A COBRANCA: a curva de adesao corre a partir do mes em que
    #: a sub-bacia passa a faturar, e a cobranca PLENA e o fim dela.
    inicio_fat = l["data_inicio_faturamento"] if coleta else None
    return {
        "prazoMeses": l["prazo_meses"],
        "mesesPredecessoras": l["prazo_inicio_meses"],
        "inicioPredecessoras": _mes_antes(l["data_inicio"], l["prazo_inicio_meses"]),
        "mesesAteCobranca": l["lag_meses"] if coleta else None,
        "dataInicioFaturamento": inicio_fat,
        "mesesRampUp": l["maturacao_meses"] if coleta else None,
        "dataCobrancaPlena": _mes_depois(inicio_fat, l["maturacao_meses"]) if coleta else None,
    }


async def obras(
    run_id: str,
    situacao: str | None = None,
    obra_ids: list[str] | None = None,
    cidade: str | None = None,
    ano: int | None = None,
    recorte: str | None = None,
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

    # UMA LISTA EXPLICITA DE OBRAS. Quem pede assim ja SABE quais sao — hoje o
    # cenario anual, que atribuiu cada obra a um ano em Python e precisa das
    # linhas completas daquelas e so daquelas. Refazer o criterio em SQL aqui
    # seria uma segunda implementacao da mesma regra, e a divergencia entre as
    # duas apareceria como uma barra que nao bate com a planilha dela.
    #
    # Lista VAZIA nao e "sem filtro": e um recorte que nao pegou nada, e devolver
    # a rodada inteira no lugar de zero linhas seria o pior erro possivel aqui.
    if obra_ids is not None:
        args.append(obra_ids)
        onde.append(f"o.obra_id = ANY(${len(args)}::text[])")
    if cidade:
        args.append(cidade)
        onde.append(f"o.cidade = ${len(args)}")
    if ano:
        # O ANO DE UMA OBRA DEPENDE DE QUEM A EXECUTA, e este filtro diz a MESMA
        # coisa que as barras do cronograma — de proposito, porque e clicando numa
        # barra que se chega aqui. Obra da Aegea pertence ao ano em que COMECA;
        # obra de terceiro, ao ano em que fica PRONTA, que e a unica data que o
        # motor calcula para ela (ver `nivel_global.cronograma_de_obras`).
        #
        # Sem isto, clicar num ano que so tem conclusao de terceiro abriria um
        # modal vazio sobre uma barra cheia.
        #
        # `data_inicio`/`data_pronta` sao texto 'AAAA-MM': comparar o prefixo
        # dispensa converter a coluna, e o ano sem obra nenhuma devolve pagina
        # vazia, nao erro.
        args.append(str(ano))
        onde.append(
            f"((NOT {casc.EH_TERCEIRO} AND LEFT(o.data_inicio, 4) = ${len(args)})"
            f" OR ({casc.EH_TERCEIRO} AND LEFT(o.data_pronta, 4) = ${len(args)}))"
        )

    if recorte and recorte != "todas":
        # O MESMO RECORTE DAS BARRAS, para o modal nao contradizer o grafico de
        # onde ele foi aberto: com o filtro em "obras de terceiro", a barra conta
        # 91 e a lista tem de trazer 91 — nao as 163 do ano inteiro.
        #
        # Recorte desconhecido nao vira filtro nenhum, e a lista sai completa. E
        # o mesmo criterio de `situacao`/`ordenar`: a querystring escolhe entre
        # valores previstos, nunca compoe SQL.
        # A lista de valores validos vive na ROTA, como `Literal` — ela recusa o
        # desconhecido com 422 antes de chegar aqui. Esta checagem sobra para
        # quem chamar a funcao direto (um script, um teste), e por isso ela
        # LEVANTA em vez de ignorar: filtro que nao se aplica em silencio
        # devolve a lista inteira parecendo filtrada.
        if recorte not in ("terceiro", "obrigatoria", "escolhida"):
            raise ValueError(f"recorte desconhecido: {recorte!r}")
        args.append(recorte)
        onde.append(f"{casc.RECORTE_SQL} = ${len(args)}")

    tamanho = max(1, min(tamanho, casc.TAMANHO_MAX))
    pagina = max(1, pagina)

    # O TOTAL VEM DE UMA CONSULTA PROPRIA, e nao de um `COUNT(*) OVER ()` na
    # pagina. A janela so devolve valor quando ha linha: com `pagina=9999` a
    # resposta saia `{"total": 0, "itens": []}` numa rodada com 7605 obras, e o
    # front nao tem como distinguir "acabou" de "nao ha nada". Uma rodada
    # publicada e imutavel, entao as duas consultas nao podem discordar.
    filtros = list(args)
    total = await db.buscar_um(
        f"""SELECT COUNT(*) AS total FROM (
                SELECT 1
                  FROM {casc.esquema()}.otim_obra o
                  LEFT JOIN {casc.esquema()}.otim_subbacia s
                         ON s.run_id = o.run_id AND s.sub_bacia = o.no
                 WHERE {' AND '.join(onde)}
                 GROUP BY {AGRUPAMENTO}
            ) AS agrupadas""",
        *filtros,
    )

    args.extend([tamanho, (pagina - 1) * tamanho])
    #: `MIN(o.obra_id)` é o id da linha: no grupo de um elemento só — todos, menos os
    #: módulos de ETE — é o próprio `obra_id`, e é por ele que a tela abre o detalhe.
    #: No grupo fundido ele é o primeiro módulo, e `obras_agrupadas` acima de 1 avisa a
    #: quem exibe que não há detalhe para abrir. `MAX(preco_unitario)` porque o unitário
    #: é o MESMO valor repetido em cada módulo — somá-lo multiplicaria o preço.
    linhas = await db.buscar(
        f"""SELECT MIN(o.obra_id) AS obra_id, COUNT(*) AS obras_agrupadas,
                   o.componente, o.responsavel, o.construida,
                   o.cidade, o.no,
                   SUM(o.capex) AS capex, SUM(o.quantidade) AS quantidade, o.unidade,
                   MAX(o.preco_unitario) AS preco_unitario,
                   o.data_inicio, o.data_pronta, o.prazo_meses,
                   -- AS QUATRO FASES DA OBRA, na ordem em que acontecem:
                   -- predecessoras -> execucao -> espera da cobranca -> ramp-up.
                   o.prazo_inicio_meses, o.lag_meses, o.maturacao_meses,
                   o.data_inicio_faturamento,
                   -- `faturando` so NAO e nulo na obra-ancora de coleta, e e por ela
                   -- que se sabe se `lag_meses`/`maturacao_meses` querem dizer algo:
                   -- nas demais eles sao o DEFAULT da classe `Obra` (1 e 2), e nao
                   -- dado do cadastro. Ver o mapeamento abaixo.
                   (o.faturando IS NOT NULL) AS eh_coleta,
                   -- O MESMO `CASE` que particiona o cronograma. Vem na linha
                   -- para a lista e a planilha poderem dizer POR QUE cada obra
                   -- esta no plano sem refazer a regra do lado do cliente.
                   {casc.RECORTE_SQL} AS recorte,
                   -- Ver a nota da consulta dos elos: `otim_obra.sistema` so vem
                   -- preenchido em parte das obras, e quem sabe o sistema das
                   -- demais e a sub-bacia em que elas estao.
                   COALESCE(o.sistema, s.sistema) AS sistema
              FROM {casc.esquema()}.otim_obra o
              LEFT JOIN {casc.esquema()}.otim_subbacia s
                     ON s.run_id = o.run_id AND s.sub_bacia = o.no
             WHERE {' AND '.join(onde)}
             GROUP BY {AGRUPAMENTO}
             ORDER BY {casc.ORDENS.get(ordenar, casc.ORDENS['inicio'])}
             LIMIT ${len(args) - 1} OFFSET ${len(args)}""",
        *args,
    )

    return {
        "total": (total or {}).get("total") or 0,
        "itens": [
            {
                "obraId": l["obra_id"],
                #: Quantas OBRAS esta linha representa. 1 em tudo, menos nos modulos de
                #: ETE fundidos — e e por ele que a tela sabe que nao ha uma pagina de
                #: detalhe para abrir: o detalhe e de UMA obra, e aqui sao varias.
                "obrasAgrupadas": l["obras_agrupadas"],
                "componente": casc.nome_componente(l["componente"]),
                "situacao": casc.situacao(l),
                "cidadeId": l["cidade"],
                "sistemaId": l["sistema"],
                # `null` para ETE e modulo de ETE: eles nao tem sub-bacia propria.
                "subBaciaId": l["no"],
                "capex": l["capex"],
                "recorte": l["recorte"],
                "dataPronta": l["data_pronta"],
                "quantidade": l["quantidade"],
                "unidade": l["unidade"],
                "anoInicio": int(str(l["data_inicio"])[:4]) if l["data_inicio"] else None,
                "precoUnitario": l["preco_unitario"],
                "capexTerreno": _capex_terreno(l),
                "dataInicio": l["data_inicio"],
                **_fases(l),
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
        # SEM O EFEITO-BASE — ver `cascata.SEM_EFEITO_BASE`. O `efeito` já foi
        # somado acima para o bloco de paridade; aqui ele sai do VPL.
        "vpl": casc.vpl_do_produto(base["vpl"], efeito),
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
            # CONTRA O VPL COM O EFEITO, e não contra o que a tela mostra: a
            # pergunta é "quanto do valor bruto vinha daqui", e dividir por um
            # VPL de onde ele já foi tirado daria uma fração de outra coisa.
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
        # SEM O EFEITO-BASE, como em todo lugar. A linha vem de `SELECT *`, então
        # `vp_efeito_base` já está aqui — e a cascata logo abaixo faz a mesma
        # subtração, pelo mesmo motivo: as duas têm de fechar.
        "vpl": casc.vpl_do_produto(base["vpl"], base["vp_efeito_base"]),
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
