"""Quantos campos do cadastro ainda estão vazios — e o quanto ele está completo.

Esta conta tem uma exigência incomum: ela precisa dar **exatamente o mesmo número
que a tela mostra**. Se divergir, o usuário vê "cadastro completo", aperta Iniciar
e o servidor recusa dizendo que faltam três campos — sem dizer quais, porque o
número veio de outra conta.

Por isso ela é uma tradução linha a linha de `derive()` em
`src/cadastro/state/cadastroReducer.ts` e das funções de `src/cadastro/domain/`,
e não uma releitura do que "faz sentido cobrar". As regras, como estão lá:

  grupo 2 · empresa     `data_fim_concessao` — a concessão é da
                        EMPRESA, e não do município: quem assina o contrato é a
                        operadora, e o ano desce dela para as cidades. Cobrar por
                        município pediria 141 preenchimentos para o que se
                        resolve em 48.
            meta        `ano` e `cobertura_pct`
            faixa       `cobertura_pct` e `paridade`
  grupo 3 · sub-bacia   5 params (preço, tarr, ramp, vazão, potencial)
                        + 5 obras × 7 campos
  grupo 4 · ETE         6 campos-base + 2 (terreno, módulos) se for nova
  grupo 5 · CTS         igual à sub-bacia, com 4 obras

`wacc` NUNCA conta, nem na ficha, nem na obra, nem na ETE: vazio ali significa
"usa o WACC médio da unidade", que é uma resposta — não silêncio. Este parágrafo
já estava aqui e a lista `ETE` abaixo cobrava `wacc` mesmo assim: o arquivo se
contradizia, e quem ganhava era a lista. Eram 598 das 997 ETEs travando a
simulação por um campo cujo default o motor usa em 2 de cada 3 obras
(`SELECT wacc_origem, count(*) FROM otim_obra` → wacc_medio 11.099, proprio 7.289).

`vazao_contribuicao_industrial` também saiu da régua na época, por não existir na
planilha de origem — e hoje a coluna não existe mais em lugar nenhum. O recorte
residencial deixou de mexer em vazão: ela dimensiona módulo de ETE e rateia obra
compartilhada, e indústria contribui com esgoto mesmo quando não conta para a meta.
Descontá-la ali subdimensionaria a estação.

Duas sutilezas que vieram do outro lado e não são óbvias:

  - **a régua da cidade muda a conta da sub-bacia.** Trocar uma cidade para medir
    por população acrescenta pendência às sub-bacias dela na hora. É o efeito
    desejado: a simulação não pode rodar com o denominador da meta em branco.
  - **obra AUSENTE conta como pendência**, com o peso de uma obra toda em branco.
    Antes não contava — o `SUM` só percorre linha gravada —, e a unidade se
    declarava 100% completa com uma obra faltando, liberando a simulação sobre
    cadastro incompleto.

    (O texto antigo dizia que a tabela "só guarda o que difere da base". Não
    guarda: o `GET` devolve todos os campos da linha gravada, e é o banco que
    completa o que o corpo omitir — ver `obras_da_ficha`. A base literal só
    alcança componente que nunca existiu.)
"""

from typing import Any

from app.config import config
from app.infra import db
from app.dominio.campos import OBRAS_CTS, OBRAS_SUBBACIA

#: Campos de `params` que a ficha de coleta cobra sempre.
_PARAMS = [
    "preco_por_ligacao",
    "tempo_arrecadacao",
    "tempo_ramp_up",
    "vazao_contribuicao",
    "potencial_crescimento",
]


_OBRA = [
    "quantidade",
    "preco_unitario",
    "opex",
    "tempo_predecessoras",
    "tempo_execucao",
    "obra_obrigatoria_ano",
    "obra_proibida_ate",
]

#: Campos-base da ETE, e os dois que só existem quando ela é nova.
_ETE = [
    "capacidade_por_modulo",
    "capex_por_modulo",
    "opex_por_modulo",
    "tempo_de_execucao",
    "capacidade_nominal_atual",
    "vazao_de_operacao_atual",
]
_ETE_NOVA = ["capex_terreno", "modulos"]

#: Quantos campos cada ficha cobra — o denominador da completude. Espelha
#: `camposDaSub`/`camposDaCts`/`g4Total` do front, inclusive onde ele conta 3 por
#: meta e por faixa enquanto a pendência olha 2: o denominador é a escala da
#: barra, e mudá-lo aqui faria a porcentagem divergir da que a tela mostra.
_CAMPOS_META = 3
_CAMPOS_FAIXA = 3


def _i() -> str:
    return config().schema_input


def _vazios(colunas: list[str], prefixo: str = "") -> str:
    """`(x IS NULL)::int + (y IS NULL)::int + ...` — quantos daqueles estão vazios."""
    p = f"{prefixo}." if prefixo else ""
    return " + ".join(f"({p}{c} IS NULL)::int" for c in colunas)


async def contar(unidade_id: str) -> dict[str, Any]:
    """`{pendencias, completude, porGrupo}` para uma unidade.

    Numa consulta só. Cinco idas ao banco para montar um número que a tela pede a
    cada tecla digitada seria custo sem ganho — e o `prontidao` é chamado no
    momento em que o usuário clica Iniciar, quando a espera é visível.
    """
    # A CIDADE NAO TEM MAIS CAMPO COBRADO NENHUM, e a CTE fica so como RECORTE —
    # `comps`, `mt` e `fx` se apoiam nela para saber o que e desta unidade.
    #
    # Tinha dois, e os dois sairam por serem de outro dono: o fim da concessao e
    # da EMPRESA (migracao 015) e virou a CTE `empresas`; a regua da cobertura
    # (`unidade_cobertura`) virou PARAMETRO DE RODADA (migracao 019), porque nao
    # e dado do cadastro — e a lente com que se olha o cadastro.
    #
    # COM ELA SAIU A COBRANCA CONDICIONAL DE POPULACAO nas sub-bacias e CTS:
    # "2 campos a mais SE a cidade mede por populacao" dependia de uma escolha
    # que o cadastro nao conhece mais. Nao muda numero nenhum hoje — nenhuma
    # cidade da base media por populacao, entao a condicao ja valia zero.
    cidades = f"""
        SELECT c.cidade_id
          FROM {_i()}.cidade_empresa c
          JOIN {_i()}.empresa s USING (emp_codigo)
         WHERE s.unidade_id = $1
    """
    empresas = f"""
        SELECT (e.data_fim_concessao IS NULL)::int AS pend
          FROM {_i()}.empresa e
         WHERE e.unidade_id = $1
    """
    # A sub-bacia herda a régua da cidade do seu sistema — é por isso que a conta
    # dela não sai só da própria tabela.
    subs = f"""
        SELECT t.componente_sistema_id AS id
          FROM {_i()}.sistema_topologia t
          JOIN {_i()}.cidade_sistema cs USING (sistema_id)
          JOIN cidades cid ON cid.cidade_id = cs.cidade_id
    """

    linha = await db.buscar_um(
        f"""
        WITH cidades AS ({cidades}),
             empresas AS ({empresas}),
             comps AS ({subs}),
             sb AS (
                SELECT c.id,
                       {_vazios(_PARAMS, "b")} AS pend_params
                  FROM comps c
                  JOIN {_i()}.subbacia_operacional b ON b.sub_bacia = c.id
             ),
             sb_obras AS (
                -- Campos vazios das obras que EXISTEM, mais as que NAO existem.
                -- O segundo termo faltava: o SUM so percorre linha gravada, entao
                -- obra ausente contribuia zero e a ficha se declarava completa
                -- sem ela. Uma obra que falta pesa o mesmo que uma obra toda em
                -- branco, que e exatamente o que ela e.
                SELECT o.sub_bacia AS id,
                       SUM({_vazios(_OBRA, "o")})
                         + ({OBRAS_SUBBACIA} - count(*)) * {len(_OBRA)} AS pend
                  FROM {_i()}.componentes_subbacias_capex o
                  JOIN comps c ON c.id = o.sub_bacia
                 GROUP BY o.sub_bacia
             ),
             -- A CTS entra por `comps` como qualquer componente: ela E um no da
             -- topologia. Antes chegava por `subbacia_cts`, e isso escolhia as
             -- CTS erradas — uma sem par ficava fora da conta (ninguem cobrava o
             -- preenchimento dela), e uma pareada era cobrada da unidade da
             -- IRMA, mesmo estando num sistema de outra. CTS ainda nao colocada
             -- nao aparece aqui, e nao deve: ela nao entra na simulacao.
             ct AS (
                SELECT c.id,
                       {_vazios(_PARAMS, "o")} AS pend_params
                  FROM comps c
                  JOIN {_i()}.cts_operacional o ON o.cts = c.id
             ),
             ct_obras AS (
                SELECT o.cts AS id,
                       SUM({_vazios(_OBRA, "o")})
                         + ({OBRAS_CTS} - count(*)) * {len(_OBRA)} AS pend
                  FROM {_i()}.componentes_cts_capex o
                  JOIN ct ON ct.id = o.cts
                 GROUP BY o.cts
             ),
             et AS (
                SELECT lower(COALESCE(e.nova, '')) IN ('sim', 's', 'true', '1') AS nova,
                       {_vazios(_ETE, "e")} AS pend_base,
                       {_vazios(_ETE_NOVA, "e")} AS pend_nova
                  FROM {_i()}.ete_capex e
                  JOIN comps c ON c.id = e.ete_id
             ),
             mt AS (
                SELECT (m.ano IS NULL)::int + (m.cobertura_pct IS NULL)::int AS pend
                  FROM {_i()}.metas_cobertura m JOIN cidades c USING (cidade_id)
             ),
             fx AS (
                SELECT (f.cobertura_pct IS NULL)::int + (f.paridade IS NULL)::int AS pend
                  FROM {_i()}.fator_esgoto f JOIN cidades c USING (cidade_id)
             )
        SELECT
          (SELECT COALESCE(SUM(pend), 0) FROM empresas)
            + (SELECT COALESCE(SUM(pend), 0) FROM mt)
            + (SELECT COALESCE(SUM(pend), 0) FROM fx)                       AS g2,
          (SELECT COALESCE(SUM(pend_params), 0) FROM sb)
            + (SELECT COALESCE(SUM(pend), 0) FROM sb_obras)                 AS g3,
          (SELECT COALESCE(SUM(pend_base + CASE WHEN nova THEN pend_nova ELSE 0 END), 0)
             FROM et)                                                       AS g4,
          (SELECT COALESCE(SUM(pend_params), 0) FROM ct)
            + (SELECT COALESCE(SUM(pend), 0) FROM ct_obras)                 AS g5,
          -- A EMPRESA e a unica ficha de cidade com campo cobrado: o fim da
          -- concessao. A cidade em si deixou de ter (ver a CTE `cidades`).
          (SELECT count(*) FROM empresas)
            + (SELECT count(*) * {_CAMPOS_META} FROM mt)
            + (SELECT count(*) * {_CAMPOS_FAIXA} FROM fx)                   AS t2,
          (SELECT COALESCE(SUM({len(_PARAMS)} + 5 * {len(_OBRA)}), 0)
             FROM sb)                                                       AS t3,
          (SELECT COALESCE(SUM({len(_ETE)} + CASE WHEN nova THEN {len(_ETE_NOVA)}
                                                  ELSE 0 END), 0) FROM et)  AS t4,
          (SELECT COALESCE(SUM({len(_PARAMS)} + 4 * {len(_OBRA)}), 0)
             FROM ct)                                                       AS t5
        """,
        unidade_id,
    ) or {}

    grupos = {g: int(linha.get(g) or 0) for g in ("g2", "g3", "g4", "g5")}
    total = sum(int(linha.get(t) or 0) for t in ("t2", "t3", "t4", "t5"))

    # GRUPO 01 — o caminho até a ETE. Entra na conta como qualquer campo em
    # branco, e por isso TRAVA a simulação: um caminho que não chega na ETE não
    # falha na rodada, ele a faz sair mais barata do que a realidade. Uma pendência
    # por componente sem caminho, e o total é um por componente que devia ter um.
    sem_caminho = await _caminho_ate_a_ete(unidade_id)
    grupos["g1"] = len(sem_caminho)
    total += await _quantos_precisam_de_caminho(unidade_id)

    pendencias = sum(grupos.values())
    # Sem nenhum campo a preencher, 100% — e não 0/0. É o mesmo critério que o hub
    # usa para liberar a simulação, e sem o guarda a tela mostrava "NaN%".
    completude = 100 if total == 0 else round((1 - pendencias / total) * 100)
    return {
        "pendencias": pendencias,
        "completude": completude,
        "porGrupo": {
            "topologia": grupos["g1"],
            "contrato": grupos["g2"],
            "subBacias": grupos["g3"],
            "etes": grupos["g4"],
            "cts": grupos["g5"],
        },
        # As duas listas respondem a mesma pergunta — "o que a tela nao tem como
        # saber sozinha" — e por isso vao juntas. `componente` recebe o nome do
        # componente sem caminho, para a linha ler igual as outras.
        "faltando": await componentes_faltando(unidade_id)
        + [
            {
                "tipo": "topologia",
                "id": c["id"],
                "componente": c["nome"] or c["id"],
                "detalhe": (
                    f"O caminho de {c['nome'] or c['id']} não chega à ETE do sistema "
                    f"{c['sistema'] or ''}. A simulação roda assim mesmo e deixa de somar "
                    "as obras de transporte desse trecho — o plano sai mais barato do que é."
                ),
            }
            for c in sem_caminho
        ],
    }


#: O mesmo teto de saltos do motor (`caminho()`, em otimizador_capex_v62.py).
#: Igualar os dois é deliberado: se o caminho for longo demais para ele, é longo
#: demais aqui — e um número menor aqui acusaria de quebrado um caminho que a
#: simulação percorre inteiro.
_MAX_SALTOS = 200


async def _caminho_ate_a_ete(unidade_id: str) -> list[dict[str, Any]]:
    """Componentes desta unidade cujo caminho NÃO termina numa ETE.

    ## Por que isto é pendência

    O motor percorre `jusante` de nó em nó e **não verifica que chegou na ETE**
    (`caminho()`): quando a corrente acaba antes, ele simplesmente para. O efeito
    não é erro — é uma sub-bacia que deixa de somar as obras de transporte do
    trecho que falta. O plano sai **mais barato** e continua plausível, e nada em
    tela nenhuma denuncia.

    Campo em branco a tela conta sozinha; caminho quebrado, não — ele depende de
    seguir a corrente inteira, e só o banco faz isso. É a mesma razão de
    `componentes_faltando` existir: a lista traz o que a tela não tem como saber.

    A ETE não entra: ela é o fim do caminho, e cobrar dela um jusante seria cobrar
    o oposto do que a topologia aceita. Componente fora de sistema também não —
    ele não é de unidade nenhuma e não entra na simulação.
    """
    return await db.buscar(
        f"""
        WITH RECURSIVE cidades AS (
            SELECT c.cidade_id
              FROM {_i()}.cidade_empresa c
              JOIN {_i()}.empresa s USING (emp_codigo)
             WHERE s.unidade_id = $1
        ),
        comps AS (
            SELECT t.componente_sistema_id AS id, t.componente_sistema_nome AS nome,
                   cs.sistema_name AS sistema
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema cs USING (sistema_id)
              JOIN cidades c ON c.cidade_id = cs.cidade_id
             WHERE NOT EXISTS (SELECT 1 FROM {_i()}.ete_capex e
                                WHERE e.ete_id = t.componente_sistema_id)
        ),
        passo AS (
            SELECT c.id AS origem, c.id AS atual, 0 AS n FROM comps c
            UNION ALL
            -- O teto de saltos e a unica saida se houver ciclo. A escrita ja o
            -- recusa, mas esta consulta le o que ESTA gravado, e dado carregado
            -- de fora nao passou por ela.
            SELECT p.origem, t.componente_sistema_id_jusante, p.n + 1
              FROM passo p
              JOIN {_i()}.sistema_topologia t ON t.componente_sistema_id = p.atual
             WHERE t.componente_sistema_id_jusante IS NOT NULL AND p.n < {_MAX_SALTOS}
        ),
        chegada AS (
            SELECT origem,
                   bool_or(EXISTS (SELECT 1 FROM {_i()}.ete_capex e
                                    WHERE e.ete_id = passo.atual)) AS chega
              FROM passo GROUP BY origem
        )
        SELECT c.id, c.nome, c.sistema
          FROM chegada ch
          JOIN comps c ON c.id = ch.origem
         WHERE NOT ch.chega
         ORDER BY c.sistema, c.id
        """,
        unidade_id,
    )


async def _quantos_precisam_de_caminho(unidade_id: str) -> int:
    """Quantos componentes da unidade DEVEM ter caminho até a ETE — o denominador.

    Os mesmos que `_caminho_ate_a_ete` examina: componentes num sistema desta
    unidade, fora as ETEs. É o total do grupo, e é ele que faz a completude cair
    proporcionalmente quando o caminho está pela metade.
    """
    linha = await db.buscar_um(
        f"""SELECT count(*) AS n
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema cs USING (sistema_id)
              JOIN {_i()}.cidade_empresa c ON c.cidade_id = cs.cidade_id
              JOIN {_i()}.empresa s USING (emp_codigo)
             WHERE s.unidade_id = $1
               AND NOT EXISTS (SELECT 1 FROM {_i()}.ete_capex e
                                WHERE e.ete_id = t.componente_sistema_id)""",
        unidade_id,
    )
    return int((linha or {}).get("n") or 0)


async def componentes_faltando(unidade_id: str) -> list[dict[str, Any]]:
    """Quais componentes de obra a ficha NÃO tem — nome por nome.

    ## Por que isto existe, e por que só isto

    A tela mostrava o NÚMERO de pendências e mais nada. Para campo em branco isso
    bastava: o campo está na tela, destacado, e quem abre a ficha o encontra. Para
    componente AUSENTE não bastava, e a diferença é de natureza — o componente que
    falta não aparece em lugar nenhum. A ficha vem do `GET` com quatro linhas em
    vez de cinco, e nada na tela diz que havia uma quinta.

    Era pior antes: a base literal preenchia a linha que faltava com números de
    template, então a tela mostrava CINCO e a quinta era invenção. A base saiu
    (`cadastro_escrita._obras_da_ficha`), o `PUT` passou a recusar a ficha
    incompleta, e sem esta lista a pessoa levaria a recusa sem saber o que
    corrigir.

    Por isso a lista traz **só o que a tela não tem como saber**. Campo vazio ela
    conta sozinha, a cada tecla, sem ida ao servidor (`subPend`/`ctsPend` no
    front); componente ausente só o banco sabe.

    ## Os nomes não são literal

    O conjunto esperado sai do PRÓPRIO banco: é o `DISTINCT componente` da tabela,
    ou seja, "os componentes que as outras 4.849 fichas têm". Uma lista de nomes
    aqui seria a base literal voltando pela porta dos fundos — e envelheceria do
    mesmo jeito, com o mesmo silêncio.

    O custo dessa escolha, dito: se TODAS as fichas perderem o mesmo componente,
    ele deixa de ser esperado e ninguém é avisado. É improvável (a carga vem de
    uma planilha, por aba inteira) e tem rede: `tests/test_obras_do_banco.py`
    fixa a cardinalidade e os nomes contra o banco real.

    Segue a forma do `inconsistencias[]` de `GET /cts` — `{tipo, id, detalhe}` —,
    que já é como esta base denuncia cadastro meio existente.
    """
    return await db.buscar(
        f"""
        WITH cidades AS (
            SELECT c.cidade_id
              FROM {_i()}.cidade_empresa c
              JOIN {_i()}.empresa s USING (emp_codigo)
             WHERE s.unidade_id = $1
        ),
        comps AS (
            SELECT t.componente_sistema_id AS id
              FROM {_i()}.sistema_topologia t
              JOIN {_i()}.cidade_sistema cs USING (sistema_id)
              JOIN cidades c ON c.cidade_id = cs.cidade_id
        ),
        -- O que uma ficha DEVE ter é o que as fichas têm — não uma lista aqui.
        esperado_sub AS (SELECT DISTINCT componente FROM {_i()}.componentes_subbacias_capex),
        esperado_cts AS (SELECT DISTINCT componente FROM {_i()}.componentes_cts_capex)

        SELECT 'sub-bacia' AS tipo, b.sub_bacia AS id, e.componente,
               'Falta o componente ' || e.componente || ' nesta sub-bacia. '
               'A simulação da unidade fica travada até o cadastro ter os '
               'componentes, e o servidor recusa salvar a ficha assim.' AS detalhe
          FROM {_i()}.subbacia_operacional b
          JOIN comps c ON c.id = b.sub_bacia
          CROSS JOIN esperado_sub e
         WHERE NOT EXISTS (
                SELECT 1 FROM {_i()}.componentes_subbacias_capex o
                 WHERE o.sub_bacia = b.sub_bacia AND o.componente = e.componente)

        UNION ALL

        SELECT 'cts', o.cts, e.componente,
               'Falta o componente ' || e.componente || ' nesta CTS. '
               'A simulação da unidade fica travada até o cadastro ter os '
               'componentes, e o servidor recusa salvar a ficha assim.'
          FROM comps c
          JOIN {_i()}.cts_operacional o ON o.cts = c.id
          CROSS JOIN esperado_cts e
         WHERE NOT EXISTS (
                SELECT 1 FROM {_i()}.componentes_cts_capex k
                 WHERE k.cts = o.cts AND k.componente = e.componente)

         ORDER BY 1, 2, 3
        """,
        unidade_id,
    )
