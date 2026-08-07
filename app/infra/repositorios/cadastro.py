"""Leitura de `input.*` — o cadastro que alimenta a simulação.

O recorte é sempre a UNIDADE, e ele desce pela hierarquia:

    unidade_regional → regional_superintendencia → superintendencia_cidade
                     → cidade_sistema → sistema_topologia
                     → subbacia_operacional / cts_operacional / ete_capex

Não há coluna `unidade_id` nas tabelas de baixo: quem pertence a quem sai do
encadeamento de FKs. Por isso quase toda consulta aqui carrega o mesmo CTE de
cidades da unidade — extraí-lo em `_CIDADES_DA_UNIDADE` mantém o recorte escrito
uma vez só, e é o recorte que, errado, faria a tela mostrar cidade de outra
unidade sem nenhum sinal.

Os nomes de campo na resposta são os do FRONT (`ligU`, `ecoA`, `preco`), e não os
das colunas. A tradução mora aqui de propósito: é o único lugar onde as duas
convenções se encontram, e espalhá-la faria cada endpoint inventar a sua.
"""

from typing import Any

from app.config import config
from app.infra import db


def _i() -> str:
    return config().schema_input


#: As cidades de uma unidade — o recorte de tudo. `$1` é o `unidade_id`.
_CIDADES_DA_UNIDADE = """
    SELECT c.cidade_id, c.cidade_name, c.superintendencia_id
      FROM {i}.superintendencia_cidade c
      JOIN {i}.regional_superintendencia s USING (superintendencia_id)
     WHERE s.unidade_id = $1
"""


def _cidades_cte() -> str:
    return _CIDADES_DA_UNIDADE.format(i=_i())


# ---------------------------------------------------------------- organização
async def regionais() -> list[dict[str, Any]]:
    """As regionais, deduzidas de `unidade_regional`.

    Não há tabela de regional: a coluna `regional_id`/`regional_name` vive junto da
    unidade. `DISTINCT` em vez de uma tabela própria é o que o esquema permite.
    """
    linhas = await db.buscar(
        f"""SELECT DISTINCT regional_id AS id, regional_name AS nome
              FROM {_i()}.unidade_regional
             WHERE regional_id IS NOT NULL ORDER BY 2"""
    )
    return [dict(l) for l in linhas]


async def unidades(regional_id: str) -> list[dict[str, Any]]:
    linhas = await db.buscar(
        f"""SELECT unidade_id FROM {_i()}.unidade_regional
             WHERE regional_id = $1 ORDER BY unidade_name""",
        regional_id,
    )
    return [await unidade(l["unidade_id"]) for l in linhas]  # type: ignore[misc]


async def unidade(unidade_id: str) -> dict[str, Any] | None:
    base = await db.buscar_um(
        f"""SELECT unidade_id, unidade_name, regional_id, regional_name, wacc_medio
              FROM {_i()}.unidade_regional WHERE unidade_id = $1""",
        unidade_id,
    )
    if not base:
        return None

    # Os contadores da capa. Numa consulta só: cinco `SELECT count(*)` fariam cinco
    # idas ao banco para montar um cartão.
    c = await db.buscar_um(
        f"""WITH cid AS ({_cidades_cte()}),
                 sis AS (SELECT s.sistema_id FROM {_i()}.cidade_sistema s
                          JOIN cid ON cid.cidade_id = s.cidade_id),
                 sub AS (SELECT t.componente_sistema_id FROM {_i()}.sistema_topologia t
                          JOIN sis ON sis.sistema_id = t.sistema_id)
            SELECT (SELECT count(*) FROM cid) AS cidades,
                   (SELECT count(*) FROM sis) AS sistemas,
                   (SELECT count(*) FROM {_i()}.subbacia_operacional b
                     WHERE b.sub_bacia IN (SELECT componente_sistema_id FROM sub)) AS sub_bacias,
                   (SELECT count(*) FROM {_i()}.ete_capex e
                     WHERE e.ete_id IN (SELECT componente_sistema_id FROM sub)) AS etes,
                   (SELECT count(*) FROM {_i()}.subbacia_cts p
                     WHERE p.sub_bacia IN (SELECT componente_sistema_id FROM sub)) AS cts""",
        unidade_id,
    ) or {}

    return {
        "id": base["unidade_id"],
        "regionalId": base["regional_id"],
        "nome": base["unidade_name"],
        "waccMedio": base["wacc_medio"],
        "resumo": {
            "cidades": c.get("cidades", 0),
            "sistemas": c.get("sistemas", 0),
            "subBacias": c.get("sub_bacias", 0),
            # 5 obras por sub-bacia, 4 por CTS — a mesma conta que a tela faz.
            "obras": (c.get("sub_bacias", 0) or 0) * 5,
        },
        # PENDENTE — a completude é a mesma conta de `pendencias_do_cadastro`, e
        # tem o mesmo problema: enquanto não for calculada, a capa afirma 0%
        # (nada preenchido) para um cadastro que pode estar completo.
        "completude": 0,
        "databricksConectado": True,
    }


# ------------------------------------------------------------------- fichas
async def hierarquia(unidade_id: str) -> dict[str, Any]:
    """Grupo 01 — a árvore inteira, cinco níveis."""
    unid = await db.buscar_um(
        f"""SELECT unidade_id AS id, unidade_name AS nome, regional_id AS "regionalId",
                   regional_name AS "regionalNome", wacc_medio AS "waccMedio"
              FROM {_i()}.unidade_regional WHERE unidade_id = $1""",
        unidade_id,
    )
    supers = await db.buscar(
        f"""SELECT superintendencia_id AS id, superintendencia_name AS nome
              FROM {_i()}.regional_superintendencia WHERE unidade_id = $1
             ORDER BY 2""",
        unidade_id,
    )
    cidades = await db.buscar(
        f"""SELECT cidade_id AS id, cidade_name AS nome,
                   superintendencia_id AS "superintendenciaId"
              FROM ({_cidades_cte()}) c ORDER BY cidade_name""",
        unidade_id,
    )
    sistemas = await db.buscar(
        f"""SELECT s.sistema_id AS id, s.sistema_name AS nome, s.cidade_id AS "cidadeId"
              FROM {_i()}.cidade_sistema s
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY s.sistema_name""",
        unidade_id,
    )
    topo = await db.buscar(
        f"""SELECT t.componente_sistema_id AS id, t.sistema_id AS "sistemaId",
                   t.componente_sistema_id_jusante AS jusante
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id""",
        unidade_id,
    )
    return {
        "unidReg": unid,
        "superintendencias": supers,
        "cidades": cidades,
        "sistemas": sistemas,
        "topo": topo,
    }


async def contrato(unidade_id: str) -> dict[str, Any]:
    """Grupo 02 — cidades, metas e as faixas de paridade."""
    cidades = await db.buscar(
        f"""SELECT c.cidade_id AS id, c.cidade_name AS nome,
                   o.data_fim_concessao AS "fimConcessao",
                   o.unidade_cobertura AS cob
              FROM ({_cidades_cte()}) c
              LEFT JOIN {_i()}.cidade_operacional o USING (cidade_id)
             ORDER BY c.cidade_name""",
        unidade_id,
    )
    metas = await db.buscar(
        f"""SELECT m.cidade_id AS "cidadeId", m.ano, m.cobertura_pct AS pct
              FROM {_i()}.metas_cobertura m
              JOIN ({_cidades_cte()}) c ON c.cidade_id = m.cidade_id
             ORDER BY m.cidade_id, m.ano""",
        unidade_id,
    )
    # A tabela cobertura -> paridade. É a mesma que a tela de RESULTADO precisa
    # para explicar o degrau de paridade e hoje não recebe, porque o job publica só
    # a paridade realizada. Aqui ela existe, porque é cadastro.
    fator = await db.buscar(
        f"""SELECT f.cidade_id AS "cidadeId", f.cobertura_pct AS "coberturaPct",
                   f.paridade
              FROM {_i()}.fator_esgoto f
              JOIN ({_cidades_cte()}) c ON c.cidade_id = f.cidade_id
             ORDER BY f.cidade_id, f.cobertura_pct""",
        unidade_id,
    )
    return {"cidades": cidades, "metas": metas, "fator": fator}


#: Colunas da ficha de coleta -> nomes do front. Sub-bacia e CTS são idênticas:
#: a mesma ficha, duas tabelas. Um dicionário só evita as duas divergirem.
_COLETA = {
    "preco_por_ligacao": "preco",
    "receita_faturada_media_mensal": "fat",
    "receita_arrecadada_media_mensal": "arr",
    "tempo_arrecadacao": "tarr",
    "tempo_ramp_up": "ramp",
    "vazao_contribuicao": "vaz",
    "universo_ligacoes": "ligU",
    "ligacoes_atuais": "ligA",
    "ligacoes_novas_obras": "ligN",
    "universo_economias": "ecoU",
    "economias_atuais": "ecoA",
    "economias_novas_obras": "ecoN",
    "universo_populacao": "popU",
    "populacao_atual": "popA",
    "populacao_novas_obras": "popN",
    "potencial_crescimento": "pot",
    "universo_ligacoes_industrial": "ligUInd",
    "ligacoes_atuais_industrial": "ligAInd",
    "receita_faturada_industrial": "fatInd",
    "receita_arrecadada_industrial": "arrInd",
    "vazao_contribuicao_industrial": "vazInd",
}

#: Quais campos vêm do Databricks (travados) e quais a Regional preenche. O front
#: usa a separação para decidir o que é editável e o que exige override.
_DO_DATABRICKS = {"fat", "arr", "ligU", "ligA", "ligN", "ecoU", "ecoA", "ecoN"}


def _ficha_coleta(linha: dict[str, Any], chave: str) -> dict[str, Any]:
    db_bloco = {v: linha[k] for k, v in _COLETA.items() if v in _DO_DATABRICKS}
    params = {v: linha[k] for k, v in _COLETA.items() if v not in _DO_DATABRICKS}
    return {"id": linha[chave], "db": db_bloco, "params": params}


async def sub_bacias(unidade_id: str) -> dict[str, Any]:
    arvore = await db.buscar(
        f"""SELECT t.componente_sistema_id AS "subId", s.sistema_id AS "sistemaId",
                   s.sistema_name AS "sistemaNome", c.cidade_id AS "cidadeId",
                   c.cidade_name AS "cidadeNome"
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY c.cidade_name, s.sistema_name, t.componente_sistema_id""",
        unidade_id,
    )
    fichas = await db.buscar(
        f"""SELECT b.* FROM {_i()}.subbacia_operacional b
             WHERE b.sub_bacia IN (
                   SELECT t.componente_sistema_id
                     FROM {_i()}.sistema_topologia t
                     JOIN {_i()}.cidade_sistema s USING (sistema_id)
                     JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id)
             ORDER BY b.sub_bacia""",
        unidade_id,
    )
    return {"arvore": arvore, "subs": [_ficha_coleta(f, "sub_bacia") for f in fichas]}


async def etes(unidade_id: str) -> dict[str, Any]:
    """As ETEs da unidade.

    O recorte passa por `sistema_topologia`: a ETE é um componente do sistema como
    a sub-bacia, e o que a distingue é ter ficha em `ete_capex` — é assim que o
    motor a identifica (`otimizador_capex_v62.py:1111`). Esta consulta já trouxe
    TODAS as ETEs do banco, porque eu tinha concluído que o esquema não ligava a
    ETE à unidade. Ligava.
    """
    linhas = await db.buscar(
        f"""SELECT e.ete_id AS id, capacidade_por_modulo AS "capMod",
                   capex_por_modulo AS "capexMod", opex_por_modulo AS "opexMod",
                   tempo_predecessoras AS "tempoPred", tempo_de_execucao AS "tempoExec",
                   capacidade_nominal_atual AS "capAtual",
                   vazao_de_operacao_atual AS "vazaoAtual",
                   capacidade_ociosa AS ociosa, obra_obrigatoria_ano AS "obrigAno",
                   obra_proibida_ate AS "proibidaAte", nova, capex_terreno AS terreno,
                   modulos, wacc
              FROM {_i()}.ete_capex e
              JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = e.ete_id
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY e.ete_id""",
        unidade_id,
    )
    return {"etes": [dict(l) for l in linhas]}


async def cts(unidade_id: str) -> dict[str, Any]:
    """CTS e o pareamento 1:1 com a sub-bacia.

    `pares` vem separado porque uma CTS sem par é estado inválido que a tela
    precisa mostrar — sem a lista, ela não teria como saber que a CTS ficou órfã.
    """
    pares = await db.buscar(
        f"""SELECT p.sub_bacia AS sub, p.cts
              FROM {_i()}.subbacia_cts p
              JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = p.sub_bacia
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY p.sub_bacia""",
        unidade_id,
    )
    # As fichas saem dos pares, e nao de um SELECT solto: a CTS chega a unidade
    # pela sub-bacia com que e pareada 1:1. Uma CTS sem par nao pertence a unidade
    # nenhuma — e o estado invalido que `pares` existe para a tela mostrar.
    fichas = await db.buscar(
        f"""SELECT o.* FROM {_i()}.cts_operacional o
              JOIN {_i()}.subbacia_cts p ON p.cts = o.cts
              JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = p.sub_bacia
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY o.cts""",
        unidade_id,
    )
    return {"pares": pares, "ctss": [_ficha_coleta(f, "cts") for f in fichas]}
