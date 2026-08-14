"""Quantos campos do cadastro ainda estão vazios — e o quanto ele está completo.

Esta conta tem uma exigência incomum: ela precisa dar **exatamente o mesmo número
que a tela mostra**. Se divergir, o usuário vê "cadastro completo", aperta Iniciar
e o servidor recusa dizendo que faltam três campos — sem dizer quais, porque o
número veio de outra conta.

Por isso ela é uma tradução linha a linha de `derive()` em
`src/cadastro/state/cadastroReducer.ts` e das funções de `src/cadastro/domain/`,
e não uma releitura do que "faz sentido cobrar". As regras, como estão lá:

  grupo 2 · cidade      `data_fim_concessao` e `unidade_cobertura`
            meta        `ano` e `cobertura_pct`
            faixa       `cobertura_pct` e `paridade`
  grupo 3 · sub-bacia   5 params (preço, tarr, ramp, vazão, potencial)
                        + 2 de população SE a cidade mede a meta por população
                        + 5 obras × 7 campos
  grupo 4 · ETE         6 campos-base + 2 (terreno, módulos) se for nova
  grupo 5 · CTS         igual à sub-bacia, com 4 obras

`wacc` NUNCA conta, nem na ficha, nem na obra, nem na ETE: vazio ali significa
"usa o WACC médio da unidade", que é uma resposta — não silêncio. Este parágrafo
já estava aqui e a lista `_ETE` abaixo cobrava `wacc` mesmo assim: o arquivo se
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
    completa o que o corpo omitir — ver `_obras_da_ficha`. A base literal só
    alcança componente que nunca existiu.)
"""

from typing import Any

from app.config import config
from app.infra import db

#: Campos de `params` que a ficha de coleta cobra sempre.
_PARAMS = [
    "preco_por_ligacao",
    "tempo_arrecadacao",
    "tempo_ramp_up",
    "vazao_contribuicao",
    "potencial_crescimento",
]
#: Só quando a cidade mede a meta por população.
_PARAMS_POP = ["universo_populacao", "populacao_atual"]

#: Campos de obra que a simulação exige. `wacc` fora, de propósito.
#: Quantas obras cada ficha TEM DE ter. E a base do cadastro (5 para sub-bacia,
#: 4 para CTS), e o que permite contar a obra AUSENTE — que nao aparece em
#: `componentes_*_capex` e por isso passava despercebida.
OBRAS_SUBBACIA = 5
OBRAS_CTS = 4

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
    cidades = f"""
        SELECT c.cidade_id,
               COALESCE(o.unidade_cobertura, '') = 'populacao' AS por_pop,
               (o.data_fim_concessao IS NULL)::int
             + (o.unidade_cobertura  IS NULL)::int AS pend
          FROM {_i()}.superintendencia_cidade c
          JOIN {_i()}.regional_superintendencia s USING (superintendencia_id)
          LEFT JOIN {_i()}.cidade_operacional o USING (cidade_id)
         WHERE s.unidade_id = $1
    """
    # A sub-bacia herda a régua da cidade do seu sistema — é por isso que a conta
    # dela não sai só da própria tabela.
    subs = f"""
        SELECT t.componente_sistema_id AS id, cid.por_pop
          FROM {_i()}.sistema_topologia t
          JOIN {_i()}.cidade_sistema cs USING (sistema_id)
          JOIN cidades cid ON cid.cidade_id = cs.cidade_id
    """

    linha = await db.buscar_um(
        f"""
        WITH cidades AS ({cidades}),
             comps AS ({subs}),
             sb AS (
                SELECT c.id, c.por_pop,
                       {_vazios(_PARAMS, "b")} AS pend_params,
                       CASE WHEN c.por_pop
                            THEN {_vazios(_PARAMS_POP, "b")} ELSE 0 END AS pend_pop
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
             ct AS (
                SELECT p.cts AS id, c.por_pop,
                       {_vazios(_PARAMS, "o")} AS pend_params,
                       CASE WHEN c.por_pop
                            THEN {_vazios(_PARAMS_POP, "o")} ELSE 0 END AS pend_pop
                  FROM {_i()}.subbacia_cts p
                  JOIN comps c ON c.id = p.sub_bacia
                  JOIN {_i()}.cts_operacional o ON o.cts = p.cts
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
          (SELECT COALESCE(SUM(pend), 0) FROM cidades)
            + (SELECT COALESCE(SUM(pend), 0) FROM mt)
            + (SELECT COALESCE(SUM(pend), 0) FROM fx)                       AS g2,
          (SELECT COALESCE(SUM(pend_params + pend_pop), 0) FROM sb)
            + (SELECT COALESCE(SUM(pend), 0) FROM sb_obras)                 AS g3,
          (SELECT COALESCE(SUM(pend_base + CASE WHEN nova THEN pend_nova ELSE 0 END), 0)
             FROM et)                                                       AS g4,
          (SELECT COALESCE(SUM(pend_params + pend_pop), 0) FROM ct)
            + (SELECT COALESCE(SUM(pend), 0) FROM ct_obras)                 AS g5,
          (SELECT count(*) * 2 FROM cidades)
            + (SELECT count(*) * {_CAMPOS_META} FROM mt)
            + (SELECT count(*) * {_CAMPOS_FAIXA} FROM fx)                   AS t2,
          (SELECT COALESCE(SUM({len(_PARAMS)} + CASE WHEN por_pop THEN {len(_PARAMS_POP)}
                                                     ELSE 0 END + 5 * {len(_OBRA)}), 0)
             FROM sb)                                                       AS t3,
          (SELECT COALESCE(SUM({len(_ETE)} + CASE WHEN nova THEN {len(_ETE_NOVA)}
                                                  ELSE 0 END), 0) FROM et)  AS t4,
          (SELECT COALESCE(SUM({len(_PARAMS)} + CASE WHEN por_pop THEN {len(_PARAMS_POP)}
                                                     ELSE 0 END + 4 * {len(_OBRA)}), 0)
             FROM ct)                                                       AS t5
        """,
        unidade_id,
    ) or {}

    grupos = {g: int(linha.get(g) or 0) for g in ("g2", "g3", "g4", "g5")}
    pendencias = sum(grupos.values())
    total = sum(int(linha.get(t) or 0) for t in ("t2", "t3", "t4", "t5"))
    # Sem nenhum campo a preencher, 100% — e não 0/0. É o mesmo critério que o hub
    # usa para liberar a simulação, e sem o guarda a tela mostrava "NaN%".
    completude = 100 if total == 0 else round((1 - pendencias / total) * 100)
    return {
        "pendencias": pendencias,
        "completude": completude,
        "porGrupo": {
            "contrato": grupos["g2"],
            "subBacias": grupos["g3"],
            "etes": grupos["g4"],
            "cts": grupos["g5"],
        },
        "faltando": await componentes_faltando(unidade_id),
    }


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
              FROM {_i()}.superintendencia_cidade c
              JOIN {_i()}.regional_superintendencia s USING (superintendencia_id)
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

        SELECT 'cts', p.cts, e.componente,
               'Falta o componente ' || e.componente || ' nesta CTS. '
               'A simulação da unidade fica travada até o cadastro ter os '
               'componentes, e o servidor recusa salvar a ficha assim.'
          FROM {_i()}.subbacia_cts p
          JOIN comps c ON c.id = p.sub_bacia
          JOIN {_i()}.cts_operacional o ON o.cts = p.cts
          CROSS JOIN esperado_cts e
         WHERE NOT EXISTS (
                SELECT 1 FROM {_i()}.componentes_cts_capex k
                 WHERE k.cts = p.cts AND k.componente = e.componente)

         ORDER BY 1, 2, 3
        """,
        unidade_id,
    )
