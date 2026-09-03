"""POR QUE FICOU DE FORA — e o que o dinheiro a mais compraria.

As sub-bacias que não faturam, agrupadas por motivo, e os elos: as obras que, não
construídas, travam mais de uma sub-bacia. Junto delas ficam os dois insumos da
análise de sensibilidade que vêm da mesma leitura — as candidatas do teto e a
contagem de obras por componente.

Estão no mesmo arquivo porque respondem à MESMA pergunta a partir do MESMO dado:
o que ficou fora do plano, e quanto custaria trazê-lo. Separá-las por serem "de
telas diferentes" partiria uma leitura só em duas.

Saiu de `niveis.py`, com o vocabulário comum em `cascata.py`.
"""

import json
from typing import Any

from app.infra import db
from app.infra.repositorios import cascata as casc
from app.infra.repositorios import nivel_detalhe
from app.dominio import teto as teto_dom


# ------------------------------------------- nível global: por que não fatura
#
# A pergunta que estes dois endpoints respondem — "por que o plano não conecta
# 100%?" — já tinha resposta no nível 4, uma sub-bacia por vez (`subbacia`, campo
# `explicacao`). O que faltava era chegar nela SEM já saber qual sub-bacia abrir.


async def explicabilidade(
    run_id: str, cidade: str | None = None, sistema: str | None = None
) -> dict[str, Any] | None:
    """As OBRAS que não entraram no plano, em três tópicos — e o que elas custariam.

    Com `cidade` ou `sistema`, o mesmo recorte dentro de um deles. Devolve `None`
    quando o recorte não existe naquela rodada, para o endpoint responder 404 em
    vez de zeros que parecem dado.

    ## A unidade é a OBRA, e não a sub-bacia

    Era a sub-bacia que não fatura. A troca não é de rótulo: **a lista antiga não
    tinha onde pôr 85% do dinheiro que ficou de fora**. Obra de transporte —
    tronco, elevatória, módulo de ETE — não tem sub-bacia própria, então não cabia
    numa lista cuja linha é uma sub-bacia. No maior run publicado eram 4.531
    obras e R$ 4,4 bi invisíveis, contra R$ 773 Mi que a tela mostrava.

    E a pergunta do produto é sobre obra: uma sub-bacia não "entra no plano" —
    quem entra ou não é a obra que a atende.

    ## Por que TRÊS tópicos, e por que estes

    O agrupamento é pelo que a pessoa pode FAZER a respeito, que é o que separa
    três tópicos úteis de três rótulos:

      orcamento    o plano quis e o teto acabou. É o único que mais dinheiro
                   compra — e é o que a análise de sensibilidade precifica.
      nao_se_paga  a receita própria não cobre, sozinha ou em conjunto. Mais
                   orçamento NÃO compra; preço, custo ou meta compram.
      depende      infraestrutura compartilhada que ninguém acionou, porque o
                   que ela serviria não entrou. É consequência, não decisão.

    A LINHA CAI NA RECEITA, e é isso que faz os três tópicos serem do domínio e
    não da tela: só ligação e CTS faturam. Os dois primeiros tópicos são 100%
    obras com receita própria; o terceiro é 99,5% sem — só CAPEX e OPEX, e existe
    para o esgoto chegar à ETE. Medido no maior run: 1.142 de 1.142, 1.070 de
    1.070, e 22 de 4.553.

    `outros` é válvula de segurança, não quarto tópico: categoria nova que o
    motor invente cai nele e APARECE, em vez de sumir do agregado e fazer as
    parcelas não fecharem com o cabeçalho. Hoje vem vazio.

    ## O que fica de fora da conta, e por quê

    `necessaria` já exclui o que nunca foi obra (CAPEX e prazo zero: o elemento
    existe na ficha e não gera obra nenhuma). Sobra `Terceiro (pre-requisito)`,
    que é obra que ACONTECE — só que outro paga. Não é decisão de investimento
    do plano, e somá-la ao "ficou de fora" inflaria a conta com linhas que
    ninguém pode acionar. Vem no cabeçalho, contada à parte.

    OBRA CONSTRUÍDA NÃO ENTRA AQUI, por definição: a pergunta é o que ficou fora.

    ## Por que agregado, e não a lista inteira

    A lista antiga vinha completa — 2.269 sub-bacias, 247 KB. Em obras seriam
    6.765, e mandar tudo trocaria uma tela pesada por uma tela ilegível. O
    agregado por COMPONENTE responde melhor à mesma pergunta (são seis tipos de
    obra na base inteira), e `maiores` traz as dez de maior CAPEX de cada tópico
    — rotuladas como tal na tela, nunca como se fossem a lista toda.
    """
    esq = casc.esquema()

    # O SISTEMA DE UMA OBRA DE TRANSPORTE VEM VAZIO (6.695 das 8.079 do maior
    # run): o motor só preenche `otim_obra.sistema` onde a obra pertence a um
    # sistema por si. Quem sabe o sistema é a sub-bacia do nó dela — o mesmo
    # `COALESCE` que a consulta de elos já fazia. Sem isto, o recorte do nível 3
    # perderia justamente as obras de transporte, que são o assunto.
    de_obras = f"""
        FROM {esq}.otim_obra o
        LEFT JOIN {esq}.otim_subbacia sn
               ON sn.run_id = o.run_id AND sn.sub_bacia = o.no
    """
    #: OS DOIS RECORTES SÃO EXCLUSIVOS, e isto é uma exceção e não um `if`
    #: silencioso: a consulta principal filtraria os dois, mas a de elos só
    #: aplica o primeiro — o resultado seria uma tela em que os números de cima
    #: e a lista de baixo falam de conjuntos diferentes. As rotas de hoje nunca
    #: passam os dois; quem passar tem de saber que não é suportado.
    if cidade and sistema:
        raise ValueError("recorte por cidade e por sistema ao mesmo tempo não é suportado")

    #: O `WHERE` EM PARTES NOMEADAS, e não numa lista de onde depois se remove
    #: por comparação de texto. `candidatas` precisa do MESMO recorte sem as
    #: cláusulas de "ficou fora" — era `x not in ("o.necessaria", ...)`, que
    #: passaria a devolver o denominador errado no dia em que alguém escrevesse
    #: `o.necessaria = TRUE`. Sem erro, sem teste vermelho: só uma porcentagem
    #: menor do que deveria.
    base = ["o.run_id = $1"]
    fora = ["o.necessaria", "NOT o.construida"]
    args: list = [run_id]
    if cidade:
        args.append(cidade)
        base.append(f"o.cidade = ${len(args)}")
    if sistema:
        args.append(sistema)
        base.append(f"COALESCE(NULLIF(o.sistema, ''), sn.sistema) = ${len(args)}")
    filtro = " AND ".join(base + fora)

    if cidade or sistema:
        #: A EXISTÊNCIA É CONFERIDA NO MESMO UNIVERSO EM QUE SE FILTRA.
        #:
        #: Era `otim_subbacia` sozinha, e o filtro real é
        #: `COALESCE(NULLIF(o.sistema,''), sn.sistema)` sobre `otim_obra` — dois
        #: conjuntos que nada no schema obriga a coincidir (não há FK de
        #: `otim_obra.sistema` para lugar nenhum). Nos dados de hoje coincidem;
        #: no dia em que um sistema existir só em obra, a tela responderia 404
        #: sobre um recorte que tem obras.
        existe = await db.buscar_um(
            f"""SELECT 1
                  {de_obras}
                 WHERE {" AND ".join(base)}
                 LIMIT 1""",
            *args,
        )
        if not existe:
            return None

    #: O motor nomeia o motivo; a tela pergunta o que fazer. Este mapa é a
    #: tradução, e mora aqui porque é regra de domínio — a tela não pode
    #: reagrupar sem mudar o significado.
    topico_sql = """
        CASE
          WHEN o.categoria_motivo = 'Perdeu a disputa pelo orcamento' THEN 'orcamento'
          WHEN o.categoria_motivo IN ('Nao se paga', 'So se paga em conjunto')
               THEN 'nao_se_paga'
          WHEN o.categoria_motivo IN ('Compartilhada nao acionada',
                                      'Travada por obra da cadeia') THEN 'depende'
          WHEN o.categoria_motivo = 'Terceiro (pre-requisito)' THEN 'terceiro'
          ELSE 'outros'
        END
    """

    por_componente = await db.buscar(
        f"""SELECT {topico_sql} AS topico, o.componente,
                   COUNT(*) AS obras,
                   COALESCE(SUM(o.capex), 0) AS capex,
                   COALESCE(SUM(o.ligacoes), 0) AS ligacoes
            {de_obras}
             WHERE {filtro}
             GROUP BY 1, 2
             ORDER BY 1, capex DESC""",
        *args,
    )

    maiores = await db.buscar(
        f"""SELECT topico, obra_id, componente, cidade, sistema, no, capex, ligacoes
              FROM (
                SELECT {topico_sql} AS topico, o.obra_id, o.componente, o.cidade,
                       COALESCE(NULLIF(o.sistema, ''), sn.sistema) AS sistema,
                       o.no, COALESCE(o.capex, 0) AS capex,
                       COALESCE(o.ligacoes, 0) AS ligacoes,
                       ROW_NUMBER() OVER (
                           PARTITION BY {topico_sql}
                           ORDER BY COALESCE(o.capex, 0) DESC, o.obra_id
                       ) AS pos
                {de_obras}
                 WHERE {filtro}
              ) x
             WHERE pos <= 10
             ORDER BY topico, capex DESC""",
        *args,
    )

    candidatas = await db.buscar_um(
        f"""SELECT COUNT(*) FILTER (WHERE o.necessaria) AS candidatas,
                   COUNT(*) FILTER (WHERE o.necessaria AND o.construida) AS no_plano
            {de_obras}
             WHERE {" AND ".join(base)}""",
        *args,
    )

    #: A ORDEM É FIXA, e é leitura: primeiro o que dinheiro resolve, depois o que
    #: não resolve, por fim o que é consequência dos dois. Ordenar por tamanho
    #: poria sempre o terceiro no topo — ele tem 4 mil obras — e enterraria o
    #: único acionável.
    ORDEM = ("orcamento", "nao_se_paga", "depende", "outros")

    por_topico: dict[str, dict[str, Any]] = {}
    de_terceiros = 0
    for l in por_componente:
        if l["topico"] == "terceiro":
            de_terceiros += l["obras"]
            continue
        t = por_topico.setdefault(
            l["topico"],
            {"topico": l["topico"], "obras": 0, "capex": 0.0, "ligacoes": 0.0,
             "porComponente": [], "maiores": []},
        )
        t["obras"] += l["obras"]
        t["capex"] += l["capex"]
        t["ligacoes"] += l["ligacoes"]
        t["porComponente"].append(
            {
                "componente": casc.nome_componente(l["componente"]),
                "obras": l["obras"],
                "capex": l["capex"],
            }
        )

    for m in maiores:
        t = por_topico.get(m["topico"])
        if t is None:
            continue
        t["maiores"].append(
            {
                "obraId": m["obra_id"],
                "componente": casc.nome_componente(m["componente"]),
                "cidadeId": m["cidade"],
                "sistemaId": m["sistema"],
                "subBaciaId": m["no"],
                "capex": m["capex"],
                "ligacoes": m["ligacoes"],
            }
        )

    topicos = [por_topico[k] for k in ORDEM if k in por_topico]

    elos = await db.buscar(
        f"""WITH presos AS (
                SELECT DISTINCT o.elo_que_trava AS elo, o.no AS sub_bacia
                  FROM {esq}.otim_obra o
                 WHERE o.run_id = $1
                   AND o.elo_que_trava IS NOT NULL
                   AND o.no IS NOT NULL
            )
            SELECT p.elo AS obra_id, e.componente, e.cidade, e.no,
                   COALESCE(e.sistema, se.sistema) AS sistema,
                   COUNT(*) AS bloqueia,
                   COALESCE(SUM(s.vazao_marginal), 0) AS vazao_liberada
              FROM presos p
              JOIN {esq}.otim_subbacia s
                ON s.run_id = $1 AND s.sub_bacia = p.sub_bacia
              LEFT JOIN {esq}.otim_obra e
                     ON e.run_id = $1 AND e.obra_id = p.elo
              LEFT JOIN {esq}.otim_subbacia se
                     ON se.run_id = $1 AND se.sub_bacia = e.no
             WHERE {"s.cidade = $2" if cidade else ("COALESCE(e.sistema, se.sistema) = $2" if sistema else "TRUE")}
             GROUP BY p.elo, e.componente, e.cidade, e.sistema, se.sistema, e.no
             ORDER BY vazao_liberada DESC, bloqueia DESC, p.elo""",
        *(args[:2] if (cidade or sistema) else args[:1]),
    )

    return {
        "obrasFora": sum(t["obras"] for t in topicos),
        "capexFora": sum(t["capex"] for t in topicos),
        "ligacoesFora": sum(t["ligacoes"] for t in topicos),
        "obrasCandidatas": (candidatas or {}).get("candidatas") or 0,
        "obrasNoPlano": (candidatas or {}).get("no_plano") or 0,
        "deTerceiros": de_terceiros,
        "topicos": topicos,
        "elos": [
            {
                "obraId": e["obra_id"],
                "componente": casc.nome_componente(e["componente"]),
                "cidadeId": e["cidade"],
                "sistemaId": e["sistema"],
                "subBaciaId": e["no"],
                "bloqueia": e["bloqueia"],
                "vazaoLiberada": e["vazao_liberada"],
            }
            for e in elos
        ],
    }


async def _obras_fora(run_id: str) -> list[dict[str, Any]]:
    """As obras que ficaram fora, cada uma com o VPL do conjunto que ela serve.

    Uma consulta só, usada pelo cenário e pela lista que o download leva — e é
    por isso que ela é uma função: se as duas montassem o recorte por conta
    própria, o número da barra e a planilha divergiriam sem nada acusar.

    `positivo` é o VPL do CONJUNTO, e não da obra: uma ligação que perde por
    orçamento arrasta com ela toda a cadeia até a ETE, e é o saldo do conjunto
    (`otim_subbacia.pot_saldo_rateado`, já rateado pelo motor) que diz se aquilo
    valia a pena. Por isso a obra herda o veredito do melhor conjunto que serve.
    """
    esq = casc.esquema()
    return await db.buscar(
        f"""WITH o AS (
                -- MESMO RECORTE DA EXPLICABILIDADE: obra de terceiro ACONTECE e
                -- outro paga, entao nao entra em "quanto custaria".
                SELECT obra_id, no, sistema, cidade, capex, componente, construida,
                       elo_que_trava
                  FROM {esq}.otim_obra
                 WHERE run_id = $1 AND necessaria AND NOT construida
                   AND categoria_motivo <> 'Terceiro (pre-requisito)'
            ),
            todas AS (
                SELECT obra_id, no, elo_que_trava FROM {esq}.otim_obra WHERE run_id = $1
            ),
            sb AS (
                SELECT sub_bacia, sistema, pot_saldo_rateado
                  FROM {esq}.otim_subbacia
                 WHERE run_id = $1 AND NOT faturando
            ),
            -- QUEM A OBRA SERVE: o proprio no, todo no que ela destrava, e — na
            -- ponta da cadeia — o sistema inteiro. A ETE nao tem no proprio: ela
            -- E o fim do caminho, e sem esta terceira via os 844 modulos ficariam
            -- sem classificacao, que e R$ 947 Mi somindo da conta.
            serve AS (
                SELECT f.obra_id, f.no AS sub_bacia FROM o f
                 WHERE f.no IS NOT NULL AND f.no <> ''
                UNION
                SELECT f.obra_id, t.no FROM o f
                  JOIN todas t ON t.elo_que_trava = f.obra_id
                                AND t.no IS NOT NULL AND t.no <> ''
                UNION
                SELECT f.obra_id, s2.sub_bacia FROM o f
                  JOIN sb s2 ON s2.sistema = f.sistema
                 WHERE f.no IS NULL OR f.no = ''
            )
            SELECT f.obra_id, f.componente, f.cidade, f.no,
                   COALESCE(f.capex, 0) AS capex,
                   COALESCE(BOOL_OR(sb.pot_saldo_rateado > 0), FALSE) AS positivo
              FROM o f
              LEFT JOIN serve s ON s.obra_id = f.obra_id
              LEFT JOIN sb ON sb.sub_bacia = s.sub_bacia
             GROUP BY f.obra_id, f.componente, f.cidade, f.no, f.capex
             ORDER BY COALESCE(f.capex, 0) DESC, f.obra_id""",
        run_id,
    )


def _distribuir(obras: list[dict[str, Any]], janela: list[tuple[int, float]]) -> dict[str, int]:
    """Em que ano cada obra entraria — obra a obra, e nao em fatia de porcentagem.

    ERA UM RATEIO. Cada componente tinha o total dele multiplicado pelo peso do
    ano, o que dava barras certas e uma mentira embaixo: nenhuma obra pertencia a
    ano nenhum, entao "os troncos de 2029" nao existiam para baixar. Com a
    atribuicao, cada obra cai em UM ano — e a planilha de uma barra e exatamente
    o que aquela barra soma.

    A REGRA E MAIOR-PRIMEIRO NO ANO MAIS VAZIO (`LPT`, a heuristica classica de
    balanceamento): as obras descem por CAPEX e cada uma vai para o ano com mais
    espaco sobrando em relacao a cota dele. As cotas seguem o PERFIL do orcamento
    atual — mesma forma, escala maior —, e o resultado fica proximo delas sem
    quebrar obra em pedacos.

    NAO E UMA OTIMIZACAO, e a tela diz isso. O motor decide sequencia com
    precedencia, prazo e receita; aqui a pergunta e outra — "de quanto teria de
    ser o orcamento" —, e para respondê-la basta uma distribuicao que respeite as
    cotas e nao invente ordem de execucao.
    """
    total = sum(v for _, v in janela) or 1.0
    falta = {ano: sum(o["capex"] for o in obras) * (v / total) for ano, v in janela}
    onde: dict[str, int] = {}
    for o in obras:
        ano = max(falta, key=lambda a: falta[a])
        onde[o["obra_id"]] = ano
        falta[ano] -= o["capex"]
    return onde


async def _janela_do_orcamento(run_id: str) -> list[tuple[int, float]]:
    """Os anos do cenário e a cota de cada um, do orçamento que a rodada publicou.

    ANO COM ORÇAMENTO ZERO NÃO É ANO DA JANELA. O ano-base vem com 0.0, e
    incluí-lo daria uma barra que promete investimento onde não há nenhum.

    Vazia quando a rodada não publicou orçamento — quem chama devolve `None`.
    """
    esq = casc.esquema()
    meta = await db.buscar_um(
        f"""SELECT orcamento_por_ano::jsonb AS orc FROM {esq}.otim_meta WHERE run_id = $1""",
        run_id,
    )
    if not meta or not meta["orc"]:
        return []
    orc = meta["orc"] if isinstance(meta["orc"], dict) else json.loads(meta["orc"])
    return sorted(
        (int(a), float(v))
        for a, v in orc.items()
        if isinstance(v, (int, float)) and float(v) > 0
    )


async def cenario_anual(run_id: str) -> dict[str, Any] | None:
    """DE QUANTO TERIA DE SER O ORÇAMENTO ANUAL para fazer tudo na MESMA janela.

    A pergunta veio depois de duas tentativas que os dados recusaram, e as duas
    recusas estão registradas porque elas explicam por que esta é a certa:

      "sem limite de CAPEX, o que entraria em cada ano?"  —  6.645 das 7.325
      obras podem começar no primeiro ano. Tirado o dinheiro, não sobra nada
      segurando obra nenhuma: o gráfico é uma torre e três anos vazios.

      "quantos anos, ao orçamento de hoje, para fazer tudo que se paga?"  —  64.
      Setenta barras não são um gráfico.

    A saída é fixar a JANELA e perguntar do orçamento. Mesmos anos, mesma régua
    do gráfico de obras por ano, e a resposta vira um fator: 11,7x para tudo que
    se paga, 18,4x para todas as obras.

    ## Cada obra cai em UM ano — atribuição, e não rateio

    As cotas seguem o perfil do orçamento que a rodada já tem
    (`otim_meta.orcamento_por_ano`): mesma forma, escala maior. Mas o que se
    distribui são as OBRAS, uma a uma (`_distribuir`), e não o valor de cada
    componente multiplicado pelo peso do ano.

    Era rateio, e o rateio dava barras certas com uma mentira embaixo: nenhuma
    obra pertencia a ano nenhum, então "os troncos de 2029" não existiam para
    listar nem para baixar. Com a atribuição, a planilha de uma fatia é
    exatamente o que aquela fatia soma — e é `obras_do_cenario` que a entrega,
    reusando esta mesma distribuição para as duas não poderem divergir.

    Distribuir pelo `inicio_min_mes` de cada obra seria mais sofisticado e MENOS
    verdadeiro: 91% delas podem começar no primeiro ano, então o resultado seria
    a mesma torre que motivou a troca de pergunta.

    ## `noPlano` sai do ano em que a obra COMEÇA

    É a régua do gráfico de obras por ano, e mantê-la é o que deixa os dois
    gráficos comparáveis lado a lado.
    """
    esq = casc.esquema()
    janela = await _janela_do_orcamento(run_id)
    if not janela:
        return None
    total_orcado = sum(v for _, v in janela)

    obras = await _obras_fora(run_id)
    plano = await db.buscar_um(
        f"""SELECT COALESCE(SUM(capex), 0) AS capex, COUNT(*) AS obras
              FROM {esq}.otim_obra
             WHERE run_id = $1 AND necessaria AND construida""",
        run_id,
    ) or {}
    capex_plano = float(plano.get("capex") or 0.0)
    obras_plano = int(plano.get("obras") or 0)

    paga = [o for o in obras if o["positivo"]]
    falta_paga = sum(o["capex"] for o in paga)
    falta_toda = sum(o["capex"] for o in obras)
    obras_paga = len(paga)
    obras_toda = len(obras)

    #: DUAS DISTRIBUICOES, uma por escopo, e nao uma filtrada depois. "So o que
    #: se paga" e um cenario menor: as cotas de cada ano sao outras, e as obras
    #: se acomodam de outro jeito. Reaproveitar a distribuicao de "todas" e
    #: filtrar deixaria os anos desbalanceados, com a barra menor num ano e
    #: quase vazia noutro por acidente de quem saiu.
    onde = {"todas": _distribuir(obras, janela), "paga": _distribuir(paga, janela)}

    por_ano: dict[int, dict[str, dict[str, float]]] = {
        ano: {} for ano, _ in janela
    }
    ordem: list[tuple[str, str]] = []
    total_por_comp: dict[str, float] = {}
    for o in obras:
        nome = casc.nome_componente(o["componente"])
        cod = o["componente"]
        if cod not in total_por_comp:
            ordem.append((cod, nome))
            total_por_comp[cod] = 0.0
        total_por_comp[cod] += o["capex"]

        alvo = por_ano[onde["todas"][o["obra_id"]]].setdefault(
            cod, {"queSePaga": 0.0, "todas": 0.0}
        )
        alvo["todas"] += o["capex"]
        if o["positivo"]:
            por_ano[onde["paga"][o["obra_id"]]].setdefault(
                cod, {"queSePaga": 0.0, "todas": 0.0}
            )["queSePaga"] += o["capex"]

    #: A ORDEM DOS TIPOS E A MESMA EM TODOS OS ANOS, por CAPEX total decrescente:
    #: pilha que troca de ordem entre barras nao se compara. E todo tipo aparece
    #: em todo ano, mesmo com zero — sem isso a fatia sumiria da legenda de um
    #: ano e a leitura ficaria diferente de barra para barra.
    ordem.sort(key=lambda c: total_por_comp[c[0]], reverse=True)

    plano_por_ano = await db.buscar(
        f"""SELECT FLOOR(mes_inicio / 12)::int AS ano_rel,
                   COUNT(*) AS obras, COALESCE(SUM(capex), 0) AS capex
              FROM {esq}.otim_obra
             WHERE run_id = $1 AND necessaria AND construida AND mes_inicio IS NOT NULL
             GROUP BY 1""",
        run_id,
    )
    #: `mes_inicio` 0 E O PRIMEIRO ANO DA JANELA, e nao o anterior.
    #:
    #: Havia um `- 1` aqui, e ele deslocava o CAPEX do plano um ano inteiro para
    #: tras na tabela do quadro. Medido no run_20260901_145746_581dc6 contra
    #: `data_inicio`, que e a data que o motor grava: com o `- 1`, as 299 obras
    #: construidas caiam no ano errado; sem ele, nenhuma.
    #:
    #: Nao e obvio porque `janela` descarta o ano-base (orcamento 0.0), e a
    #: intuicao e que descartar um ano exige recuar um. Nao exige: `mes_inicio`
    #: conta a partir do primeiro ano COM orcamento, que e exatamente
    #: `janela[0]`.
    ano0 = janela[0][0]
    do_plano = {ano0 + int(l["ano_rel"]): l for l in plano_por_ano}

    anos = []
    for ano, orcado in janela:
        no_plano = do_plano.get(ano)
        fatias = por_ano[ano]
        anos.append(
            {
                "ano": ano,
                "orcado": orcado,
                "noPlano": float(no_plano["capex"]) if no_plano else 0.0,
                "obrasNoPlano": int(no_plano["obras"]) if no_plano else 0,
                "faltaQueSePaga": sum(v["queSePaga"] for v in fatias.values()),
                "faltaTodas": sum(v["todas"] for v in fatias.values()),
                "porComponente": [
                    {
                        "componente": nome,
                        "codigo": cod,
                        "queSePaga": fatias.get(cod, {}).get("queSePaga", 0.0),
                        "todas": fatias.get(cod, {}).get("todas", 0.0),
                    }
                    for cod, nome in ordem
                ],
            }
        )

    #: A NOTA: quantas das que ficaram fora poderiam comecar JA no primeiro ano.
    #:
    #: E o resto da primeira pergunta que os dados recusaram ("sem teto, o que
    #: entra em cada ano?"). A resposta nao dava grafico, mas da FRASE — e a
    #: frase e forte: tirado o dinheiro, nao sobra nada segurando obra nenhuma.
    #: O cronograma do plano e artefato de orcamento, nao de engenharia.
    cedo = await db.buscar_um(
        f"""SELECT COUNT(*) FILTER (WHERE inicio_min_mes < 12) AS podem, COUNT(*) AS de
              FROM {esq}.otim_obra
             WHERE run_id = $1 AND necessaria AND NOT construida
               AND categoria_motivo <> 'Terceiro (pre-requisito)'""",
        run_id,
    ) or {}

    def fator(falta: float) -> float:
        return (capex_plano + falta) / capex_plano if capex_plano else 0.0

    #: O MESMO NUMERO PELA OUTRA REGUA: em vez de "quanto por ano", "quantos
    #: anos". Um fator de 11,7x e abstrato para quem nao lida com orcamento
    #: todo dia; "mais 64 anos ao ritmo de hoje" nao e. As duas frases dizem a
    #: mesma coisa, e ter as duas e o que faz a ideia atravessar.
    anual = total_orcado / len(janela)

    def anos_ao_ritmo(falta: float) -> float:
        return falta / anual if anual else 0.0

    return {
        "anos": anos,
        "podemComecarCedo": {
            "obras": int(cedo.get("podem") or 0),
            "de": int(cedo.get("de") or 0),
        },
        "anosDaJanela": len(janela),
        "orcamentoAnualDeHoje": anual,
        "obrasNoPlano": obras_plano,
        "capexNoPlano": capex_plano,
        "queSePaga": {
            "obras": obras_paga, "capex": falta_paga,
            "fator": fator(falta_paga), "anosAoRitmoDeHoje": anos_ao_ritmo(falta_paga),
        },
        "todas": {
            "obras": obras_toda, "capex": falta_toda,
            "fator": fator(falta_toda), "anosAoRitmoDeHoje": anos_ao_ritmo(falta_toda),
        },
    }


async def obras_do_cenario(
    run_id: str,
    escopo: str,
    ano: int | None = None,
    componente: str | None = None,
    pagina: int = 1,
    tamanho: int = 50,
) -> dict[str, Any] | None:
    """AS OBRAS DE UMA FATIA do cenário anual — a lista e a planilha.

    O que a fatia soma é o que esta lista traz, porque as duas saem da MESMA
    distribuição: `cenario_anual` chama `_obras_fora` + `_distribuir` para
    desenhar a barra, e esta função chama as duas de novo com os mesmos
    argumentos. Não há um segundo critério para divergir do primeiro.

    OS TRÊS RECORTES IMPORTAM, e é por isso que os três são obrigatórios no
    caminho até aqui:

      `escopo` — "só o que se paga" é um cenário MENOR, com cotas anuais
      próprias; as obras se acomodam de outro jeito. Era o furo antigo: o chip
      dizia R$ 514,5 Mi e a planilha vinha com R$ 1.210,8 Mi.

      `ano` — cada obra cai em um ano só. Antes o ano era rateio e não
      selecionava obra nenhuma. `None` é a JANELA INTEIRA, e não "tanto faz": é
      o que o chip de um tipo mostra (o total dos anos), então é o que o
      download dele tem de levar.

      `componente` — a fatia clicada. `None` é a barra inteira do ano.

    Cada recorte que a tela oferece existe aqui, e o que o número prometeu é o
    que o arquivo entrega.

    `None` quando a rodada não publicou orçamento: sem janela não há cenário, e
    é a mesma resposta que `cenario_anual` dá.
    """
    janela = await _janela_do_orcamento(run_id)
    if not janela:
        return None

    obras = await _obras_fora(run_id)
    if escopo == "paga":
        obras = [o for o in obras if o["positivo"]]
    onde = _distribuir(obras, janela)

    ids = [
        o["obra_id"]
        for o in obras
        if (ano is None or onde[o["obra_id"]] == ano)
        and (componente is None or o["componente"] == componente)
    ]
    return await nivel_detalhe.obras(
        run_id, obra_ids=ids, pagina=pagina, tamanho=tamanho
    )

async def candidatas_do_teto(
    run_id: str,
) -> tuple[list[teto_dom.Candidata], float, int] | None:
    """As sub-bacias fora do plano com o que custa trazer cada uma, e o orcamento.

    O CUSTO E NOMINAL (`otim_obra.capex`), e nao valor presente. O teto compara
    com o ORCAMENTO, que e nominal por ano — misturar as duas reguas daria um
    numero que parece dinheiro e nao e comparavel com o que a tela chama de
    orcamento. `pot_vp_capex_solo` existe na tabela e seria a escolha errada aqui
    exatamente por isso.

    So obras NAO CONSTRUIDAS do proprio no entram: as construidas ja foram pagas
    pelo plano atual, e cobra-las de novo inflaria o custo — o unico erro que
    deixaria o teto BAIXO demais, que e o unico que o tornaria mentiroso.

    `None` quando a rodada nao publicou orcamento — sem ele nao ha o que escalar.
    """
    meta = await db.buscar_um(
        f"""SELECT orcamento_total,
                   -- QUANTOS ANOS O ORCAMENTO COBRE. A tela precisa dele para
                   -- dizer "R$ 11,0 Mi somados os 2 anos do plano" — sem o
                   -- numero, "+10% ao ano" ao lado de um valor que e a soma da
                   -- janela e ambiguo, e a leitura errada erra por um fator
                   -- igual ao numero de anos.
                   --
                   -- Anos com orcamento ZERO nao contam: `orcamento_por_ano`
                   -- traz o ano-base com 0.0, e chama-lo de ano do plano faria a
                   -- frase prometer um ano de investimento que nao existe.
                   -- `jsonb_each` + `jsonb_typeof`, e nao `jsonb_each_text` com
                   -- cast: `''::numeric` levanta, e um unico ano com valor vazio
                   -- derrubaria a rota inteira com 500 — o teto sumiria da tela
                   -- por causa de um campo mal preenchido. Perguntar pelo TIPO
                   -- antes de converter e a unica forma de o cast nunca falhar.
                   (SELECT COUNT(*) FROM jsonb_each(
                              CASE jsonb_typeof(orcamento_por_ano::jsonb)
                                   WHEN 'object' THEN orcamento_por_ano::jsonb
                                   ELSE '{{}}'::jsonb END) AS a(ano, valor)
                     WHERE jsonb_typeof(a.valor) = 'number'
                       AND (a.valor #>> '{{}}')::numeric > 0) AS anos
              FROM {casc.esquema()}.otim_meta WHERE run_id = $1""",
        run_id,
    )
    if not meta or not meta["orcamento_total"]:
        return None

    linhas = await db.buscar(
        f"""SELECT s.sub_bacia,
                   COALESCE(s.vazao_marginal, 0) AS vazao,
                   COALESCE((SELECT SUM(o.capex)
                               FROM {casc.esquema()}.otim_obra o
                              WHERE o.run_id = s.run_id
                                AND o.no = s.sub_bacia
                                AND NOT o.construida), 0) AS capex
              FROM {casc.esquema()}.otim_subbacia s
             WHERE s.run_id = $1 AND NOT s.faturando""",
        run_id,
    )
    candidatas = [
        teto_dom.Candidata(l["sub_bacia"], float(l["capex"]), float(l["vazao"])) for l in linhas
    ]
    return candidatas, float(meta["orcamento_total"]), int(meta["anos"] or 0)


async def obras_por_componente(run_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Quantas obras CADA rodada construiu, por componente.

    Uma consulta para todas as rodadas da curva, e nao uma por rodada: sao ate
    seis (a base e cinco degraus), e seis idas ao banco para somar contagens
    seria seis vezes o custo pela mesma resposta.

    A regra de "construida" e a MESMA do resto do produto — `construida`,
    `status <> 'N/A'` e sem responsavel `terceiro%` —, e isso importa mais do que
    parece: a contagem daqui tem de fechar com `obras_construidas` do cabecalho
    da rodada. Se as duas divergissem, a tela mostraria "77 obras priorizadas" em
    cima e uma soma diferente logo abaixo, e nenhuma das duas ganharia a
    discussao.

    A coluna `obras_construidas` e gravada pelo MOTOR, cujo codigo nao vive neste
    repositorio — entao a igualdade e verificada, e nao deduzida: conferi as 12
    rodadas publicadas do banco de desenvolvimento e as tres contagens (com
    terceiro, sem terceiro, e a do motor) batem em todas. Se um dia divergirem, e
    aqui que a regra se ajusta.

    Obra de TERCEIRO nao entra, e a razao e de produto antes de ser de
    consistencia: a pergunta deste quadro e o que o dinheiro A MAIS compra, e
    obra que outro paga nao responde a ela.

    Componentes com zero obras saem da lista, nao vem com zero: "ETE: 0" ao lado
    de "ETE (modulo): 23" leria como se ETE fosse um tipo que o plano recusou,
    quando na verdade e como o motor representa ETE existente contra ETE nova.
    """
    if not run_ids:
        return {}
    linhas = await db.buscar(
        f"""SELECT o.run_id, o.componente, COUNT(*) AS construidas
              FROM {casc.esquema()}.otim_obra o
             WHERE o.run_id = ANY($1::text[])
               AND o.construida
               AND {casc.SO_OBRA}
               AND LOWER(COALESCE(o.responsavel, '')) NOT LIKE 'terceiro%'
             GROUP BY 1, 2""",
        run_ids,
    )
    por_run: dict[str, list[dict[str, Any]]] = {r: [] for r in run_ids}
    for l in linhas:
        por_run[l["run_id"]].append(
            {
                "componente": l["componente"],
                "nome": casc.NOME_DO_COMPONENTE.get(l["componente"], l["componente"]),
                "construidas": l["construidas"],
            }
        )
    # ORDEM CANONICA, de montante para jusante — a mesma de `casc.ORDEM_COMPONENTES`.
    # Ordenar por contagem faria as linhas trocarem de lugar entre um degrau e
    # outro, e a leitura "o que mudou" viraria "onde foi parar".
    posicao = {nome: i for i, nome in enumerate(casc.ORDEM_COMPONENTES)}
    for lista in por_run.values():
        lista.sort(key=lambda c: (posicao.get(c["nome"], len(posicao)), c["nome"]))
    return por_run
