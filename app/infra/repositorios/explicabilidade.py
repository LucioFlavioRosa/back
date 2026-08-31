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
            f"SELECT 1 FROM {casc.esquema()}.otim_cidade WHERE run_id = $1 AND cidade = $2",
            run_id,
            cidade,
        )
        if not existe:
            return None

    totais = await db.buscar_um(
        f"""SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE NOT s.faturando) AS nao_fatura
              FROM {casc.esquema()}.otim_subbacia s
             WHERE s.run_id = $1{filtro_cidade}""",
        *args,
    )

    presas = await db.buscar(
        f"""SELECT s.sub_bacia, s.cidade, s.sistema,
                   COALESCE(s.vazao_marginal, 0) AS vazao,
                   oc.categoria_motivo
              FROM {casc.esquema()}.otim_subbacia s
              LEFT JOIN {casc.esquema()}.otim_obra oc
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
                  FROM {casc.esquema()}.otim_obra o
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
              JOIN {casc.esquema()}.otim_subbacia s
                ON s.run_id = $1 AND s.sub_bacia = p.sub_bacia
              LEFT JOIN {casc.esquema()}.otim_obra e
                     ON e.run_id = $1 AND e.obra_id = p.elo
              LEFT JOIN {casc.esquema()}.otim_subbacia se
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
