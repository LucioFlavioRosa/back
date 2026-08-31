# Motor: obra de terceiro sai sem data de início, e ignora restrição de início

**Para quem mantém o `Otimizador_CAPEX_v62_pacote`.** Este arquivo descreve dois
defeitos do pacote e a correção sugerida. **Nada foi aplicado** — o pacote não é
versionado por nós, e uma edição local se perderia na próxima atualização dele.

Arquivos: `persistencia.py` e `otimizador_capex_v62.py`.
Números de linha conferem com o `rev11`.

## Contexto

A classificação é do próprio pacote (`otimizador_capex_v62.py:13`):

```
capex>0                -> AEGEA (investe e executa; leva prazo).
capex=0 e prazo>0      -> TERCEIRO (ex.: prefeitura): nao investe, mas leva prazo.
capex=0 e prazo=0      -> NAO NECESSARIA (ja pronta).
```

Obra de terceiro **trava faturamento**: a sub-bacia só fatura quando todas as
obras necessárias da cadeia estão prontas, e o `ready` de terceiro entra no
`max()` que define esse marco (`chain_last`, mesma função `avaliar`). O próprio
motor grava o motivo: *"Executada por terceiro (prazo 16m, sem CAPEX Aegea).
Entra na cadeia como pré-requisito"*.

## Defeito 1 — `mes_inicio`/`data_inicio` saem nulos

`persistencia.py:175`:

```python
for oid, o in cen.obras.items():
    y = plano.get(oid)
    ...
    "mes_inicio": y, "data_inicio": _data(cen, y),
    "mes_pronta": ready.get(oid), "data_pronta": _data(cen, ready.get(oid)),
    "construida": y is not None,
```

`plano` é o mapa de decisão do CP-SAT, e ele **só contém obra da Aegea** —
terceiro não é decidido pelo otimizador. Então `y` é `None` e a linha sai com
`mes_inicio`/`data_inicio` nulos e `construida=False`, embora `mes_pronta` venha
preenchido (de `_ready`).

O início existe e é dedutível: `_ready` devolve `o.prazo` para terceiro
(`otimizador_capex_v62.py:186`), o que equivale a **começar no mês 0**. Medido em
`run_20260814_153912_85cbc6`: nas 227 obras de terceiro, `mes_pronta ==
prazo_meses` exatamente (delta mín = máx = 0), enquanto nas 77 da Aegea vale
`mes_pronta == mes_inicio + prazo_meses`. Em 58 rodadas e 22.747 obras
agendadas, **nenhuma** obra de terceiro tem `data_inicio`.

Correção sugerida — o início é 0 para quem não é decidido pelo plano:

```python
y = plano.get(oid)
if y is None and o.responsavel == "Terceiro" and o.necessaria:
    y = 0                       # o mesmo mes que `_ready` ja assume
...
"construida": plano.get(oid) is not None,   # NAO derivar de `y`
```

A última linha é o cuidado que faz a correção não estragar outra coisa:
`construida` precisa continuar significando "a Aegea vai executar", senão obra de
terceiro passa a contar como construída no painel e nas metas.

## Defeito 2 — restrição de início não vale para terceiro

`otimizador_capex_v62.py:186`:

```python
def _ready(cen,o,plano):
    if not o.necessaria: return 0
    if o.responsavel=="Terceiro": return o.prazo   # meses de execucao
    y=plano.get(o.id); return (y+o.prazo) if y is not None else None
```

O retorno é `o.prazo` **sem olhar `inicio_min_mes`, `proibida_ate` ou
`proibida_nunca`**. Ou seja: se o cadastro disser "a prefeitura só pode começar
em 2028", o motor ignora e segue assumindo início imediato — e como esse `ready`
entra no marco de faturamento, a receita da sub-bacia é antecipada.

Hoje o defeito é latente: nas 227 obras de terceiro da rodada medida, nenhuma usa
esses campos (0 com `inicio_min_mes > 0`, 0 com `proibida_ate > 0`, 0 com
`proibida_nunca`). Mas os campos existem no schema e são honrados para a Aegea,
então a assimetria é silenciosa — quem preencher vai supor que valeu.

Correção sugerida — e ela é menor do que parece, porque o pacote JÁ calcula o
mês mais cedo de início. `Obra.__post_init__` (linha 47) faz:

```python
self.inicio_min = (10**7 if self.proibida_nunca else max(int(self.prazo_inicio), _proib))
```

isto é, `inicio_min` já incorpora `proibida_nunca`, `proibida_ate` e o prazo de
partida — e a linha 1279 ainda o desloca pelo mês-base do plano. Então basta
usá-lo, em vez de rederivar das colunas persistidas:

```python
if o.responsavel=="Terceiro":
    if o.inicio_min >= 10**7: return None      # proibida_nunca
    return o.inicio_min + o.prazo
```

Repare que isto também torna o caso comum idêntico ao de hoje: sem restrição
nenhuma, `inicio_min` é 0 e o retorno continua sendo `o.prazo`. A mudança só
aparece quando alguém preenche uma restrição que hoje é ignorada em silêncio.

## O que fizemos deste lado, enquanto isso

Nada que dependa da correção. O backend passou a ancorar a obra de terceiro na
**conclusão** (`data_pronta`), que é a única data que o motor de fato calcula
para ela — em `nivel_global.cronograma_de_obras` e no filtro de ano de
`nivel_detalhe.obras`. Se o pacote passar a gravar `data_inicio` para terceiro,
as duas consultas continuam corretas: a série da Aegea exclui terceiro
explicitamente, então não há risco de a mesma obra ser contada em dois anos.

O defeito 2 **não tem contorno do nosso lado** — é decisão dentro da função
objetivo, e afeta o marco de faturamento que o motor devolve pronto.
