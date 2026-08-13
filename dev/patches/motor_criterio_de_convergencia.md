# Motor: o solver gasta o teto inteiro PROVANDO o que já achou

**Para quem mantém o `Otimizador_CAPEX_v62_pacote`.** Este arquivo descreve uma
melhoria no pacote e a mudança sugerida. **Nada foi aplicado** — o pacote não é
versionado por nós, e uma edição local se perderia na próxima atualização dele.

Arquivo: `otimizador_capex_cpsat63.py`, função `resolver_por_sistema`.
Linhas conferem com o `rev11`.

## O sintoma

Rodadas da unidade de 67 cidades terminam com
`VIAVEL(limite de tempo)`, sugerindo que faltou tempo. Não faltou: o plano
devolvido está a **0,006% do limite superior provado**. O solver achou a resposta
e gastou o resto do orçamento tentando *provar* que ela era ótima.

Subir `MAX_TIME_S` de 1000 para 5000 não melhorou o resultado na proporção do
custo, porque o gargalo dessas rodadas é o orçamento de CAPEX, não o tempo.

## A medição

Instrumentação por fora do pacote (envolvendo `CpSolver.Solve`, sem alterar o
motor), unidade `uA3`, `MAX_TIME_S=1200`, verba de 50 Mi/ano:

```
fase 1 (obrigatórias)   limite 420s   gastou   1s   OTIMO    desperdício   0s
fase 2 (metas)          limite 480s   gastou   1s   OTIMO    desperdício   0s
fase 3 (cobertura/VPL)  limite 720s   gastou 720s   VIAVEL   desperdício 339s
```

**As duas primeiras fases não são o problema** — objetivos inteiros pequenos
(104, 73), provados em 1 segundo mesmo com 67 cidades. Toda a dificuldade está
na fase 3.

Trajetória da fase 3, com o gap entre a solução e o limite superior:

```
t=  24,1s   obj=579.577   bound=581.832   gap=0,39%
t=  42,3s   obj=580.809   bound=581.785   gap=0,17%
t=  59,1s   obj=580.846   bound=581.785   gap=0,16%
t= 380,7s   obj=581.748   bound=581.785   gap=0,01%
t= 720,0s   (nenhuma melhoria; para por relógio, devolve FEASIBLE)
```

Ou seja: **47% do tempo de solver correu depois da última melhoria**, e os
últimos 339s não produziram nada além da tentativa de fechar 0,01%.

Em unidades menores nada disso acontece — `uA1` (5 cidades) prova as três fases
em 1,2s no total, com `MAX_TIME_S=5000`. O teto alto não custa nada lá.

## A causa

Nenhum dos quatro `CpSolver` criados em `resolver_por_sistema` define critério de
parada por convergência:

```python
sv=cp_model.CpSolver(); sv.parameters.max_time_in_seconds=...; sv.parameters.num_search_workers=int(workers)
```

(linhas 513, 522, 526, 534, 540)

Sem `relative_gap_limit`, o CP-SAT só para em dois casos: prova de otimalidade
(gap exatamente zero) ou relógio. Para um objetivo com muitos ótimos equivalentes
— como a soma de coberturas arredondadas a inteiro — fechar o último décimo de
por cento pode custar ordens de grandeza mais que achar a solução.

## A correção sugerida

Um parâmetro novo, com default que **preserva o comportamento de hoje**:

```python
def resolver_por_sistema(cen, max_time_s=60, workers=8, verbose=True,
                         col_time_s=5, col_grid=12, gap_relativo=0.0):
    """...
    gap_relativo: para a busca quando a solução está comprovadamente a menos de
    `gap_relativo` do ótimo (0.005 = 0,5%). 0.0 (default) mantém o comportamento
    atual: só para por prova exata ou por relógio."""
```

e, em cada `CpSolver` criado na função:

```python
sv=cp_model.CpSolver()
sv.parameters.max_time_in_seconds=...
sv.parameters.num_search_workers=int(workers)
if gap_relativo > 0: sv.parameters.relative_gap_limit = float(gap_relativo)
```

`MAX_TIME_S` continua sendo o teto — o que muda é que ele deixa de ser também o
alvo.

### Efeito medido, na fase 3 da `uA3`

| `gap_relativo` | pararia em | economia | custo no objetivo |
|---|---|---|---|
| 0,001 (0,1%) | ~381s | 339s (47%) | 0,006% |
| 0,005 (0,5%) | ~24s | 696s (97%) | 0,37% |
| 0,01 (1%) | ~24s | 696s (97%) | 0,37% |

A escolha do valor é decisão de produto, não de engenharia: 0,5% devolve o plano
em 24s em vez de 720s, abrindo mão de 0,37% da cobertura.

### Efeito colateral bom: o status deixa de mentir

O CP-SAT devolve **`OPTIMAL`** quando para por `relative_gap_limit`, e
`FEASIBLE` quando para por relógio. Verificado (ortools 9.15.6755, mochila 0/1
com pesos correlacionados, 400 itens):

```
gap_limit desligado  ->  FEASIBLE  120,08s   (gap real 0,0000%)
gap_limit 0,0001     ->  OPTIMAL     0,14s   (gap real 0,0032%)
gap_limit 0,001      ->  OPTIMAL     0,10s   (gap real 0,0275%)
```

A primeira linha é a patologia inteira num caso mínimo: solução essencialmente
ótima, teto de tempo gasto por completo, e status que se lê como "faltou tempo".

## Sobre a repartição do tempo entre as fases

Fica o registro, sem proposta: `max_time_s` é repartido em frações fixas —
0,35 na fase 0, 0,4 na fase 1, 0,6 na fase 2 (linhas 513, 526, 534). A soma é
**1,35×** o valor pedido, então `MAX_TIME_S=5000` autoriza até 1h52 de solver, e
não 1h23. As duas primeiras fases provam em segundos e devolvem o saldo, mas o
teto nominal não é o que o nome sugere.

## Enquanto o patch não existe

Dá para obter o mesmo efeito de fora, sem tocar no pacote: `dev/worker.py` pode
envolver `cp_model.CpSolver` antes de chamar `resolver_por_sistema` e definir
`relative_gap_limit` em cada instância. Funciona (foi assim que a medição acima
foi feita), mas atinge **todos** os solvers do processo, inclusive os da geração
de colunas quando `ete_faseada=False` — que hoje não é o nosso caso, e passaria a
ser um acoplamento silencioso se algum dia for. Preferível como medida temporária
declarada, não como solução.
