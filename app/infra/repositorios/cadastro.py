"""Leitura de `input.*` — o cadastro que alimenta a simulação.

A hierarquia é `regional → diretoria → unidade → empresa → cidade → sistema`.
O recorte é sempre a UNIDADE, e ele desce dela para baixo:

    unidade_regional → empresa → cidade_empresa → cidade
                     → cidade_sistema → sistema_topologia
                     → subbacia_operacional / cts_operacional / ete_capex

Os dois níveis ACIMA da unidade (`regional`, `diretoria`) não entram no recorte:
eles são o caminho até ela, e quem escolhe a unidade já passou por eles.

Não há coluna `unidade_id` nas tabelas de baixo: quem pertence a quem sai do
encadeamento de FKs. Por isso quase toda consulta aqui carrega o mesmo CTE de
cidades da unidade — extraí-lo em `_CIDADES_DA_UNIDADE` mantém o recorte escrito
uma vez só, e é o recorte que, errado, faria a tela mostrar cidade de outra
unidade sem nenhum sinal.

Os nomes de campo na resposta são os do FRONT (`ligU`, `ecoA`, `preco`), e não os
das colunas. A tradução mora aqui de propósito: é o único lugar onde as duas
convenções se encontram, e espalhá-la faria cada endpoint inventar a sua.
"""

import logging
from typing import Any

from app.config import config
from app.infra import db
from app.infra.repositorios import pendencias
from app.dominio.campos import COLETA, DO_DATABRICKS
from app.dominio.formato import SEM_SEPARADOR, pt_br, pt_br_ano


log = logging.getLogger(__name__)


def _i() -> str:
    return config().schema_input


#: As cidades de uma unidade — o recorte de tudo. `$1` é o `unidade_id`.
#:
#: TRÊS TABELAS ONDE ANTES ERAM DUAS (modelo de dados v8): o município deixou de
#: existir só como linha de vínculo e ganhou tabela própria, então o caminho
#: passa por `cidade_empresa` (o vínculo) até `cidade` (o município). A
#: superintendência virou `empresa`, campo a campo.
_CIDADES_DA_UNIDADE = """
    SELECT c.cidade_id, c.cidade_name, ce.emp_codigo
      FROM {i}.cidade c
      JOIN {i}.cidade_empresa ce ON ce.cidade_id = c.cidade_id
      JOIN {i}.empresa e ON e.emp_codigo = ce.emp_codigo
     WHERE e.unidade_id = $1
"""


def _auditoria(linha: dict[str, Any]) -> dict[str, Any]:
    """`atualizadoEm` e `atualizadoPor` — a última gravação desta ficha.

    A escrita de cadastro não tem controle otimista: a tela usa este carimbo para
    mostrar quem gravou por último e quando (R6). Duas pessoas na mesma ficha
    podem se sobrescrever, e é aqui que isso fica visível — depois, não no momento
    da gravação.

    **ISO-8601 com fuso, e não data formatada.** Quem lê pode estar em outro fuso,
    e uma data formatada no servidor congela o formato. A formatação é do front.

    **Vazio, e não nulo.** A tela trata todo campo de ficha como string editável e
    chama `.trim()`; um `null` ali derruba a tela inteira, não só o campo. Ficha
    nunca gravada pela tela devolve os dois vazios — a coluna não tem
    `DEFAULT now()`, para não afirmar uma alteração que não houve.
    """
    quando = linha.get("atualizado_em")
    return {
        "atualizadoEm": quando.isoformat() if quando else "",
        "atualizadoPor": linha.get("atualizado_por") or "",
    }


def _cidades_cte() -> str:
    return _CIDADES_DA_UNIDADE.format(i=_i())


# ---------------------------------------------------------------- organização
async def regionais() -> list[dict[str, Any]]:
    """As regionais que TÊM unidade, deduzidas de `unidade_regional`.

    Existe `input.regional`, e mesmo assim a lista sai daqui: uma regional sem
    unidade nenhuma não é escolhível — a tela seguinte abriria vazia, e a pessoa
    voltaria sem saber o que fez de errado. `DISTINCT` na unidade responde
    "regionais em que há o que abrir", que é a pergunta da tela.
    """
    linhas = await db.buscar(
        f"""SELECT DISTINCT regional_id AS id, regional_name AS nome
              FROM {_i()}.unidade_regional
             WHERE regional_id IS NOT NULL ORDER BY 2"""
    )
    return [dict(l) for l in linhas]


async def diretorias(regional_id: str) -> list[dict[str, Any]]:
    """As diretorias da regional que têm unidade — mesma regra de `regionais`.

    A DIRETORIA É O NÍVEL ENTRE A REGIONAL E A UNIDADE (migração 017):
    regional → diretoria → unidade → empresa → cidade → sistema.

    Sai de `unidade_regional`, e não de `input.diretoria`, pela razão de
    `regionais`: uma diretoria sem unidade abriria uma tela vazia. E o `JOIN` na
    unidade é o que faz o recorte por concessão da rota funcionar sem uma
    segunda regra — quem enxerga a unidade enxerga a diretoria dela.
    """
    linhas = await db.buscar(
        f"""SELECT DISTINCT diretoria_id AS id, diretoria_name AS nome
              FROM {_i()}.unidade_regional
             WHERE regional_id = $1 AND diretoria_id IS NOT NULL
             ORDER BY 2""",
        regional_id,
    )
    return [dict(l) for l in linhas]


async def unidades(regional_id: str) -> list[dict[str, Any]]:
    linhas = await db.buscar(
        f"""SELECT unidade_id FROM {_i()}.unidade_regional
             WHERE regional_id = $1 ORDER BY unidade_name""",
        regional_id,
    )
    return [await unidade(l["unidade_id"]) for l in linhas]  # type: ignore[misc]


def _resumo(c: dict[str, Any]) -> dict[str, int]:
    """O PORTE DA UNIDADE, a partir dos contadores da consulta da capa.

    Quem le isto esta decidindo se roda esta unidade ou outra, e a tela de nova
    simulacao usa estes numeros para dizer se a rodada e de minutos ou de meia
    hora.

    `etes` e `cts` sairam da consulta e eram DESCARTADOS aqui — contados no banco,
    montados no dicionario, e jogados fora no `return`. Passam a ser entregues:
    quem paga a consulta ja pagou por eles.

    AS TRES CATEGORIAS DE COMPONENTE, e por que elas existem em vez de um numero:

      obrasAegea      `capex > 0` — investimento da Aegea
      obrasTerceiros  `capex = 0` e `tempo_execucao > 0` — a obra ACONTECE e ocupa
                      prazo na sequencia, mas quem paga e outro
      semObra         `capex = 0` e `tempo_execucao = 0` — o elemento existe na
                      ficha e nao gera obra nenhuma

    Sao exaustivas e sem sobreposicao: somadas, dao o total de linhas das duas
    tabelas de componente. E o corte importa porque um numero so escondia os dois
    extremos — "11.525 obras" contava 4.830 linhas que nao sao obra.

    `obras` = Aegea + terceiros, que e EXATAMENTE o filtro do motor
    (`otimizador_capex_v62.ler_banco`: `necess = cap > 0 or pe > 0`). Antes vinha
    de `sub_bacias * 5 + cts * 4`: as constantes batem com a base (5,00 linhas por
    sub-bacia, 4,00 por CTS), mas contavam FICHAS, nao candidatas — e inflavam o
    numero em ~43% na maior unidade. Agora sao contadas, e nao presumidas.

    Ainda NAO e o total que o motor usa: faltam uma obra por ETE de sistema e, com
    `ETE_FASEADA`, os modulos de expansao. Esses dependem de parametro da RODADA,
    entao nenhum numero por unidade os alcanca — e prometer que alcanca seria o
    erro que este corte veio consertar.
    """

    def conta(chave: str) -> int:
        # `or 0` e nao so o default do `get`: a consulta devolve NULL quando nao ha
        # linha, e `int(None)` estoura.
        return int(c.get(chave) or 0)

    aegea, terceiros = conta("obras_aegea"), conta("obras_terceiros")
    return {
        "cidades": conta("cidades"),
        "sistemas": conta("sistemas"),
        "subBacias": conta("sub_bacias"),
        "cts": conta("cts"),
        "etes": conta("etes"),
        "obras": aegea + terceiros,
        "obrasAegea": aegea,
        "obrasTerceiros": terceiros,
        "semObra": conta("sem_obra"),
    }


async def unidade(unidade_id: str) -> dict[str, Any] | None:
    base = await db.buscar_um(
        f"""SELECT unidade_id, unidade_name, regional_id, regional_name,
                   diretoria_id, diretoria_name, wacc_medio
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
                          JOIN sis ON sis.sistema_id = t.sistema_id),
                 -- Os componentes de CAPEX das duas fichas, no mesmo formato: o
                 -- que separa uma obra de um elemento sem obra e a mesma regra
                 -- nas duas, e ela nao deve ser escrita duas vezes.
                 obr AS (
                   SELECT k.capex, k.tempo_execucao
                     FROM {_i()}.componentes_subbacias_capex k
                    WHERE k.sub_bacia IN (SELECT componente_sistema_id FROM sub)
                   UNION ALL
                   -- A CTS e componente da topologia como qualquer outro, entao
                   -- as obras dela entram por `sub` (que e a topologia da
                   -- unidade), e nao pelo par com a sub-bacia.
                   SELECT k.capex, k.tempo_execucao
                     FROM {_i()}.componentes_cts_capex k
                    WHERE k.cts IN (SELECT componente_sistema_id FROM sub))
            SELECT (SELECT count(*) FROM cid) AS cidades,
                   (SELECT count(*) FROM sis) AS sistemas,
                   (SELECT count(*) FROM {_i()}.subbacia_operacional b
                     WHERE b.sub_bacia IN (SELECT componente_sistema_id FROM sub)) AS sub_bacias,
                   (SELECT count(*) FROM {_i()}.ete_capex e
                     WHERE e.ete_id IN (SELECT componente_sistema_id FROM sub)) AS etes,
                   (SELECT count(*) FROM {_i()}.cts_operacional o
                     WHERE o.cts IN (SELECT componente_sistema_id FROM sub)) AS cts,
                   -- Os tres baldes do componente de CAPEX, exaustivos e sem
                   -- sobreposicao: a soma e o total de linhas das duas tabelas.
                   (SELECT count(*) FROM obr
                     WHERE COALESCE(capex, 0) > 0) AS obras_aegea,
                   (SELECT count(*) FROM obr
                     WHERE COALESCE(capex, 0) = 0
                       AND COALESCE(tempo_execucao, 0) > 0) AS obras_terceiros,
                   (SELECT count(*) FROM obr
                     WHERE COALESCE(capex, 0) = 0
                       AND COALESCE(tempo_execucao, 0) = 0) AS sem_obra""",
        unidade_id,
    ) or {}

    return {
        "id": base["unidade_id"],
        "regionalId": base["regional_id"],
        # NULO ATRAVESSA em vez de virar texto vazio: a diretoria pode ainda não
        # ter chegado na carga, e `""` diria que ela existe e não tem nome.
        "diretoriaId": base["diretoria_id"],
        "diretoriaNome": base["diretoria_name"],
        "nome": base["unidade_name"],
        "waccMedio": base["wacc_medio"],
        "resumo": _resumo(c),
        "completude": (await pendencias.contar(unidade_id))["completude"],
        "databricksConectado": True,
    }


# ------------------------------------------------------------------- fichas
async def hierarquia(unidade_id: str) -> dict[str, Any]:
    """Grupo 01 — a arvore inteira, seis niveis:
    `regional > diretoria > unidade > empresa > cidade > sistema`.

    OS NOMES SAO OS DO FRONT, e sao curtos: `rid`/`uid`/`empId`/`cidId`/`sis`/
    `jus`, como declarados em `cadastro/domain/hierarquia.ts`. Renomear um deles
    aqui nao quebra nada visivel — a tela apenas encontra `undefined` no lugar do
    campo e renderiza em branco, sem erro.

    Tudo string: o front trata como texto e chama `.trim()`.
    """
    # `usaCts` sai como `'true'`/`'false'` MINUSCULO, e a conversao e feita aqui,
    # no SQL. O contrato do Grupo 01 e "tudo string" (`txt()` abaixo converte a
    # resposta inteira), e `str(True)` em Python daria `'True'` — o front
    # compararia com `'true'` e acharia que a unidade nao usa CTS, calado.
    u = await db.buscar_um(
        f"""SELECT regional_id, regional_name, diretoria_id, diretoria_name,
                   unidade_id, unidade_name, wacc_medio,
                   CASE WHEN usa_macrorregiao_cts THEN 'true' ELSE 'false' END AS usa_cts
              FROM {_i()}.unidade_regional WHERE unidade_id = $1""",
        unidade_id,
    ) or {}
    unid = {
        "rid": u.get("regional_id") or "",
        "rnome": u.get("regional_name") or "",
        # A DIRETORIA entre a regional e a unidade (migração 017). Aqui vazio, e
        # não nulo: o contrato do Grupo 01 é "tudo string", e a tela chama
        # `.trim()` no que recebe.
        "did": u.get("diretoria_id") or "",
        "dnome": u.get("diretoria_name") or "",
        "uid": u.get("unidade_id") or "",
        "unome": u.get("unidade_name") or "",
        "waccMedio": pt_br(u.get("wacc_medio")),
        # A POLITICA DE CTS DA UNIDADE, e nao mais de cada sistema: marcada, ela
        # usa MACRORREGIAO DE CTS e cada sistema daqui aceita UMA. Vem na
        # hierarquia porque e la que a tela monta a aba da unidade, junto do WACC
        # — e e logo abaixo dele que a caixa aparece.
        "usaCts": u.get("usa_cts") or "false",
    }
    empresas = await db.buscar(
        f"""SELECT emp_codigo AS id, empresa AS nome,
                   data_fim_concessao AS "fimConcessao"
              FROM {_i()}.empresa WHERE unidade_id = $1
             ORDER BY 2""",
        unidade_id,
    )
    cidades = await db.buscar(
        f"""SELECT cidade_id AS id, cidade_name AS nome,
                   emp_codigo AS "empId"
              FROM ({_cidades_cte()}) c ORDER BY cidade_name""",
        unidade_id,
    )
    # O SISTEMA NAO DECLARA MAIS SE USA CTS: a politica passou a ser da unidade,
    # e vem em `unid.usaCts` acima. Um `usaCts` por sistema aqui seria a mesma
    # resposta repetida N vezes, e daria a entender que ainda da para divergir.
    sistemas = await db.buscar(
        f"""SELECT s.sistema_id AS id, s.sistema_name AS nome, s.cidade_id AS "cidId"
              FROM {_i()}.cidade_sistema s
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY s.sistema_name""",
        unidade_id,
    )
    # O `JOIN` recorta pela unidade E, por ser interno, deixa de fora quem nao tem
    # sistema. Os dois efeitos sao desejados aqui: `topo` e a arvore MONTADA desta
    # unidade. Quem esta fora de sistema vem em `semSistema`, logo abaixo.
    # `tipo` vem junto porque a tela trata os tres de forma diferente: sub-bacia e
    # ETE pertencem ao sistema por carga do Databricks, e a CTS e a unica que a
    # Regional coloca e tira. Sem o tipo, a tela ofereceria "tirar do sistema"
    # para uma sub-bacia — o que nao e decisao dela.
    topo = await db.buscar(
        f"""SELECT t.sistema_id AS sis, t.componente_sistema_id AS id,
                   t.componente_sistema_nome AS nome,
                   t.componente_sistema_id_jusante AS jus,
                   CASE WHEN e.ete_id IS NOT NULL THEN 'ete'
                        WHEN k.cts    IS NOT NULL THEN 'cts'
                        ELSE 'sub-bacia' END AS tipo
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
              LEFT JOIN {_i()}.ete_capex e ON e.ete_id = t.componente_sistema_id
              LEFT JOIN {_i()}.cts_operacional k ON k.cts = t.componente_sistema_id
             ORDER BY t.sistema_id, t.componente_sistema_id""",
        unidade_id,
    )
    # COMPONENTE SEM SISTEMA — cadastrado, ainda nao colocado em lugar nenhum.
    #
    # RECORTADO PELA UNIDADE, e isto e uma correcao. O comentario aqui dizia que
    # o recorte era impossivel — "sem sistema nao ha cidade, nao ha empresa, nao
    # ha unidade" — e por isso a resposta trazia TODAS as CTS da base, para
    # qualquer unidade. A premissa era falsa: a fonte sempre soube onde a CTS
    # esta (o extrato de portfolio traz CIDADE e CTS na mesma linha). Quem tinha
    # perdido a informacao era o esquema, e a migracao 018 a devolveu em
    # `cts_operacional.cidade_id`.
    #
    # O que a falta de recorte custava: das 151 CTS soltas da base, TODAS sao de
    # uma unidade so. As outras quatro unidades recebiam as 151 assim mesmo —
    # uma lista inteira de candidatas que nao podiam ser colocadas ali sem erro,
    # e nenhuma indicacao de qual era qual.
    #
    # CIDADE DESCONHECIDA NAO SOME. `cidade_id` e nulavel (a carga pode nao ter
    # trazido), e escondê-la deixaria uma CTS que existe no banco sem forma
    # nenhuma de ser colocada. Ela vem, com `cidId` vazio, e a tela a mostra
    # separada — o mesmo tratamento das unidades sem diretoria.
    #
    # O RECORTE SO ALCANCA CTS, e o `c.cts IS NULL` diz isso em vez de deixar
    # acontecer. Sub-bacia e ETE nao tem coluna de cidade em lugar nenhum: se um
    # dia uma ficar sem sistema, nao ha por onde recorta-la, e ela aparece para
    # todas as unidades. Sem esta linha o efeito era o mesmo, mas POR ACIDENTE —
    # `c.cidade_id IS NULL` e verdadeiro para quem nem esta em `cts_operacional`,
    # e a consulta parecia recortar o que nao recortava. Hoje a lista e 100% CTS,
    # entao isto e sobre a proxima pessoa a ler a consulta, nao sobre um defeito
    # visivel.
    #
    # `tipo` viaja junto porque a tela precisa rotular a lista, e a natureza do
    # componente nao esta na topologia: ela e a tabela em que ele tem ficha.
    # `cidId` viaja junto porque e por ele que a tela recorta o seletor para a
    # cidade do sistema que esta sendo montado.
    sem_sistema = await db.buscar(
        f"""WITH cid AS ({_cidades_cte()})
            SELECT t.componente_sistema_id AS id,
                   t.componente_sistema_nome AS nome,
                   CASE WHEN e.ete_id IS NOT NULL THEN 'ete'
                        WHEN c.cts    IS NOT NULL THEN 'cts'
                        WHEN b.sub_bacia IS NOT NULL THEN 'sub-bacia'
                        ELSE '' END AS tipo,
                   c.cidade_id AS "cidId"
              FROM {_i()}.sistema_topologia t
              LEFT JOIN {_i()}.ete_capex e ON e.ete_id = t.componente_sistema_id
              LEFT JOIN {_i()}.cts_operacional c ON c.cts = t.componente_sistema_id
              LEFT JOIN {_i()}.subbacia_operacional b
                     ON b.sub_bacia = t.componente_sistema_id
             WHERE t.sistema_id IS NULL
               AND (c.cts IS NULL
                    OR c.cidade_id IS NULL
                    OR c.cidade_id IN (SELECT cidade_id FROM cid))
             ORDER BY 3, 2, 1""",
        unidade_id,
    )

    def txt(linhas):
        return [{k: ("" if v is None else str(v)) for k, v in l.items()} for l in linhas]

    return {
        "unidReg": unid,
        "empresas": txt(empresas),
        "cidades": txt(cidades),
        "sistemas": txt(sistemas),
        "topo": txt(topo),
        "semSistema": txt(sem_sistema),
    }


async def contrato(unidade_id: str) -> dict[str, Any]:
    """Grupo 02 — cidades, metas e as faixas de paridade.

    Os nomes aqui são CURTOS (`fim`, `cid`, `par`) porque são os do front
    (`Cidade`, `Meta`, `Fator` em `cadastro/domain/contrato.ts`). A grafia é
    contrato: o front lê cada campo pelo nome, sem guarda de ausência, e um campo
    rebatizado aqui chega `undefined` do outro lado.

    `fim` CONTINUA SENDO SERVIDO e não é mais editável por aqui: a concessão é da
    empresa (`PUT /empresas/{emp_codigo}`), e a aba do município apenas mostra o
    ano que desceu para ela.

    E TUDO vai como string, inclusive ano e percentual — o front trata todo campo
    de ficha como texto editável e chama `.trim()` neles. Número cru quebraria do
    mesmo jeito.
    """
    cidades = await db.buscar(
        f"""SELECT c.cidade_id AS id, c.cidade_name AS nome,
                   -- A EMPRESA que responde pelo municipio. A cidade sempre teve
                   -- uma (`cidade_empresa`), mas a consulta nao a trazia e a aba
                   -- mostrava as duas colunas vazias.
                   c.emp_codigo AS "empId", emp.empresa AS "empNome",
                   o.data_fim_concessao AS fim,
                   o.atualizado_em, o.atualizado_por
              FROM ({_cidades_cte()}) c
              JOIN {_i()}.empresa emp ON emp.emp_codigo = c.emp_codigo
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

    # A auditoria sai do `_txt` porque não é campo de ficha: `pt_br` a trataria
    # como número e a devolveria mastigada. Ela entra depois, pelo `_auditoria`,
    # que é o único lugar que sabe a forma dela.
    #
    # E ela cobre a ficha de cidade INTEIRA, mesmo saindo só de `cidade_operacional`:
    # a ficha nasce de três tabelas, mas o `PUT` grava as três de uma vez e carimba
    # a cidade em toda gravação — quem mexeu numa meta aparece aqui.
    _AUDITADAS = ("atualizado_em", "atualizado_por")
    cidades = [
        {
            **_txt({k: v for k, v in c.items() if k not in _AUDITADAS}, ("id", "nome")),
            **_auditoria(c),
        }
        for c in cidades
    ]
    metas = [_txt(m, ("cid",)) for m in metas]
    fator = [_txt(f, ("cid",)) for f in fator]

    return {"cidades": cidades, "metas": metas, "fator": fator}


def _ticket(linha: dict[str, Any]) -> str:
    """Receita media mensal por ligacao — o `ticket` do bloco `db`.

    NAO e coluna: e conta, e por isso eu a tinha esquecido. O tipo `SubBaciaDb` do
    front declara `ticket: string` e a tela o mostra entre as medidas do
    Databricks; sem ele o campo chega `undefined` e vira um input sem valor.

    Base ARRECADADA, e nao faturada: e o que de fato entrou, ja refletindo
    inadimplencia — a mesma escolha que o notebook chama de recomendada. Sem
    ligacoes atuais nao ha divisao, e o campo sai vazio (nunca zero, que afirmaria
    ticket nulo onde a conta nao existe).
    """
    receita = linha.get("receita_arrecadada_media_mensal")
    ligacoes = linha.get("ligacoes_atuais")
    if receita is None or not ligacoes:
        return ""
    return pt_br(round(float(receita) / float(ligacoes), 2))


def _ficha_coleta(linha: dict[str, Any], chave: str) -> dict[str, Any]:
    # Todo número sai em pt-BR — o mesmo formato que o `PUT` exige de volta. Ver
    # `pt_br`: a ficha lida tem de poder ser reenviada sem tradução no meio.
    db_bloco = {v: pt_br(linha[k]) for k, v in COLETA.items() if v in DO_DATABRICKS}
    params = {v: pt_br(linha[k]) for k, v in COLETA.items() if v not in DO_DATABRICKS}
    db_bloco["ticket"] = _ticket(linha)
    # A auditoria fica FORA de `db` e de `params`: os dois blocos são o contrato do
    # que o `PUT` devolve inteiro (`exigir_ficha_inteira`), e quem gravou não é
    # campo de ficha — é fato sobre a ficha. Dentro de um bloco, o cliente passaria
    # a ser obrigado a reenviá-la, e reenviar autoria é justamente o que não pode.
    return {"id": linha[chave], "db": db_bloco, "params": params, **_auditoria(linha)}


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
                   c.cidade_id, c.cidade_name, c.emp_codigo,
                   e.empresa
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
              JOIN {_i()}.empresa e USING (emp_codigo)
             ORDER BY e.empresa, c.cidade_name, s.sistema_name,
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
        subs[sid] = ficha

    return {"arvore": _arvore(linhas, com_ficha=set(subs)), "subs": subs}


def _arvore(linhas: list[dict[str, Any]], com_ficha: set[str]) -> list[dict[str, Any]]:
    """Sup → cidade → sistema → subIds, só com os ramos que levam a alguma ficha."""
    sups: dict[str, dict[str, Any]] = {}
    for l in linhas:
        if l["sub_id"] not in com_ficha:
            continue
        sup = sups.setdefault(
            l["emp_codigo"],
            {
                "id": l["emp_codigo"],
                "nome": l["empresa"] or l["emp_codigo"],
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


#: Colunas de obra -> nomes do front. Inverso do `OBRA` da escrita, mais o
#: `componente`.
#:
#: `nome` NAO tem contrapartida em `OBRA`, e a assimetria e proposital: o nome
#: identifica a obra, nao e editavel na tela, e a escrita o grava a partir da
#: linha que ja esta no banco. Aceita-lo de volta no corpo deixaria um cliente
#: renomear componente — e o nome e justamente o que o motor casa com
#: `componentes_*_capex` (`otimizador_capex_v62.py:1136`).
#:
#: Ele passou a viajar quando a base literal de obras saiu do front: sem base, e
#: daqui que a tela tira o rotulo de cada linha. `pt_br` devolve texto intacto
#: (`str(v)` para o que nao e numero), entao nome e unidade atravessam o mesmo
#: caminho dos numeros sem tratamento especial.
_OBRA_LEITURA = {
    "componente": "nome",
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

#: O INDICE do override sai da POSICAO do componente na obra-base do front, entao
#: cada base precisa do seu de-para. E as duas bases usam VOCABULARIOS DIFERENTES
#: para a mesma peca — conferido no cadastro real:
#:
#:   sub-bacia:  Coletor tronco | Estacao elevatoria (EEE) | Linha de recalque (LR)
#:   CTS:        Tronco         | EEE                      | Linha de recalque
#:
#: AS DUAS GRAFIAS SAO ACEITAS porque as tabelas divergem no vocabulario. Usar so
#: a da sub-bacia faz a ficha da CTS voltar com 1 obra em vez de 4 — e as outras
#: tres perdem, em silencio, o que a Regional tiver digitado.
_INDICE_SUBBACIA = {
    "Ligacao de esgoto": "0", "Ligação de esgoto": "0",
    "Rede coletora": "1",
    "Coletor tronco": "2", "Tronco": "2",
    "Estacao elevatoria (EEE)": "3", "Estação elevatória (EEE)": "3", "EEE": "3",
    "Linha de recalque (LR)": "4", "Linha de recalque": "4",
}
_INDICE_CTS = {
    "Coletor de tempo seco": "0",
    "Coletor tronco": "1", "Tronco": "1",
    "Estacao elevatoria (EEE)": "2", "Estação elevatória (EEE)": "2", "EEE": "2",
    "Linha de recalque (LR)": "3", "Linha de recalque": "3",
}


async def _obras_por_ficha(
    tabela: str, chave: str, ids: list[str], indice: dict[str, str] | None = None
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
    pos = indice or _INDICE_SUBBACIA
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
                   -- O SISTEMA da ETE. O join com `cidade_sistema` ja existia
                   -- (e por ele que a ETE chega a uma unidade); faltava trazer a
                   -- coluna, e a tela mostrava "ID Sistema" vazio nas 474.
                   s.sistema_id, s.sistema_name,
                   e.capacidade_por_modulo, e.capex_por_modulo, e.opex_por_modulo,
                   e.tempo_de_execucao, e.capacidade_nominal_atual,
                   e.vazao_de_operacao_atual, e.capex_terreno, e.modulos, e.wacc,
                   e.tempo_predecessoras, e.obra_obrigatoria_ano, e.obra_proibida_ate,
                   e.nova, e.atualizado_em, e.atualizado_por
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
        #: A OBRA DA ETE TEM PRAZO E JANELA, como qualquer outra obra.
        #:
        #: As tres colunas sempre existiram em `ete_capex` e o motor sempre as leu
        #: (`otimizador_capex_v62.py:1314-1315`: `prazo_inicio`, `obrigatoria`,
        #: `proibida_ate` da ETE saem daqui). Faltava so mandá-las — entao a
        #: restricao valia na simulacao e nenhuma tela conseguia defini-la: quem
        #: precisasse dizer "esta ETE e obrigatoria em 2028" nao tinha onde.
        #:
        #: `capacidade_ociosa` continua de FORA de propósito: e derivada
        #: (nominal menos vazao de operacao) e o motor avisa quando o valor
        #: gravado discorda da conta. Campo derivado nao volta no PUT — mesma
        #: regra do `ticket` da sub-bacia.
        "tempo_predecessoras": "tPred",
        "obra_obrigatoria_ano": "anoObrig",
        "obra_proibida_ate": "proibAte",
    }

    etes = []
    for l in linhas:
        e = {
            "id": l["ete_id"],
            "sub": l["sub"] or "",
            "cidId": l["cidade_id"] or "",
            "sisId": l["sistema_id"] or "",
            "sistema": l["sistema_name"] or l["sistema_id"] or "",
            "nova": (l["nova"] or "Nao"),
            #: `pt_br_ano` nos que sao ANO, pela mesma razao da aba de obras: um
            #: `pt_br(2028)` devolve "2.028", e ano com separador de milhar e erro
            #: de leitura na tela. A regra ja existia em `SEM_SEPARADOR`; a ETE e
            #: que passava tudo por `pt_br` porque, ate agora, nao tinha campo de
            #: ano nenhum.
            **{
                destino: (pt_br_ano if destino in SEM_SEPARADOR else pt_br)(l[col])
                for col, destino in MAPA.items()
            },
            **_auditoria(l),
        }
        etes.append(e)
    return {"etes": etes}


async def cts(unidade_id: str) -> dict[str, Any]:
    """Grupo 05 — as CTS COLOCADAS nos sistemas desta unidade.

    `ctss` e um MAPA por id, como `subs`.

    A ancora e a TOPOLOGIA, e nao o pareamento com a sub-bacia: quem diz que uma
    CTS e desta unidade e o sistema em que ela foi colocada, e sistema e coisa que
    a Regional monta (Grupo 01). Uma CTS ainda nao colocada nao aparece aqui —
    ela nao e de unidade nenhuma, nao entra na simulacao e nao tem o que preencher
    ainda. Ela vive na lista do Grupo 01, esperando ser adicionada a um sistema.

    `sisId`, `sistema` e `jusante` saem da linha DA PROPRIA CTS. Antes vinham da
    linha da sub-bacia pareada, o que fazia a tela mostrar o caminho de outro
    componente como se fosse o dela.

    `inconsistencias` traz componente colocado no sistema que nao tem ficha —
    ver `_cts_inconsistentes`. Ela NAO cruza com `ctss`: sao justamente os que
    nao tem ficha para editar.
    """
    fichas = {
        f["cts"]: f
        for f in await db.buscar(
            f"""SELECT o.* FROM {_i()}.cts_operacional o
                  JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = o.cts
                  JOIN {_i()}.cidade_sistema s USING (sistema_id)
                  JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id""",
            unidade_id,
        )
    }
    linhas = await db.buscar(
        f"""SELECT t.componente_sistema_id AS cts,
                   t.componente_sistema_nome AS nome,
                   t.componente_sistema_id_jusante AS jusante,
                   s.sistema_id, s.sistema_name
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cts_operacional o ON o.cts = t.componente_sistema_id
              JOIN {_i()}.cidade_sistema s USING (sistema_id)
              JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
             ORDER BY s.sistema_name, t.componente_sistema_id""",
        unidade_id,
    )
    obras = await _obras_por_ficha(
        "componentes_cts_capex", "cts", list(fichas), _INDICE_CTS
    )

    ctss: dict[str, Any] = {}
    for l in linhas:
        cid = l["cts"]
        if cid not in fichas:
            continue
        ctss[cid] = {
            **_ficha_coleta(fichas[cid], "cts"),
            "nome": l["nome"] or cid,
            "sisId": l["sistema_id"],
            "sistema": l["sistema_name"] or l["sistema_id"],
            "jusante": l["jusante"] or "",
            "obrasOverride": obras.get(cid, {}),
        }

    return {
        "ctss": ctss,
        "inconsistencias": await _cts_inconsistentes(unidade_id),
    }


async def _cts_inconsistentes(unidade_id: str) -> list[dict[str, Any]]:
    """Componente COLOCADO no sistema que nao tem ficha em lugar nenhum.

    Um componente precisa de duas coisas para existir de verdade: estar num
    sistema (`sistema_topologia` com `sistema_id`) e ter ficha — em
    `subbacia_operacional`, `cts_operacional` ou `ete_capex`. O motor monta os nos
    percorrendo a topologia; sem ficha, o no entra na simulacao com demanda ZERO,
    ocupa posicao na rede e puxa a media do sistema para baixo, sem aparecer como
    erro em lugar nenhum.

    E o unico estado meio-existente que sobrou, e o unico que MUDA O RESULTADO.
    Componente sem sistema nao entra aqui: nao estar colocado e estado normal —
    e o que acontece com toda CTS antes de a Regional adiciona-la a um sistema.

    Nao ha mais "ficha sem no" nem "sem par". O primeiro virou o estado normal
    acima; o segundo dependia de `subbacia_cts`, que e sobreposicao de area e nao
    diz onde a CTS esta.

    O `GET` devolve isto porque nao e diagnostico de infraestrutura: e informacao
    de cadastro, e quem le a tela e exatamente quem pode corrigi-la.

    A checagem NAO usa prefixo de id (havia um `LIKE 'cts%'` aqui). Prefixo e
    convencao de quem gerou a base, e um componente sem ficha e problema seja ele
    qual for — inclusive um que ninguem consiga classificar, que e justamente o
    caso mais suspeito.
    """
    achados = await db.buscar(
        f"""
        SELECT 'no-sem-ficha' AS tipo, t.componente_sistema_id AS id,
               t.componente_sistema_nome AS nome,
               'Esta num sistema e nao tem ficha em lugar nenhum. '
               'Entra na simulacao com demanda zero.' AS detalhe
          FROM {_i()}.sistema_topologia t
          JOIN {_i()}.cidade_sistema s USING (sistema_id)
          JOIN ({_cidades_cte()}) c ON c.cidade_id = s.cidade_id
         WHERE NOT EXISTS (SELECT 1 FROM {_i()}.cts_operacional o
                            WHERE o.cts = t.componente_sistema_id)
           AND NOT EXISTS (SELECT 1 FROM {_i()}.subbacia_operacional b
                            WHERE b.sub_bacia = t.componente_sistema_id)
           AND NOT EXISTS (SELECT 1 FROM {_i()}.ete_capex e
                            WHERE e.ete_id = t.componente_sistema_id)
         ORDER BY 2""",
        unidade_id,
    )
    if achados:
        log.warning(
            "unidade %s: %d CTS inconsistente(s) — %s",
            unidade_id,
            len(achados),
            ", ".join(f"{a['tipo']}:{a['id']}" for a in achados[:5]),
        )
    return [dict(a) for a in achados]


#: Quantas alterações a leitura devolve por vez. Não é paginação — é um teto.
#:
#: A trilha é append-only e cresce com o uso: uma ficha muito editada acumula
#: centenas de linhas, e mandar todas para desenhar uma lista que ninguém rola
#: até o fim gasta banda para nada. O teto é generoso o bastante para a pergunta
#: real ("o que andou mudando aqui") e a resposta DIZ quando cortou, para a tela
#: não afirmar que aquilo é o histórico inteiro.
LIMITE_ALTERACOES = 200


async def alteracoes(
    unidade_id: str,
    *,
    tipo: str | None = None,
    ficha_id: str | None = None,
    limite: int = LIMITE_ALTERACOES,
) -> dict[str, Any]:
    """A trilha de auditoria do cadastro — quem mudou o quê, quando.

    ## Por que esta função existe

    A trilha era gravada desde a migração 001 e **nunca foi lida por ninguém**. O
    único `SELECT` nela, em todo o serviço, servia para deduplicar contra a última
    linha — e saiu quando o servidor passou a comparar com o dado gravado. Ou
    seja: o registro existia, crescia, e respondê-lo exigia SQL na mão.

    Auditoria que só o DBA alcança não é auditoria do produto. É por isso que este
    endpoint veio junto da trilha completa, e não depois: gravar mais e continuar
    sem mostrar teria piorado a mesma situação.

    ## A forma

    `de`/`para` em vez de `valorAntigo`/`valorNovo`: é a leitura que a tela faz
    ("de 2.472,6 para 3.000"), e os dois lados podem ser nulos, com significados
    diferentes — `de` nulo é criação, `para` nulo é remoção
    (`migracoes/007_trilha_do_cadastro.sql`).

    `cortado` diz que o teto foi atingido. Sem ele a tela mostraria as 200 mais
    recentes afirmando, em silêncio, que aquilo é tudo.
    """
    filtros = ["o.unidade_id = $1"]
    args: list[Any] = [unidade_id]
    if tipo:
        args.append(tipo)
        filtros.append(f"o.tipo = ${len(args)}")
    if ficha_id:
        args.append(ficha_id)
        filtros.append(f"o.ficha_id = ${len(args)}")
    args.append(limite + 1)  # +1 só para saber se havia mais

    linhas = await db.buscar(
        f"""SELECT o.tipo, o.ficha_id, o.campo, o.valor_antigo, o.valor_novo,
                   o.autor, o.gravado_em, o.origem
              FROM {_i()}.override o
             WHERE {" AND ".join(filtros)}
             ORDER BY o.gravado_em DESC, o.override_id DESC
             LIMIT ${len(args)}""",
        *args,
    )
    cortado = len(linhas) > limite
    return {
        "alteracoes": [
            {
                "tipo": l["tipo"],
                "fichaId": l["ficha_id"],
                "campo": l["campo"],
                "de": l["valor_antigo"],
                "para": l["valor_novo"],
                "autor": l["autor"],
                "quando": l["gravado_em"].isoformat(),
                "origem": l["origem"],
            }
            for l in linhas[:limite]
        ],
        "cortado": cortado,
    }
