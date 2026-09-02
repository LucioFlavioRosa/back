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

from typing import Any

from app.infra import db
from app.infra.repositorios import cascata as casc
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
    onde = ["o.run_id = $1", "o.necessaria", "NOT o.construida"]
    args: list = [run_id]
    if cidade:
        args.append(cidade)
        onde.append(f"o.cidade = ${len(args)}")
    if sistema:
        args.append(sistema)
        onde.append(f"COALESCE(NULLIF(o.sistema, ''), sn.sistema) = ${len(args)}")
    filtro = " AND ".join(onde)

    if cidade or sistema:
        col, val = ("cidade", cidade) if cidade else ("sistema", sistema)
        existe = await db.buscar_um(
            f"SELECT 1 FROM {esq}.otim_subbacia WHERE run_id = $1 AND {col} = $2 LIMIT 1",
            run_id,
            val,
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
             WHERE {" AND ".join(x for x in onde if x not in ("o.necessaria", "NOT o.construida"))}""",
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

    ## A distribuição segue o PERFIL do plano, e não é escalonamento novo

    O que falta é rateado entre os anos na proporção do orçamento que a rodada já
    tem (`otim_meta.orcamento_por_ano`). Não é uma otimização — é a mesma forma,
    numa escala maior, e a tela diz isso.

    Distribuir pelo `inicio_min_mes` de cada obra seria mais sofisticado e MENOS
    verdadeiro: 91% delas podem começar no primeiro ano, então o resultado seria
    a mesma torre que motivou a troca de pergunta.

    ## `noPlano` sai do ano em que a obra COMEÇA

    É a régua do gráfico de obras por ano, e mantê-la é o que deixa os dois
    gráficos comparáveis lado a lado.
    """
    esq = casc.esquema()
    meta = await db.buscar_um(
        f"""SELECT orcamento_por_ano::jsonb AS orc FROM {esq}.otim_meta WHERE run_id = $1""",
        run_id,
    )
    if not meta or not meta["orc"]:
        return None

    import json

    orc = meta["orc"] if isinstance(meta["orc"], dict) else json.loads(meta["orc"])
    #: ANO COM ORÇAMENTO ZERO NÃO É ANO DA JANELA. O ano-base vem com 0.0, e
    #: incluí-lo daria uma barra que promete investimento onde não há nenhum.
    janela = sorted(
        (int(a), float(v))
        for a, v in orc.items()
        if isinstance(v, (int, float)) and float(v) > 0
    )
    if not janela:
        return None
    total_orcado = sum(v for _, v in janela)

    linhas = await db.buscar(
        f"""WITH o AS (
                -- MESMO RECORTE DA EXPLICABILIDADE, e nao "todas as necessarias":
                -- obra de terceiro ACONTECE e outro paga, entao ela nao entra em
                -- "quanto custaria". Sao 560 obras de CAPEX zero — o dinheiro nao
                -- mudava, mas a CONTAGEM mudava, e duas telas da mesma rodada
                -- diriam numeros diferentes para a mesma pergunta.
                SELECT obra_id, no, sistema, capex, construida, elo_que_trava, mes_inicio
                  FROM {esq}.otim_obra
                 WHERE run_id = $1 AND necessaria
                   AND (construida OR categoria_motivo <> 'Terceiro (pre-requisito)')
            ),
            todas AS (
                SELECT obra_id, no, elo_que_trava FROM {esq}.otim_obra WHERE run_id = $1
            ),
            sb AS (
                SELECT sub_bacia, sistema, pot_saldo_rateado
                  FROM {esq}.otim_subbacia
                 WHERE run_id = $1 AND NOT faturando
            ),
            -- QUEM A OBRA SERVE: o próprio nó, todo nó que ela destrava, e — na
            -- ponta da cadeia — o sistema inteiro. A ETE não tem nó próprio: ela
            -- É o fim do caminho, e sem esta terceira via os 844 módulos ficariam
            -- sem classificação, que é R$ 947 Mi somindo da conta.
            serve AS (
                SELECT f.obra_id, f.no AS sub_bacia FROM o f
                 WHERE NOT f.construida AND f.no IS NOT NULL AND f.no <> ''
                UNION
                SELECT f.obra_id, t.no FROM o f
                  JOIN todas t ON t.elo_que_trava = f.obra_id
                                AND t.no IS NOT NULL AND t.no <> ''
                 WHERE NOT f.construida
                UNION
                SELECT f.obra_id, s2.sub_bacia FROM o f
                  JOIN sb s2 ON s2.sistema = f.sistema
                 WHERE NOT f.construida AND (f.no IS NULL OR f.no = '')
            )
            SELECT
              (SELECT COALESCE(SUM(capex), 0) FROM o WHERE construida) AS capex_plano,
              (SELECT COUNT(*) FROM o WHERE construida)                AS obras_plano,
              f.positivo, COUNT(*) AS obras, COALESCE(SUM(f.capex), 0) AS capex
              FROM (
                SELECT f.obra_id, f.capex,
                       COALESCE(BOOL_OR(sb.pot_saldo_rateado > 0), FALSE) AS positivo
                  FROM o f
                  LEFT JOIN serve s ON s.obra_id = f.obra_id
                  LEFT JOIN sb ON sb.sub_bacia = s.sub_bacia
                 WHERE NOT f.construida
                 GROUP BY f.obra_id, f.capex
              ) f
             GROUP BY f.positivo""",
        run_id,
    )

    capex_plano = float(linhas[0]["capex_plano"]) if linhas else 0.0
    obras_plano = int(linhas[0]["obras_plano"]) if linhas else 0
    falta_paga = sum(float(l["capex"]) for l in linhas if l["positivo"])
    falta_toda = sum(float(l["capex"]) for l in linhas)
    obras_paga = sum(int(l["obras"]) for l in linhas if l["positivo"])
    obras_toda = sum(int(l["obras"]) for l in linhas)

    por_ano = await db.buscar(
        f"""SELECT FLOOR(mes_inicio / 12)::int AS ano_rel,
                   COUNT(*) AS obras, COALESCE(SUM(capex), 0) AS capex
              FROM {esq}.otim_obra
             WHERE run_id = $1 AND necessaria AND construida AND mes_inicio IS NOT NULL
             GROUP BY 1""",
        run_id,
    )
    ano0 = janela[0][0]
    plano_do_ano = {ano0 + int(l["ano_rel"]) - 1: l for l in por_ano}

    anos = []
    for ano, orcado in janela:
        peso = orcado / total_orcado
        no_plano = plano_do_ano.get(ano)
        anos.append(
            {
                "ano": ano,
                "orcado": orcado,
                "noPlano": float(no_plano["capex"]) if no_plano else 0.0,
                "obrasNoPlano": int(no_plano["obras"]) if no_plano else 0,
                # O RATEIO É PELO PESO DO ANO no orçamento atual: mesma forma,
                # escala maior. Ver o docstring para por que não é por obra.
                "faltaQueSePaga": falta_paga * peso,
                "faltaTodas": falta_toda * peso,
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
