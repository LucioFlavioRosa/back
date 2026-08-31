"""O VOCABULÁRIO COMPARTILHADO DA CASCATA — o que os cinco níveis têm em comum.

Estava tudo num `niveis.py` de 1.357 linhas, com os cinco níveis, a
explicabilidade, o cronograma e os insumos da sensibilidade no mesmo arquivo. O
que ficou aqui é o que mais de um deles usa: a tradução de componente, as contas
de porcentagem que o contrato exige (`null` é "não existe", nunca 0), a cascata
do fluxo de escoamento e os pedaços de SQL repetidos.

OS NOMES PERDERAM O `_` AO ATRAVESSAR A FRONTEIRA. Enquanto tudo morava num
arquivo só, `_pct` e `_situacao` eram privados de verdade. Agora eles são a
interface deste módulo para os outros três, e mantê-los com underscore diria uma
coisa (implementação) enquanto o código faz outra (importar de fora). O que
continua privado aqui continua com `_`.
"""


from typing import Any

from app.config import config
from app.infra import db

# `_escala_pct` e `_realizado_pct` NÃO estão aqui, e o underscore diz por quê:
# eles são usados só dentro de `meta_de_cobertura`, neste arquivo. Perderam o
# underscore na divisão junto com os que de fato atravessaram a fronteira — um
# renome em lote —, e exportá-los seria prometer estabilidade a duas contas que
# ninguém de fora chama.
__all__ = [
    "NOME_DO_COMPONENTE",
    "ORDEM_COMPONENTES",
    "ORDENS",
    "SITUACAO_SQL",
    "SO_OBRA",
    "TAMANHO_MAX",
    "cascata_do_fluxo",
    "componente",
    "elementos_por_ano",
    "esquema",
    "fim_capex",
    "meta_de_cobertura",
    "nome_componente",
    "obras_do_plano_por_ano",
    "pct",
    "situacao",
]

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


def esquema() -> str:
    return config().schema_resultado


def pct(parte: float | None, total: float | None) -> float | None:
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


def meta_de_cobertura(m: dict[str, Any]) -> dict[str, Any]:
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


def situacao(linha: dict[str, Any]) -> str:
    """`construida | nao-construida | terceiro | sem-obra`.

    A ordem dos testes importa: uma obra de terceiro que já existe é `terceiro`, e
    não `construida` — quem paga muda a leitura do plano.
    """
    if linha.get("obra_id") is None:
        return "sem-obra"
    if (linha.get("responsavel") or "").lower().startswith("terceiro"):
        return "terceiro"
    return "construida" if linha.get("construida") else "nao-construida"


async def fim_capex(run_id: str) -> int | None:
    """Último ano da janela de CAPEX — vira linha de referência em vários gráficos."""
    linha = await db.buscar_um(
        f"SELECT ano_base, anos_capex FROM {esquema()}.otim_meta WHERE run_id = $1", run_id
    )
    if not linha or not linha.get("ano_base") or not linha.get("anos_capex"):
        return None
    return int(linha["ano_base"]) + int(linha["anos_capex"]) - 1


async def cascata_do_fluxo(run_id: str, cidade: str | None = None, sub_bacia: str | None = None) -> list[dict[str, Any]]:
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
              FROM {esquema()}.otim_subbacia
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


async def obras_do_plano_por_ano(
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
              FROM {esquema()}.otim_obra o
              LEFT JOIN {esquema()}.otim_subbacia s
                     ON s.run_id = o.run_id AND s.sub_bacia = o.no
             WHERE {' AND '.join(onde)}
             GROUP BY 1, 2
             ORDER BY 1, 2""",
        *args,
    )


def elementos_por_ano(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

# ------------------------------------------- nível global: o plano de execução
#
# A mesma `otim_obra` que o nível 5 lê uma linha por vez, agora vista do topo: a
# lista do plano e o cronograma por ano.

#: A linha de ETE com `status = 'N/A'` NÃO É OBRA. Ela existe para a ETE ter ficha
#: (capex 0, sem quantidade, sem data), e o que de fato se constrói é o módulo
#: (`tipo = 'ete_mod'`). Deixá-la na lista poria 474 linhas de capex zero no meio
#: do plano de execução.
SO_OBRA = "o.status <> 'N/A'"

#: `situacao()` (Python) e este CASE dizem a MESMA coisa, na mesma ordem — obra de
#: terceiro que já existe é `terceiro`, e não `construida`, porque quem paga muda a
#: leitura do plano. A duplicação é consciente: a lista é paginada, e filtrar por
#: situação depois de trazer a página daria um `total` que não bate com o filtro.
SITUACAO_SQL = (
    "CASE WHEN LOWER(COALESCE(o.responsavel, '')) LIKE 'terceiro%' THEN 'terceiro'"
    "     WHEN o.construida THEN 'construida'"
    "     ELSE 'nao-construida' END"
)

#: Whitelist — o `ordenar` da querystring escolhe uma ENTRADA, nunca compõe SQL.
ORDENS = {
    "inicio": "o.data_inicio NULLS LAST, o.obra_id",
    "capex": "o.capex DESC NULLS LAST, o.obra_id",
    "cidade": "o.cidade, o.data_inicio NULLS LAST, o.obra_id",
}

#: Teto de página. O front pede 200 ao abrir um ano do cronograma. Pela rota o
#: valor já chega validado (`Query(le=500)`, que recusa o excesso com 422 em vez
#: de fingir que atendeu); este `min` é o teto para quem chamar a função direto —
#: um script de conferência, um teste —, onde nada mais impede pedir as 8 mil
#: obras da rodada de uma vez.
TAMANHO_MAX = 500

def componente(o: dict[str, Any]) -> dict[str, Any]:
    ano = None
    if o.get("data_inicio"):
        ano = int(str(o["data_inicio"])[:4])
    return {
        "nome": nome_componente(o["componente"]),
        "obraId": o["obra_id"],
        "situacao": situacao(o),
        "capex": o["capex"],
        "precoUnitario": o["preco_unitario"],
        "quantidade": o["quantidade"],
        "unidade": o["unidade"],
        "anoInicio": ano,
        "prazoMeses": o["prazo_meses"],
    }
