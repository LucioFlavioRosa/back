# Revisao da deduplicacao do POST /runs

Data da revisao: 2026-09-29.

Verificacao executada: `python -m pytest` no backend: **466 passed, 9 skipped**.

Escopo: revisei o commit `2085eb9` conforme pedido. Durante a revisao apareceram mudancas nao comitadas em `app/infra/repositorios/controle.py` e `migracoes/025_a_carga_carimba_o_resto.sql` que parecem enderecar o buraco principal; elas nao fazem parte do commit revisado e nao foram tratadas como base da conclusao abaixo.

## A conta das tabelas

Fonte 1: `pacote-motor-main/carregar_postgres.py`, `ABAS_INPUT` (linhas 34-61).  
Fonte 2: `pacote-motor-main/otimizador_capex_v62.py`, `ler_banco` (assinatura na linha 858 e leituras nas linhas citadas abaixo).  
Fonte 3: dedupe atual em `app/infra/repositorios/controle.py`, `_CADASTRO_ALTERADO_EM` (linhas 182-204), mais triggers da 024 (linhas 67, 71, 75 e 79).

Resumo: a dedupe cobre so quatro tabelas:

- `input.subbacia_operacional`
- `input.cts_operacional`
- `input.ete_capex`
- `input.cidade_operacional`

O motor carrega e consome mais que isso:

| Aba do motor | Tabela(s) de origem | `ler_banco` consome? | Coberta pela dedupe? | Veredito |
|---|---|---:|---:|---|
| `diretoria` | `input.diretoria` | Nao encontrei consumo em `ler_banco` | Nao | Sem efeito direto no motor hoje. |
| `unidade-regional` | `input.unidade_regional` | Sim, linhas 898-902 | Nao | **Defeito confirmado**: muda escopo, nomes e `wacc_medio` fallback. |
| `regional-superintendencia` | `input.empresa` | Sim, linha 903 | Nao | **Defeito confirmado**: muda quais cidades entram na unidade. |
| `superintendencia-cidade` | `input.cidade_empresa` + `input.cidade` | Sim, linha 905 | Nao | **Defeito confirmado**: muda cidades do escopo e nomes publicados. |
| `cidade-sistema` | `input.cidade_sistema` | Sim, linhas 912-915 | Nao | **Defeito confirmado**: muda sistemas/cidades do escopo. |
| `sistema-topologia` | `input.sistema_topologia` | Sim, linhas 1305-1314 | Nao | **Defeito confirmado**: muda nos, jusante, ETE do sistema e caminho ate a ETE. |
| `cidade-operacional` | `input.cidade_operacional` | Sim, linhas 1028-1030 | Sim | Coberta. Muda horizonte por fim de concessao. |
| `subbacia-operacional` | `input.subbacia_operacional` | Sim, linhas 921, 1040 e varios usos | Sim | Coberta. Muda demanda, receita, cobertura, CTS consolidada, vazao. |
| `componentes-subbacias-capex` | `input.componentes_subbacias_capex` | Sim, linha 1346 | Nao | **Defeito confirmado**: muda obras, CAPEX, WACC, prazos e overrides. |
| `ete-capex` | `input.ete_capex` | Sim, linha 1302 e bloco 1483+ | Sim | Coberta. Muda ETE e modulos. |
| `regional-operacional` | `input.regional_operacional` | Sim, linhas 1022-1026 | Nao | **Defeito confirmado**: muda `ano_base` e, portanto, janela/horizonte/calendario. |
| `metas-cobertura` | `input.metas_cobertura` | Sim, linhas 1687-1706 | Nao | **Defeito confirmado**: muda restricao/penalidade de cobertura. |
| `fator-esgoto` | `input.fator_esgoto` | Sim, linhas 1710-1726 | Nao | **Defeito confirmado**: muda paridade, receita e VPL. |
| `subbacia-cts` | `input.subbacia_cts` | Sim, linhas 1043 e 1160+ | Nao | **Defeito confirmado**: muda pareamento CTS/sub-bacia. |
| `cts-operacional` | `input.cts_operacional` | Sim, linhas 923 e 1042 | Sim | Coberta. Mas pode liberar rodada a toa quando a CTS alterada fica fora do recorte topologico da unidade. |
| `componentes-cts-capex` | `input.componentes_cts_capex` | Sim se `usar_cts=True`, linha 1348 | Nao | **Defeito confirmado**: muda obras/CAPEX/prazos/WACC de CTS. |
| `orcamento` | `input.orcamento` | Sim quando `ORCAMENTO` nao vem em params, linhas 1027 e 1327-1343 | Nao | Hoje o `POST /runs` sempre monta `ORCAMENTO`; ainda assim e entrada real para jobs legados/externos e fallback do motor. |

Conclusao da conta: **a cobertura nao esta completa**. O risco suspeitado existe: mudar tabelas lidas pelo motor e nao cobertas por `_CADASTRO_ALTERADO_EM` pode fazer `POST /runs` devolver 200 com uma rodada calculada sobre dados antigos.

## Defeitos confirmados

1. `app/infra/repositorios/controle.py:182` cobre so quatro fichas, mas o motor le outras tabelas que alteram resultado.

Cenario concreto: existe uma rodada publicada e bem-sucedida da unidade `uA3`, pedida depois da ultima alteracao das quatro fichas cobertas. Alguem atualiza `input.componentes_subbacias_capex.preco_unitario` ou `capex` de uma obra da unidade. O motor passa a montar `cap` por `quantidade * preco_unitario` ou `capex` em `otimizador_capex_v62.py:1346+`; a dedupe nao ve nenhuma data nova; repetir o mesmo pedido devolve 200 com o `runId` antigo. Entrada diferente, resultado antigo.

2. `migracoes/024_a_carga_carimba.sql:67,71,75,79` instala trigger so nas quatro fichas cobertas.

Cenario concreto: carga do Databricks reescreve `input.metas_cobertura` ou `input.fator_esgoto`. Nenhum `carregado_em` existe ali e nenhum trigger carimba. A rodada antiga continua elegivel, embora metas/paridade tenham mudado.

3. Tabelas de estrutura/topologia nao entram na data da dedupe.

Cenario concreto: alterar `input.sistema_topologia.componente_sistema_id_jusante` muda o caminho ate a ETE, logo quais transportes sao pre-requisitos para faturar (`caminho`/`requisitos` no motor). A dedupe nao ve a alteracao e reaproveita plano antigo.

4. Tabelas de hierarquia nao entram na data da dedupe.

Cenario concreto: alterar `input.cidade_empresa.emp_codigo` ou `input.empresa.unidade_id` move uma cidade/sistema para dentro ou fora da unidade. `ler_banco` recorta o escopo nas linhas 925-1001. A dedupe continua olhando so dados operacionais das quatro fichas que ainda casam com o recorte antigo.

5. `input.unidade_regional.wacc_medio` nao entra na data da dedupe.

Cenario concreto: componente/ETE sem `wacc` proprio usa fallback `_wacc_fb` a partir de `wacc_medio` lido em `unidade-regional` (linhas 898-902 e 1011-1020). Trocar o WACC medio muda desconto/VPL; a dedupe nao libera nova rodada.

6. `input.regional_operacional.ano_base` nao entra na data da dedupe.

Cenario concreto: alterar `ano_base` muda conversao de ano-calendario para ano-plano, horizonte e data automatica (`cal2py`, `_anobase`, linhas 1022, 1318-1325, 1561-1575). A mesma tela pode virar outra janela temporal sem liberar rodada.

## Veredito por tabela que sobra

- `unidade_regional`: muda resultado se alterar unidade/regional/nome usado no eixo, e muda VPL se alterar `wacc_medio` quando ha elementos sem WACC proprio.
- `empresa`: muda resultado se mover empresa entre unidades; muda escopo de cidades/sistemas.
- `cidade` / `cidade_empresa`: muda escopo e rotulos. Rotulo sozinho nao deveria mudar otimizacao, mas `cidade_empresa` muda quais cidades entram.
- `cidade_sistema`: muda resultado; sistema entra/sai do escopo, cidade de reserva muda, metas/paridade/horizonte podem mudar.
- `sistema_topologia`: muda resultado; altera nos, jusante, associacao da ETE ao sistema e caminhos de requisitos.
- `componentes_subbacias_capex`: muda resultado; altera obras, CAPEX, OPEX, prazos, obrigatoriedade/proibicao, quantidade/preco unitario e WACC.
- `regional_operacional`: muda resultado por `ano_base`.
- `metas_cobertura`: muda resultado quando ha peso/foco de cobertura ou diagnostico/publicacao de metas; mesmo sem peso, muda entrada publicada.
- `fator_esgoto`: muda resultado; altera paridade esgoto/agua e receita.
- `subbacia_cts`: muda resultado quando `USAR_CTS=True`; muda pareamento e regra de absorcao.
- `componentes_cts_capex`: muda resultado quando `USAR_CTS=True`; altera obras da CTS.
- `orcamento`: com o `POST /runs` atual, `ORCAMENTO` sempre vai nos params e a tabela nao e usada nesse caminho. Para jobs que omitem `ORCAMENTO`, muda resultado e deveria entrar numa regra geral de identidade do input.
- `diretoria`: carregada, mas nao encontrei consumo pelo motor. Nao parece mudar simulacao hoje.

## Inverso: libera rodada a toa

Existe risco menor de falso negativo na cobertura atual:

- `cts_operacional`: a dedupe so considera CTS que esta em `sistema_topologia` da unidade. Isso evita o bug antigo do par/cidade citado no comentario. Mas, se uma linha de CTS coberta pela unidade for alterada e a rodada estiver com `USAR_CTS=False`, essa CTS nao participa do cenario; a dedupe vai liberar rodada nova mesmo assim. E gasto de cluster, nao falso positivo.
- `cidade_operacional`: se alterar cidade da unidade que nao tem sistema/no efetivo no recorte, a data sobe e libera rodada sem efeito economico. Tambem e falso negativo.
- `subbacia_operacional` / `ete_capex`: pelo join com `comps`, tende a recortar por componentes topologicos. Se o componente esta topologicamente na unidade, a alteracao deve afetar ou ao menos estar no input do cenario.

## Outros eixos de diferenca

### Parametros do pedido

O `digest` calcula `json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)` em `controle.py:126-161`.

Cada um destes, mudado sozinho, muda o digest se a chave esta em `params`:

- `ANOS_EXTRA_CONCLUSAO`: hoje fixo em `0` por `montar_params` (`parametros.py:252`). Se um params antigo/manual diferir, libera.
- `BASE_RECEITA`: repasse direto (`parametros.py:203-213`). Libera.
- `COBERTURA_SO_RESIDENCIAL`: repasse direto. Libera.
- `CURVA_ADOCAO`: repasse direto. Libera.
- `DATA_INICIO`: repasse direto apos conversao por `mes_ano` quando usado. Libera se valor normalizado diferir.
- `FOCO_COBERTURA`: repasse direto. Libera.
- `MAX_TIME_S`: entra no `params` fixo em 1000 (`parametros.py:287`) e e `CHAVES_DO_JOB` (`parametros.py:58`, `job_databricks.py:73`). Se diferir em params, **libera hoje**.
- `ORCAMENTO`: entra no params. Libera se cronograma/soma/distribuicao diferir.
- `PENALIDADE_COBERTURA`: repasse direto. Libera.
- `UNIDADE`: entra no params e tambem filtra `WHERE r.unidade = $1`. Libera.
- `UNIDADE_COBERTURA`: repasse direto. Libera.
- `USAR_CTS`: repasse direto. Libera.
- `USUARIO`: entra no params. Libera por desenho de posse.
- `WORKERS`: nao entra no `POST /runs` normal (`parametros.py:264-265`), mas e aceito como chave de job. Se aparecer em params manual/variacao, muda o digest.

Minha opiniao de produto sobre `MAX_TIME_S` e `WORKERS`: se a promessa escrita for "entrada economica 100% igual", eles nao deveriam participar da dedupe de simulacao publicada, porque nao mudam cadastro, restricoes economicas ou objetivo; mudam esforco de busca. Mas ha uma ressalva pratica: `MAX_TIME_S` pode mudar o plano encontrado quando o solver para por tempo. Entao eu separaria a decisao:

- `WORKERS`: deveria ficar fora da identidade da simulação. E detalhe de execucao/infra.
- `MAX_TIME_S`: se o produto vende "mesma pergunta, mais/menos tempo de solver" como outra rodada comparavel, manter no digest. Se a pergunta for estritamente "dados de entrada economicos", tirar do digest e armazenar como metadado de execucao. Hoje o proprio codigo comenta que `MAX_TIME_S` e afinacao de execucao, mas inclui no digest via params.

### Parametro que o pedido nao carrega mas o motor usa

Confirmados:

- `ETE_FASEADA`: o backend nao coloca em `params`; comentario em `parametros.py:223-238` diz que o executor deve afirmar `True`, mas `job_databricks._params_para_ler_banco` so repassa chaves presentes e `ler_banco` defaulta `ete_faseada=False`. Suspeita: ha outro executor/camada que injeta esse valor, mas neste caminho `job_databricks.py` nao injeta.
- `ETE_FIXO`, `METAS_COBERTURA`, `PESO_COBERTURA`, `PESO_CIDADE`, `REGIONAL`: aceitos pelo job/motor, nao produzidos por `POST /runs`. A ausencia e regra de negocio documentada para alguns deles.
- `HORIZONTE_CAPEX`: so aparece no modo de `orcamento_anual`; nao aparece no cronograma por ano.
- `ORCAMENTO_TOTAL`: aparece so quando `redistribuir_orcamento=True`.

Defeito/suspeita importante: se algum executor preenche `ETE_FASEADA=True` por conta, esse valor nao participa do digest gravado pelo backend; duas rodadas com mesmo params e executor diferente poderiam ler input economico diferente. Precisa conferir o executor real em producao.

### ORCAMENTO com mesma soma e distribuicao diferente

Libera. `ORCAMENTO` e dicionario por ano; `digest` preserva valores por chave. `{2027: 80, 2028: 20}` e `{2027: 20, 2028: 80}` tem mesma soma, mas digest diferente. Isto esta correto: o motor usa teto anual e calcula `cen.anos_capex` pelo maior ano do cronograma.

Quando `redistribuir_orcamento=True`, a distribuicao original e deliberadamente achatada em `ORCAMENTO` e a soma fica em `ORCAMENTO_TOTAL` (`parametros.py:152-168`). Nesse modo, duas distribuicoes diferentes com mesma soma, mesmos anos e mesmo teto escolhido podem virar o mesmo params; isso e coerente com a semantica "deixe o otimizador distribuir", mas precisa estar coberto por teste para nao virar surpresa.

### Numeros iguais escritos diferente

Confirmado correto no caminho normal:

- `montar_params` converte valores de `orcamento` para `float` e ano para `int` (`parametros.py:118-123`).
- O job converte chaves JSON string de ano de volta para `int` em `_normalizar_orcamento` (`job_databricks.py:80-137`).
- `json.dumps` diferencia tipo, mas depois da normalizacao do backend `80000000`, `80000000.00` e `8e7` viram `80000000.0`.

Formas de escapar:

- chamar `digest` diretamente com params crus, sem passar por `montar_params`;
- colocar `Decimal` dentro de params manualmente: `json.dumps(..., default=str)` transforma em string e pode diferir de float;
- usar strings numericas em outras chaves aceitas que nao sao normalizadas antes do digest, por exemplo `FOCO_COBERTURA` se vier como `"0.5"` versus `0.5`. `_validar` valida, mas nao reatribui o float normalizado.

### Janela entre disparo e leitura do cadastro

A dedupe compara `run_request.solicitado_em` com a ultima alteracao do cadastro (`controle.py:259`). O comentario diz que `solicitado_em` e "o instante em que a rodada começou a ler o cadastro", mas o job le o cadastro depois: ele primeiro le request, marca `RODANDO`, chama `abas_do_postgres`, e so entao `ler_banco` (`job_databricks.py:177-188`).

Dois casos:

- Alteracao feita durante a execucao depois da leitura real do cadastro: liberar nova rodada esta certo.
- Alteracao feita entre `solicitado_em` e a leitura real do cadastro: a rodada antiga pode ter lido o dado novo, mas `solicitado_em` fica anterior a alteracao. A dedupe libera rodada nova a toa. Isso e falso negativo, custo de cluster.

Nao encontrei uma janela que gere falso positivo por causa dessa escolha de timestamp. Para falso positivo acontecer, a rodada antiga teria de ler dado novo enquanto a comparacao acredita que ela e posterior a alteracao; com `solicitado_em`, o efeito vai para o lado conservador.

## Os testes que sugiro

Helpers para os testes com banco real:

```python
import os
import asyncpg
import pytest

from app.infra.repositorios import controle


def _banco_disponivel():
    return bool(os.environ.get("POSTGRES_URL", "").endswith("/otimizador"))


async def rodada_publicada_mais_recente(con):
    base = await con.fetchrow("""
        SELECT r.run_id, r.unidade, r.params
          FROM controle.run_request r
          JOIN controle.run_status s USING (run_id)
          JOIN public.otim_meta m USING (run_id)
         WHERE s.status = 'SUCESSO'
           AND r.params ? 'ORCAMENTO'
         ORDER BY r.solicitado_em DESC
         LIMIT 1
    """)
    assert base, "preciso de ao menos uma rodada publicada para testar dedupe"
    return base


@pytest.fixture
async def con_real():
    con = await asyncpg.connect(os.environ["POSTGRES_URL"])
    try:
        yield con
    finally:
        await con.close()
```

### 1. Contrato sem banco: toda aba consumida pelo motor precisa estar coberta ou justificada

Impede: adicionar/continuar tabela de input consumida pelo motor sem decidir se ela invalida dedupe.

Tipo: sem banco.

```python
def test_toda_tabela_consumida_pelo_motor_esta_na_regua_da_dedupe_ou_justificada():
    from pathlib import Path

    motor = Path(r"C:\Users\LúcioFláviodosSantos\projetos\pacote-motor-main\otimizador_capex_v62.py").read_text(encoding="utf-8")
    controle = Path("app/infra/repositorios/controle.py").read_text(encoding="utf-8")

    consumidas = {
        "unidade_regional",
        "empresa",
        "cidade_empresa",
        "cidade",
        "cidade_sistema",
        "sistema_topologia",
        "cidade_operacional",
        "subbacia_operacional",
        "componentes_subbacias_capex",
        "ete_capex",
        "regional_operacional",
        "metas_cobertura",
        "fator_esgoto",
        "subbacia_cts",
        "cts_operacional",
        "componentes_cts_capex",
    }
    justificadas_sem_efeito = {"diretoria"}
    cobertas = {t for t in consumidas if t in controle}

    assert consumidas - justificadas_sem_efeito <= cobertas
```

Observacao: esse teste vai falhar hoje; e bom. Depois da correcao, troque a expectativa para a lista real coberta.

### 2. Banco real: alteracao em `componentes_subbacias_capex` invalida rodada publicada

Impede: devolver plano antigo quando CAPEX/obra mudou.

Tipo: banco real, em transacao com rollback.

```python
@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real")
@pytest.mark.asyncio
async def test_dedupe_nao_reaproveita_publicada_quando_componente_de_obra_mudou(con_real):
    tx = con_real.transaction()
    await tx.start()
    try:
        base = await rodada_publicada_mais_recente(con_real)
        componente = await con_real.fetchrow("""
            WITH sistemas AS (
                SELECT DISTINCT cs.sistema_id
                  FROM input.cidade_sistema cs
                  JOIN input.cidade_empresa ce ON ce.cidade_id = cs.cidade_id
                  JOIN input.empresa e ON e.emp_codigo = ce.emp_codigo
                 WHERE e.unidade_id = $1
            )
            SELECT c.sub_bacia, c.componente
              FROM input.componentes_subbacias_capex c
              JOIN input.sistema_topologia t ON t.componente_sistema_id = c.sub_bacia
              JOIN sistemas s USING (sistema_id)
             LIMIT 1
        """, base["unidade"])
        assert componente

        await con_real.execute("""
            UPDATE input.componentes_subbacias_capex
               SET capex = COALESCE(capex, 0) + 1
             WHERE sub_bacia = $1 AND componente = $2
        """, componente["sub_bacia"], componente["componente"])

        achada = await controle.rodada_identica(con_real, base["unidade"], dict(base["params"]))
        assert achada is None
    finally:
        await tx.rollback()
```

### 3. Banco real: alteracao em `metas_cobertura` invalida rodada publicada

Impede: reaproveitar rodada quando meta contratual mudou.

Tipo: banco real, rollback.

```python
@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real")
@pytest.mark.asyncio
async def test_dedupe_nao_reaproveita_publicada_quando_meta_de_cobertura_mudou(con_real):
    tx = con_real.transaction()
    await tx.start()
    try:
        base = await rodada_publicada_mais_recente(con_real)
        meta = await con_real.fetchrow("""
            SELECT m.cidade_id, m.ano
              FROM input.metas_cobertura m
              JOIN input.cidade_empresa ce USING (cidade_id)
              JOIN input.empresa e ON e.emp_codigo = ce.emp_codigo
             WHERE e.unidade_id = $1
             LIMIT 1
        """, base["unidade"])
        assert meta

        await con_real.execute("""
            UPDATE input.metas_cobertura
               SET cobertura_pct = LEAST(COALESCE(cobertura_pct, 0) + 0.01, 100)
             WHERE cidade_id = $1 AND ano = $2
        """, meta["cidade_id"], meta["ano"])

        assert await controle.rodada_identica(con_real, base["unidade"], dict(base["params"])) is None
    finally:
        await tx.rollback()
```

### 4. Banco real: alteracao em `fator_esgoto` invalida rodada publicada

Impede: reaproveitar rodada quando paridade/tarifa de esgoto mudou.

Tipo: banco real, rollback.

```python
@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real")
@pytest.mark.asyncio
async def test_dedupe_nao_reaproveita_publicada_quando_paridade_mudou(con_real):
    tx = con_real.transaction()
    await tx.start()
    try:
        base = await rodada_publicada_mais_recente(con_real)
        faixa = await con_real.fetchrow("""
            SELECT f.cidade_id, f.cobertura_pct
              FROM input.fator_esgoto f
              JOIN input.cidade_empresa ce USING (cidade_id)
              JOIN input.empresa e ON e.emp_codigo = ce.emp_codigo
             WHERE e.unidade_id = $1
             LIMIT 1
        """, base["unidade"])
        assert faixa

        await con_real.execute("""
            UPDATE input.fator_esgoto
               SET paridade = COALESCE(paridade, 1) + 0.01
             WHERE cidade_id = $1 AND cobertura_pct = $2
        """, faixa["cidade_id"], faixa["cobertura_pct"])

        assert await controle.rodada_identica(con_real, base["unidade"], dict(base["params"])) is None
    finally:
        await tx.rollback()
```

### 5. Banco real: alteracao em `sistema_topologia` invalida rodada publicada

Impede: reaproveitar rodada quando caminho/topologia mudou.

Tipo: banco real, rollback.

```python
@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real")
@pytest.mark.asyncio
async def test_dedupe_nao_reaproveita_publicada_quando_topologia_mudou(con_real):
    tx = con_real.transaction()
    await tx.start()
    try:
        base = await rodada_publicada_mais_recente(con_real)
        no = await con_real.fetchrow("""
            SELECT t.componente_sistema_id
              FROM input.sistema_topologia t
              JOIN input.cidade_sistema cs USING (sistema_id)
              JOIN input.cidade_empresa ce USING (cidade_id)
              JOIN input.empresa e ON e.emp_codigo = ce.emp_codigo
             WHERE e.unidade_id = $1
               AND t.componente_sistema_id_jusante IS NOT NULL
             LIMIT 1
        """, base["unidade"])
        assert no

        await con_real.execute("""
            UPDATE input.sistema_topologia
               SET componente_sistema_id_jusante = NULL
             WHERE componente_sistema_id = $1
        """, no["componente_sistema_id"])

        assert await controle.rodada_identica(con_real, base["unidade"], dict(base["params"])) is None
    finally:
        await tx.rollback()
```

Observacao: o update muda valor de verdade e fica dentro do rollback.

### 6. Banco real: alteracao em `regional_operacional.ano_base` invalida rodada publicada

Impede: reaproveitar rodada quando calendario/janela mudou.

Tipo: banco real, rollback.

```python
@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real")
@pytest.mark.asyncio
async def test_dedupe_nao_reaproveita_publicada_quando_ano_base_mudou(con_real):
    tx = con_real.transaction()
    await tx.start()
    try:
        base = await rodada_publicada_mais_recente(con_real)
        reg = await con_real.fetchval(
            "SELECT regional_id FROM input.unidade_regional WHERE unidade_id = $1",
            base["unidade"],
        )
        assert reg

        await con_real.execute("""
            UPDATE input.regional_operacional
               SET ano_base = COALESCE(ano_base, 2026) + 1
             WHERE regional_id = $1
        """, reg)

        assert await controle.rodada_identica(con_real, base["unidade"], dict(base["params"])) is None
    finally:
        await tx.rollback()
```

### 7. Banco real: alteracao em `unidade_regional.wacc_medio` invalida rodada publicada quando ha fallback

Impede: reaproveitar rodada quando WACC de fallback mudou.

Tipo: banco real, rollback.

```python
@pytest.mark.skipif(not _banco_disponivel(), reason="sem banco real")
@pytest.mark.asyncio
async def test_dedupe_nao_reaproveita_publicada_quando_wacc_medio_mudou(con_real):
    tx = con_real.transaction()
    await tx.start()
    try:
        base = await rodada_publicada_mais_recente(con_real)
        await con_real.execute("""
            UPDATE input.unidade_regional
               SET wacc_medio = COALESCE(wacc_medio, 0.1) + 0.001
             WHERE unidade_id = $1
        """, base["unidade"])

        assert await controle.rodada_identica(con_real, base["unidade"], dict(base["params"])) is None
    finally:
        await tx.rollback()
```

### 8. Sem banco: os 13 parametros diferenciam digest, exceto o que o produto decidir excluir

Impede: parametro economico novo ou existente sumir da identidade.

Tipo: sem banco.

```python
@pytest.mark.parametrize("chave, valor", [
    ("ANOS_EXTRA_CONCLUSAO", 1),
    ("BASE_RECEITA", "faturada"),
    ("COBERTURA_SO_RESIDENCIAL", True),
    ("CURVA_ADOCAO", "linear"),
    ("DATA_INICIO", (2, 2027)),
    ("FOCO_COBERTURA", 0.7),
    ("MAX_TIME_S", 500),
    ("ORCAMENTO", {2027: 10.0, 2028: 90.0}),
    ("PENALIDADE_COBERTURA", "ligacao"),
    ("UNIDADE", "uA2"),
    ("UNIDADE_COBERTURA", "economias"),
    ("USAR_CTS", False),
    ("USUARIO", "outra@aegea"),
])
def test_cada_parametro_de_entrada_muda_digest(chave, valor):
    base = {
        "UNIDADE": "uA1",
        "USUARIO": "ana@aegea",
        "ORCAMENTO": {2027: 80_000_000.0},
        "BASE_RECEITA": "arrecadada",
        "USAR_CTS": True,
        "ANOS_EXTRA_CONCLUSAO": 0,
        "MAX_TIME_S": 1000,
    }
    alterado = {**base, chave: valor}
    assert digest(base) != digest(alterado)
```

### 9. Sem banco: `WORKERS` nao deve participar se for decisao de produto

Impede: paralelismo de maquina gerar rodada duplicada.

Tipo: sem banco. Este teste depende da decisao de produto; hoje ele falharia se `WORKERS` entrar em params.

```python
def digest_de_entrada(params):
    return digest({k: v for k, v in params.items() if k not in {"WORKERS"}})

def test_workers_nao_muda_identidade_da_simulacao():
    base = {"UNIDADE": "uA1", "USUARIO": "ana", "ORCAMENTO": {2027: 1.0}, "WORKERS": 8}
    assert digest_de_entrada(base) == digest_de_entrada({**base, "WORKERS": 16})
```

### 10. Sem banco: distribuicao anual do orcamento muda digest mesmo com mesma soma

Impede: colapsar cronograma por soma e reaproveitar rodada com tetos anuais diferentes.

Tipo: sem banco.

```python
def test_orcamento_mesma_soma_distribuicao_diferente_nao_deduplica():
    base = {"UNIDADE": "uA1", "USUARIO": "ana", "ORCAMENTO": {2027: 80.0, 2028: 20.0}}
    outro = {"UNIDADE": "uA1", "USUARIO": "ana", "ORCAMENTO": {2027: 20.0, 2028: 80.0}}
    assert digest(base) != digest(outro)
```

### 11. Sem banco: normalizacao numerica do corpo antes do digest

Impede: `80000000`, `80000000.00`, `8e7` ou ano string/int virarem rodadas diferentes.

Tipo: sem banco.

```python
def test_montar_params_normaliza_orcamento_numericamente():
    a = montar_params({"orcamento": {"2027": 80000000}}, "uA1", "ana")
    b = montar_params({"orcamento": {2027: 80000000.00}}, "uA1", "ana")
    c = montar_params({"orcamento": {"2027": "8e7"}}, "uA1", "ana")
    assert digest(a) == digest(b) == digest(c)
```

### 12. Sem banco: string numerica em parametro validado nao deve escapar

Impede: `"0.5"` e `0.5` em `FOCO_COBERTURA` virarem entradas diferentes se o produto considerar equivalentes.

Tipo: sem banco. Hoje tende a falhar porque `_validar` nao grava o float convertido.

```python
def test_foco_cobertura_numericamente_igual_normaliza_antes_do_digest():
    a = montar_params({"orcamento": {"2027": 1}, "foco_cobertura": "0.5"}, "uA1", "ana")
    b = montar_params({"orcamento": {"2027": 1}, "foco_cobertura": 0.5}, "uA1", "ana")
    assert digest(a) == digest(b)
```

### 13. Banco real: alteracao entre `solicitado_em` e leitura real deve ser documentada como conservadora

Impede: alguem "corrigir" para `publicado_em` e criar falso positivo.

Tipo: banco real ou teste de contrato sem banco sobre SQL/comentario. Sugestao simples sem banco:

```python
def test_dedupe_usa_solicitado_em_e_nao_publicacao_para_ser_conservadora():
    fonte = Path("app/infra/repositorios/controle.py").read_text(encoding="utf-8")
    assert "r.solicitado_em > COALESCE" in fonte
    assert "otim_meta" in fonte
```

## Suspeitas

- Suspeita: `ETE_FASEADA` e afirmado por algum executor real fora de `job_databricks.py`. Se sim, ele muda input do motor sem aparecer no digest do backend.
- Suspeita: algumas alteracoes de hierarquia podem mudar so rotulo, nao plano; mesmo assim, como a entrada publicada/auditada muda, eu trataria como invalidadora ate haver uma lista explicita de colunas cosmeticas.
- Suspeita: usar `now()` no trigger e correto para carga em transacao, mas se a carga faz muitas transacoes pequenas, a data vira por lote. Isso nao quebra a regra; so torna o diagnostico menos "uma carga, uma data".

## O que verifiquei e esta correto

- A 024 carimba `carregado_em` nas quatro tabelas que ela pretende cobrir e nao mexe em `atualizado_em`.
- `_CADASTRO_ALTERADO_EM` usa `GREATEST(atualizado_em, carregado_em)` nas quatro fichas.
- A dedupe de concluida exige `SUCESSO`, publicacao em `otim_meta` e `solicitado_em` posterior a alteracao coberta.
- O digest inclui `USUARIO`, impedindo devolver rodada de outra pessoa.
- Ordem de chaves nao muda digest.
- `ORCAMENTO` por ano e normalizado no caminho normal do `POST /runs`.
- A comparacao por `solicitado_em` e conservadora em relacao a alteracoes durante a execucao: pode rodar a mais, nao identifiquei falso positivo por essa janela.
