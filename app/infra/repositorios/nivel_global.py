"""O NÍVEL 1 — a rodada inteira, antes de descer para qualquer recorte.

Os quadros do painel global, o EBITDA, a lista de cidades e o cronograma de
obras. É o que a tela mostra quando alguém abre um resultado: os números da
unidade, e as portas para os níveis de baixo.

Saiu de `niveis.py` junto com `nivel_detalhe.py` e `explicabilidade.py` — um
arquivo de 1.357 linhas com os cinco níveis, onde mudar o cronograma obrigava a
abrir o mesmo arquivo que a topologia do sistema. O vocabulário comum aos três
ficou em `cascata.py`.
"""

from typing import Any

from app.infra import db
from app.infra.repositorios import cascata as casc


async def existe(run_id: str) -> bool:
    """A rodada foi publicada?

    `otim_meta` e a mesma fonte que `/runs/{id}/meta` usa para responder 404, e
    por isso e a definicao certa de "existe" para as telas de resultado: rodada
    ainda na fila nao tem linha ali, e nao tem resultado nenhum a mostrar.
    """
    return bool(
        await db.buscar_um(f"SELECT 1 FROM {casc.esquema()}.otim_meta WHERE run_id = $1", run_id)
    )


# ---------------------------------------------------------------- nível global
async def painel(run_id: str) -> dict[str, Any]:
    anos = await db.buscar(
        f"""SELECT ano, capex, opex, receita_total AS receita,
                   CASE WHEN dentro_janela_capex THEN teto_capex END AS teto_capex
              FROM {casc.esquema()}.otim_ano WHERE run_id = $1 ORDER BY ano""",
        run_id,
    )
    curva = await db.buscar(
        f"""SELECT competencia, capex_mes, capex_acumulado
              FROM {casc.esquema()}.otim_mes WHERE run_id = $1 ORDER BY mes_indice""",
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
              FROM {casc.esquema()}.otim_obra
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
              FROM {casc.esquema()}.otim_sistema
             WHERE run_id = $1 AND COALESCE(modulos_construidos, 0) > 0""",
        run_id,
    )
    capacidade_modulos = (cap_ete or {}).get("capacidade")
    unidade_capacidade = (
        (cap_ete or {}).get("unidade") if (cap_ete or {}).get("unidades_distintas") == 1 else None
    )

    obras_ano = await casc.obras_do_plano_por_ano(run_id)
    por_ano: dict[int, list[dict[str, Any]]] = {}
    for o in obras_ano:
        # Aqui `quantidade` e a CONTAGEM DE OBRAS, e nao a quantidade fisica:
        # `obrasPorAno` sempre significou isso, e o front do :8080 o le assim
        # (`GraficoObrasPorAno`). A quantidade fisica vai em `elementosPorAno`,
        # logo abaixo, montada da MESMA consulta para os dois nao divergirem.
        por_ano.setdefault(o["ano"], []).append(
            {"componente": casc.nome_componente(o["componente"]), "quantidade": o["obras"]}
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
        "cascata": await casc.cascata_do_fluxo(run_id),
        "capexPorComponente": sorted(
            (
                {
                    "componente": casc.nome_componente(c["componente"]),
                    "capex": c["capex"],
                    "pctDoTotal": casc.pct(c["capex"], total_capex),
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
            key=lambda c: casc.ORDEM_COMPONENTES.index(c["componente"])
            if c["componente"] in casc.ORDEM_COMPONENTES
            else len(casc.ORDEM_COMPONENTES),
        ),
        "obrasPorAno": [
            {"ano": ano, "porComponente": comps} for ano, comps in sorted(por_ano.items())
        ],
        "elementosPorAno": casc.elementos_por_ano(obras_ano),
        "fimCapex": await casc.fim_capex(run_id),
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
                  FROM {casc.esquema()}.otim_ano WHERE run_id = $1 ORDER BY ano""",
            run_id,
        )
    else:
        linhas = await db.buscar(
            f"""SELECT ano, SUM(ebitda) AS ebitda,
                       CASE WHEN SUM(receita_direta + receita_indireta + efeito_base) > 0
                            THEN ROUND((SUM(ebitda) / NULLIF(SUM(receita_direta
                                 + receita_indireta + efeito_base), 0) * 100)::numeric, 1)
                       END AS margem
                  FROM {casc.esquema()}.otim_subbacia_ano
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
        "fimCapex": await casc.fim_capex(run_id),
    }


async def cidades(run_id: str) -> list[dict[str, Any]]:
    """A lista de cidades da rodada — uma linha por cidade, sem series.

    SEM `cobertura` e `metas`, e a diferenca e grande: elas vinham em duas
    consultas a mais e eram 89% do payload (39 KB de 44 KB numa unidade de 27
    cidades). Existiam para o cartao-grafico do nivel 1 desenhar o par cobertura
    x meta de cada cidade sem abrir N requisicoes — e aquela grade de cartoes
    saiu da tela.

    Continuar mandando-as seria pagar duas consultas e 39 KB, em TODA abertura de
    qualquer nivel do resultado (a arvore de escopo tambem chama esta lista),
    para dado que ninguem le. Quem precisa da serie de uma cidade e o nivel 2, e
    ele a busca no proprio payload de detalhe.
    """
    linhas = await db.buscar(
        f"""SELECT c.cidade, c.vpl, c.capex_total, c.cobertura_final_pct,
                   c.metas_atingidas, c.metas_total,
                   (SELECT COUNT(*) FROM {casc.esquema()}.otim_sistema s
                     WHERE s.run_id = c.run_id AND s.cidade = c.cidade) AS sistemas
              FROM {casc.esquema()}.otim_cidade c
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
              FROM {casc.esquema()}.otim_obra o
             WHERE o.run_id = $1 AND o.data_inicio IS NOT NULL AND {casc.SO_OBRA}
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
                "componente": casc.nome_componente(l["componente"]),
                "obras": l["obras"],
                "capex": l["capex"],
            }
        )

    return {"anos": [anos[a] for a in sorted(anos)]}
