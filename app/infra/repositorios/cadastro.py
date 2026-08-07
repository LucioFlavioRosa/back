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

import hashlib
import json
from typing import Any

from app.config import config
from app.infra import db
from app.infra.repositorios import pendencias


def _i() -> str:
    return config().schema_input


#: As cidades de uma unidade — o recorte de tudo. `$1` é o `unidade_id`.
_CIDADES_DA_UNIDADE = """
    SELECT c.cidade_id, c.cidade_name, c.superintendencia_id
      FROM {i}.superintendencia_cidade c
      JOIN {i}.regional_superintendencia s USING (superintendencia_id)
     WHERE s.unidade_id = $1
"""


def pt_br(v: Any) -> str:
    """Número do banco -> string pt-BR, que é como o contrato manda ele viajar.

    Sem isto o `GET` devolvia `str(2497.7)` = `"2497.7"`, e a escrita — que exige
    pt-BR estrito — não reconhecia o próprio formato que acabara de emitir. O
    efeito era o pior possível: **ler uma ficha e salvá-la de volta dava 500**, que
    é a operação mais comum do cadastro. Um teste de uso real pegou; nenhum dos
    smokes pegava, porque todos montavam o corpo à mão em vez de reenviar o que
    o `GET` devolveu.

    `1234.5` -> `"1.234,5"`. Inteiro não ganha casa decimal: `244.0` -> `"244"`,
    senão a tela mostraria "244,0" numa quantidade de ligações.
    """
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if float(v).is_integer():
        return f"{int(v):,}".replace(",", ".")
    return f"{v:,.4f}".rstrip("0").rstrip(".").replace(",", "\x00").replace(".", ",").replace("\x00", ".")


#: Campos que sao ANO ou CODIGO, e nao quantidade: vao sem separador de milhar.
#: `pt_br(2049)` daria "2.049", e ano com ponto e erro de leitura na tela — alem de
#: `obra_obrigatoria_ano` ser codigo (0 = nao obrigatoria, -1 = qualquer ano).
SEM_SEPARADOR = {"fim", "ano", "anoObrig", "proibAte"}


def pt_br_ano(v: Any) -> str:
    """`2049` -> `"2049"`. Numero que nao e quantidade nao ganha separador."""
    if v is None:
        return ""
    if isinstance(v, (int, float)) and float(v).is_integer():
        return str(int(v))
    return pt_br(v)


def versao(ficha: Any) -> str:
    """A versão de uma ficha é o HASH do conteúdo que o `GET` devolveu.

    Não é um contador: é a resposta a "isto ainda está como quando você leu?".

    A escolha do hash sobre uma coluna `versao` tem três motivos, e o primeiro é o
    que decide: **não precisa de migração**. As tabelas de cadastro vivem no
    repositório do otimizador, e acrescentar coluna em cinco delas é uma migração
    coordenada para resolver um problema que o conteúdo já responde.

    Os outros dois: uma ficha pode nascer de mais de uma tabela (a de cidade sai de
    `cidade_operacional` + `metas_cobertura` + `fator_esgoto`, e combinar três
    contadores é pior que hashear o resultado); e duas pessoas fazendo a MESMA
    edição não geram conflito, porque o conteúdo final é o mesmo — com contador,
    a segunda levaria um 409 que não protege nada.

    `sort_keys` porque a ordem das chaves de um JSON não significa nada: sem ele, a
    mesma ficha lida duas vezes poderia dar versões diferentes.
    """
    bruto = json.dumps(ficha, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


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
        "completude": (await pendencias.contar(unidade_id))["completude"],
        "databricksConectado": True,
    }


# ------------------------------------------------------------------- fichas
async def hierarquia(unidade_id: str) -> dict[str, Any]:
    """Grupo 01 — a arvore inteira, cinco niveis.

    Os nomes sao os do front (`cadastro/domain/hierarquia.ts`) e sao CURTOS:
    `rid`/`uid`/`supId`/`cidId`/`sis`/`jus`. Eu tinha usado `regionalId`,
    `superintendenciaId`, `cidadeId`, `sistemaId`, `jusante` — e a tela do Grupo 01
    renderizava em branco, porque cada campo que ela procura vinha `undefined`.

    Tudo string: o front trata como texto e chama `.trim()`.
    """
    u = await db.buscar_um(
        f"""SELECT regional_id, regional_name, unidade_id, unidade_name, wacc_medio
              FROM {_i()}.unidade_regional WHERE unidade_id = $1""",
        unidade_id,
    ) or {}
    unid = {
        "rid": u.get("regional_id") or "",
        "rnome": u.get("regional_name") or "",
        "uid": u.get("unidade_id") or "",
        "unome": u.get("unidade_name") or "",
        "waccMedio": pt_br(u.get("wacc_medio")),
    }
    supers = await db.buscar(
        f"""SELECT superintendencia_id AS id, superintendencia_name AS nome
              FROM {_i()}.regional_superintendencia WHERE unidade_id = $1
             ORDER BY 2""",
        unidade_id,
    )
    cidades = await db.buscar(
        f"""SELECT cidade_id AS id, cidade_name AS nome,
                   superintendencia_id AS "supId"
              FROM ({_cidades_cte()}) c ORDER BY cidade_name""",
        unidade_id,
    )
    sistemas = await db.buscar(
        f"""SELECT s.sistema_id AS id, s.sistema_name AS nome, s.cidade_id AS "cidId"
              FROM {_i()}.cidade_sistema s
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY s.sistema_name""",
        unidade_id,
    )
    topo = await db.buscar(
        f"""SELECT t.sistema_id AS sis, t.componente_sistema_id AS id,
                   t.componente_sistema_nome AS nome,
                   t.componente_sistema_id_jusante AS jus
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY t.sistema_id, t.componente_sistema_id""",
        unidade_id,
    )

    def txt(linhas):
        return [{k: ("" if v is None else str(v)) for k, v in l.items()} for l in linhas]

    return {
        "unidReg": unid,
        "superintendencias": txt(supers),
        "cidades": txt(cidades),
        "sistemas": txt(sistemas),
        "topo": txt(topo),
    }


async def contrato(unidade_id: str) -> dict[str, Any]:
    """Grupo 02 — cidades, metas e as faixas de paridade.

    Os nomes aqui são CURTOS (`fim`, `cob`, `cid`, `par`) porque são os do front
    (`Cidade`, `Meta`, `Fator` em `cadastro/domain/contrato.ts`). Eu tinha usado
    `fimConcessao`, `cidadeId`, `coberturaPct` e `paridade`, e o efeito foi um
    "Unexpected Application Error" no navegador ao abrir o cadastro: a contagem de
    pendências chama `c.fim.trim()` direto, sem guarda, e `undefined.trim()`
    derruba a tela inteira.

    E TUDO vai como string, inclusive ano e percentual — o front trata todo campo
    de ficha como texto editável e chama `.trim()` neles. Número cru quebraria do
    mesmo jeito.
    """
    cidades = await db.buscar(
        f"""SELECT c.cidade_id AS id, c.cidade_name AS nome,
                   o.data_fim_concessao AS fim,
                   o.unidade_cobertura AS cob
              FROM ({_cidades_cte()}) c
              LEFT JOIN {_i()}.cidade_operacional o USING (cidade_id)
             ORDER BY c.cidade_name""",
        unidade_id,
    )
    metas = await db.buscar(
        f"""SELECT m.cidade_id AS cid, m.ano, m.cobertura_pct AS pct
              FROM {_i()}.metas_cobertura m
              JOIN ({_cidades_cte()}) c ON c.cidade_id = m.cidade_id
             ORDER BY m.cidade_id, m.ano""",
        unidade_id,
    )
    # A tabela cobertura -> paridade. É a mesma que a tela de RESULTADO precisa
    # para explicar o degrau de paridade e hoje não recebe, porque o job publica só
    # a paridade realizada. Aqui ela existe, porque é cadastro.
    fator = await db.buscar(
        f"""SELECT f.cidade_id AS cid, f.cobertura_pct AS cob, f.paridade AS par
              FROM {_i()}.fator_esgoto f
              JOIN ({_cidades_cte()}) c ON c.cidade_id = f.cidade_id
             ORDER BY f.cidade_id, f.cobertura_pct""",
        unidade_id,
    )

    def _txt(linha: dict[str, Any], exceto: tuple[str, ...]) -> dict[str, Any]:
        return {
            k: v
            if k in exceto
            else (pt_br_ano if k in SEM_SEPARADOR else pt_br)(v)
            for k, v in linha.items()
        }

    cidades = [_txt(c, ("id", "nome")) for c in cidades]
    metas = [_txt(m, ("cid",)) for m in metas]
    fator = [_txt(f, ("cid",)) for f in fator]

    # A ficha de cidade sai de TRÊS tabelas — a versão cobre as três, senão editar
    # uma meta não invalidaria a leitura de quem tem a cidade aberta.
    for c in cidades:
        c["versao"] = versao(
            {
                "cidade": {k: v for k, v in c.items() if k != "id"},
                "metas": [m for m in metas if m["cid"] == c["id"]],
                "fator": [f for f in fator if f["cid"] == c["id"]],
            }
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

#: Quais campos vêm do Databricks (travados, corrigíveis só por override) e quais
#: a Regional preenche. A divisão é a do `DEPLOY.md` §3, e eu a tinha errado: o
#: RECORTE INDUSTRIAL (`ligUInd`, `ligAInd`, `fatInd`, `arrInd`) é medida do
#: Databricks como as do topo, e estava caindo em `params` — a tela mostraria como
#: campo a preencher o que é dado travado, e a Regional digitaria por cima sem
#: gerar trilha de override.
#:
#: `vazInd` fica em `params` de propósito, e não é inconsistência: `vazao_contribuicao`
#: é o total do Databricks e a parcela industrial dentro dele é estimativa da
#: Regional. Está assim no `DEPLOY.md`.
_DO_DATABRICKS = {
    "fat",
    "arr",
    "ligU",
    "ligA",
    "ligN",
    "ecoU",
    "ecoA",
    "ecoN",
    "ligUInd",
    "ligAInd",
    "fatInd",
    "arrInd",
}

#: O que a ficha de coleta DEVE trazer em cada bloco. É o contrato do front
#: (`SubBaciaDb` / `SubBaciaParams`), e é o que torna o PUT uma substituição de
#: ficha inteira em vez de um patch — ver `cadastro_escrita._exigir_ficha_inteira`.
CAMPOS_DB = sorted(_DO_DATABRICKS)
CAMPOS_PARAMS = ["preco", "tarr", "ramp", "vaz", "vazInd", "pot", "popU", "popA"]

#: `popN` (`populacao_novas_obras`) existe na tabela e NÃO é modelado pelo front:
#: não está em `SubBaciaDb` nem em `SubBaciaParams`. Por isso a escrita nunca o
#: toca — zerá-lo em nome de "ficha inteira" apagaria uma coluna que o cliente
#: nem sabe que existe.
NAO_MODELADOS = {"popN"}


def _ficha_coleta(linha: dict[str, Any], chave: str) -> dict[str, Any]:
    # Todo número sai em pt-BR — o mesmo formato que o `PUT` exige de volta. Ver
    # `pt_br`: a ficha lida tem de poder ser reenviada sem tradução no meio.
    db_bloco = {v: pt_br(linha[k]) for k, v in _COLETA.items() if v in _DO_DATABRICKS}
    params = {v: pt_br(linha[k]) for k, v in _COLETA.items() if v not in _DO_DATABRICKS}
    return {"id": linha[chave], "db": db_bloco, "params": params}


async def sub_bacias(unidade_id: str) -> dict[str, Any]:
    """Grupo 03 — a árvore de navegação e as fichas.

    Duas formas que NÃO são detalhe de gosto, e que eu tinha errado nas duas:

      - `subs` é um MAPA por id, não uma lista. A tela faz `subs[subId]` para abrir
        a ficha selecionada no rail; com lista, `Object.keys` devolve `"0"`, `"1"`,
        e o salvamento passa a chamar `PUT /sub-bacias/0`.
      - `arvore` é ANINHADA — superintendência → cidade → sistema → ids —, e não uma
        lista plana. É ela que desenha o rail; plana, o rail não tem o que expandir.

    A árvore traz só ramos COM sub-bacia: um sistema vazio no rail é um caminho que
    não leva a lugar nenhum.
    """
    linhas = await db.buscar(
        f"""SELECT t.componente_sistema_id AS sub_id,
                   t.componente_sistema_nome AS sub_nome,
                   t.componente_sistema_id_jusante AS jusante,
                   s.sistema_id, s.sistema_name,
                   c.cidade_id, c.cidade_name, c.superintendencia_id,
                   r.superintendencia_name
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
              JOIN {_i()}.regional_superintendencia r
                   USING (superintendencia_id)
             ORDER BY r.superintendencia_name, c.cidade_name, s.sistema_name,
                      t.componente_sistema_id""",
        unidade_id,
    )
    fichas = {
        f["sub_bacia"]: f
        for f in await db.buscar(
            f"""SELECT b.* FROM {_i()}.subbacia_operacional b
                 WHERE b.sub_bacia IN (
                       SELECT t.componente_sistema_id
                         FROM {_i()}.sistema_topologia t
                         JOIN {_i()}.cidade_sistema s USING (sistema_id)
                         JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id)""",
            unidade_id,
        )
    }
    obras = await _obras_por_ficha("componentes_subbacias_capex", "sub_bacia", list(fichas))

    subs: dict[str, Any] = {}
    for l in linhas:
        sid = l["sub_id"]
        if sid not in fichas:
            continue  # linha da topologia sem ficha: e ETE ou nó sem cadastro
        ficha = {
            **_ficha_coleta(fichas[sid], "sub_bacia"),
            "nome": l["sub_nome"] or sid,
            "sisId": l["sistema_id"],
            "sistema": l["sistema_name"] or l["sistema_id"],
            "jusante": l["jusante"] or "",
            "obrasOverride": obras.get(sid, {}),
        }
        # A versão é calculada sobre o que MUDA — `db`, `params` e as obras. Nome,
        # sistema e jusante vêm da topologia e não são editáveis por esta ficha;
        # incluí-los faria uma mudança na hierarquia invalidar edições em curso
        # sem que ninguém tivesse tocado no dado.
        ficha["versao"] = versao(
            {k: ficha[k] for k in ("db", "params", "obrasOverride")}
        )
        subs[sid] = ficha

    return {"arvore": _arvore(linhas, com_ficha=set(subs)), "subs": subs}


def _arvore(linhas: list[dict[str, Any]], com_ficha: set[str]) -> list[dict[str, Any]]:
    """Sup → cidade → sistema → subIds, só com os ramos que levam a alguma ficha."""
    sups: dict[str, dict[str, Any]] = {}
    for l in linhas:
        if l["sub_id"] not in com_ficha:
            continue
        sup = sups.setdefault(
            l["superintendencia_id"],
            {
                "id": l["superintendencia_id"],
                "nome": l["superintendencia_name"] or l["superintendencia_id"],
                "_cid": {},
            },
        )
        cid = sup["_cid"].setdefault(
            l["cidade_id"],
            {"id": l["cidade_id"], "nome": l["cidade_name"] or l["cidade_id"], "_sis": {}},
        )
        sis = cid["_sis"].setdefault(
            l["sistema_id"],
            {"id": l["sistema_id"], "nome": l["sistema_name"] or l["sistema_id"], "subIds": []},
        )
        sis["subIds"].append(l["sub_id"])

    return [
        {
            "id": s["id"],
            "nome": s["nome"],
            "cidades": [
                {"id": c["id"], "nome": c["nome"], "sistemas": list(c["_sis"].values())}
                for c in s["_cid"].values()
            ],
        }
        for s in sups.values()
    ]


#: Colunas de obra -> nomes do front. Inverso do `_OBRA` da escrita.
_OBRA_LEITURA = {
    "quantidade": "qtd",
    "unidade": "un",
    "preco_unitario": "preco",
    "opex": "opex",
    "tempo_predecessoras": "tPred",
    "tempo_execucao": "dur",
    "obra_obrigatoria_ano": "anoObrig",
    "obra_proibida_ate": "proibAte",
    "wacc": "wacc",
}

#: A ordem canonica dos componentes — e ela que da o INDICE do override, porque o
#: front indexa por posicao na obra-base. Gravar fora de ordem faria o override da
#: rede coletora voltar como se fosse da ligacao.
_ORDEM_OBRAS = [
    "Ligacao de esgoto",
    "Rede coletora",
    "Coletor tronco",
    "Estacao elevatoria (EEE)",
    "Linha de recalque (LR)",
]


async def _obras_por_ficha(
    tabela: str, chave: str, ids: list[str]
) -> dict[str, dict[str, Any]]:
    """`{ficha: {indice: {campo: valor}}}` — o `obrasOverride` como o front espera.

    Devolve TODOS os campos da linha gravada, e nao so os que diferem da base: o
    front trata `obrasOverride` como sobreposicao, entao mandar demais e inofensivo
    e mandar de menos perderia o que a Regional digitou.
    """
    if not ids:
        return {}
    linhas = await db.buscar(
        f"SELECT * FROM {_i()}.{tabela} WHERE {chave} = ANY($1::text[])", ids
    )
    pos = {nome: str(i) for i, nome in enumerate(_ORDEM_OBRAS)}
    out: dict[str, dict[str, Any]] = {}
    for l in linhas:
        indice = pos.get(l["componente"])
        if indice is None:
            continue  # componente fora da base: o front nao teria onde encaixar
        # pt-BR, e não `str(...)`: é o formato que a escrita aceita de volta.
        campos = {
            destino: (pt_br_ano if destino in SEM_SEPARADOR else pt_br)(l[col])
            for col, destino in _OBRA_LEITURA.items()
        }
        out.setdefault(l[chave], {})[indice] = campos
    return out


async def etes(unidade_id: str) -> dict[str, Any]:
    """As ETEs da unidade.

    Os nomes sao os do tipo `Ete` do front (`cadastro/domain/ete.ts`): `tExec`,
    `capNom`, `vazOp` — e nao `tempoExec`, `capAtual`, `vazaoAtual`, que foi o que
    eu tinha escrito. E TUDO vai como string, `""` no lugar de NULL: o tipo declara
    todo campo como `string`, e um `null` chegando ali derruba a tela inteira com
    `Cannot read properties of null (reading 'trim')` — nao so o campo.

    `sub` e `cidId` situam a ETE na arvore: ela e um componente de
    `sistema_topologia` como a sub-bacia, e a cidade vem do sistema dela.

    O recorte passa por `sistema_topologia`: e por ela que a ETE chega a uma
    unidade — o motor a identifica assim (`otimizador_capex_v62.py:1111`).
    """
    linhas = await db.buscar(
        f"""SELECT e.ete_id, t.componente_sistema_id AS sub, s.cidade_id,
                   e.capacidade_por_modulo, e.capex_por_modulo, e.opex_por_modulo,
                   e.tempo_de_execucao, e.capacidade_nominal_atual,
                   e.vazao_de_operacao_atual, e.capex_terreno, e.modulos, e.wacc,
                   e.nova
              FROM {_i()}.ete_capex e
              JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = e.ete_id
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY e.ete_id""",
        unidade_id,
    )

    #: coluna -> nome do front. `nova` e texto ("Sim"/"Nao"), nao numero.
    MAPA = {
        "capacidade_por_modulo": "capMod",
        "capex_por_modulo": "capexMod",
        "opex_por_modulo": "opexMod",
        "tempo_de_execucao": "tExec",
        "capacidade_nominal_atual": "capNom",
        "vazao_de_operacao_atual": "vazOp",
        "capex_terreno": "terreno",
        "modulos": "modulos",
        "wacc": "wacc",
    }

    etes = []
    for l in linhas:
        e = {
            "id": l["ete_id"],
            "sub": l["sub"] or "",
            "cidId": l["cidade_id"] or "",
            "nova": (l["nova"] or "Nao"),
            **{destino: pt_br(l[col]) for col, destino in MAPA.items()},
        }
        e["versao"] = versao({k: v for k, v in e.items() if k != "id"})
        etes.append(e)
    return {"etes": etes}


async def cts(unidade_id: str) -> dict[str, Any]:
    """Grupo 05 — CTS e o pareamento 1:1 com a sub-bacia.

    `ctss` e um MAPA por id, como `subs`. E `pares` vem separado porque uma CTS SEM
    par e estado invalido que a tela precisa mostrar — sem a lista, ela nao teria
    como saber que a CTS ficou orfa.
    """
    linhas = await db.buscar(
        f"""SELECT p.sub_bacia AS sub, p.cts,
                   t.componente_sistema_id_jusante AS jusante,
                   s.sistema_id, s.sistema_name
              FROM {_i()}.subbacia_cts p
              JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = p.sub_bacia
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY p.sub_bacia""",
        unidade_id,
    )
    fichas = {
        f["cts"]: f
        for f in await db.buscar(
            f"""SELECT o.* FROM {_i()}.cts_operacional o
                  JOIN {_i()}.subbacia_cts p ON p.cts = o.cts
                  JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = p.sub_bacia
                  JOIN {_i()}.cidade_sistema s USING (sistema_id)
                  JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id""",
            unidade_id,
        )
    }
    obras = await _obras_por_ficha("componentes_cts_capex", "cts", list(fichas))

    ctss: dict[str, Any] = {}
    for l in linhas:
        cid = l["cts"]
        if cid not in fichas:
            continue
        ficha = {
            **_ficha_coleta(fichas[cid], "cts"),
            "nome": cid,
            "subId": l["sub"],
            "sisId": l["sistema_id"],
            "sistema": l["sistema_name"] or l["sistema_id"],
            "jusante": l["jusante"] or "",
            "obrasOverride": obras.get(cid, {}),
        }
        ficha["versao"] = versao(
            {k: ficha[k] for k in ("db", "params", "obrasOverride")}
        )
        ctss[cid] = ficha

    return {"pares": [{"sub": l["sub"], "cts": l["cts"]} for l in linhas], "ctss": ctss}
