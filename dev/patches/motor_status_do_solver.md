# Motor: `KeyError` no reparo do teto anual

**Para quem mantém o `Otimizador_CAPEX_v62_pacote`.** Este arquivo descreve um
defeito no pacote e a correção sugerida. **Nada foi aplicado** — o pacote não é
versionado por nós, e uma edição local se perderia na próxima atualização dele.

Arquivo: `otimizador_capex_cpsat63.py`, função `resolver_por_sistema`.
Números de linha conferem com o `rev11`.

## O sintoma

```
File "otimizador_capex_cpsat63.py", line 577, in resolver_por_sistema
    cur=cols[g][sel[g]][1]
                ~~~^^^
KeyError: 'Araruama Leste1'
```

Observado duas vezes numa unidade de 67 cidades / 474 sistemas / 8079 obras, com
janela de CAPEX de 8 anos e `max_time_s=45`. Uma terceira rodada, igual em tudo
menos a janela (6 anos), concluiu normalmente.

**O nome da cidade é irrelevante.** Não é encoding nem formatação: é a primeira
cidade ausente na ordem de iteração, e por isso muda entre execuções
(`Araruama Leste1` numa, `Araruama Leste3` na outra).

## A mecânica

`sel_final` só recebe a cidade quando o master selecionou alguma coluna para ela:

```python
sel_final = {}                                   # linha 473
def _extrai(sv, y):
    sel_final.clear()
    for g in grupos:
        for j, yv in enumerate(y[g]):
            if sv.Value(yv) == 1:                # ← só entra se houver seleção
                sel_final[g] = j
```

O reparo do teto anual copia esse dicionário e depois percorre **todas** as
cidades:

```python
sel = dict(sel_final)                            # linha 568
for g, j in sel.items():                         # linha 570 — seguro
    ...
while E > 1.0 and len(rep) < 200:
    for g in grupos:                             # linha 575 — TODAS as cidades
        cur = cols[g][sel[g]][1]                 # linha 577 — estoura nas ausentes
```

Os dois laços discordam sobre o mesmo conjunto. O de cima itera o que existe; o
de baixo assume que existe tudo.

## Por que `sel` fica parcial

O modelo **exige** uma coluna por cidade — `md.AddExactlyOne(yy)`, linha 455 — e
toda cidade tem ao menos a coluna "nada". Ou seja: com uma solução válida,
`sel_final` seria completo e o `KeyError` seria impossível.

O que não está garantido é que haja solução válida quando `_extrai` é chamado.
Nas três chamadas, o status do `Solve()` é tratado de forma incompleta:

| linha | trecho                                                       | tratamento          |
| ----- | ------------------------------------------------------------ | ------------------- |
| 523   | `st=sv.Solve(md); return _extrai(sv,y),st,"-",5,O0`          | **nenhum**          |
| 537   | `if st==cp_model.INFEASIBLE: return None,...` → `_extrai(...)` | só `INFEASIBLE`     |
| 543   | idem                                                         | só `INFEASIBLE`     |

`UNKNOWN` — que é o que o CP-SAT devolve ao estourar o tempo sem achar solução
viável — passa por esses filtros e chega em `_extrai`.

Isso também explica a correlação com a janela: 8 anos gera mais colunas e mais
restrições anuais que 6, com o mesmo orçamento de tempo (`max_time_s=45`, sendo
40% para a primeira fase do lexicográfico e 60% para a segunda). O problema maior
é o que não fecha no tempo.

> **Ressalva honesta.** Os defeitos acima são certos, por leitura do código. O
> caminho exato entre "o `Solve()` voltou sem solução" e "`sel_final` ficou
> PARCIAL em vez de vazio" **não foi reproduzido** — com `sel_final` vazio, o
> `if not ok and sel_final:` da linha 558 nem entraria no reparo. Pode haver um
> terceiro fator. A correção sugerida é defensiva nas duas camadas justamente por
> isso: uma delas fecha o buraco mesmo que o mecanismo seja outro.

## Correção sugerida

**1. Não extrair de um solve sem solução.** Nas três chamadas, trocar o filtro de
`INFEASIBLE` por uma verificação positiva:

```python
# linha 523
st = sv.Solve(md)
if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    return None, st, "-", 5, O0
return _extrai(sv, y), st, "-", 5, O0

# linhas 535-537
st = sv.Solve(md2)
if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    return None, st, Mstar, _idx2, O0
return _extrai(sv, y2), st, Mstar, _idx2, O0

# linhas 541-543
st = sv.Solve(md)
if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    return None, st, None, 0, O0
return _extrai(sv, y), st, None, 0, O0
```

A linha 235 do mesmo arquivo já usa exatamente esse critério
(`ok = st in (cp_model.OPTIMAL, cp_model.FEASIBLE)`) — a correção alinha o resto
do arquivo com ele. O `plano is None` que passa a acontecer já tem tratamento na
linha 546 (`if plano is None: plano={oid:None for oid in cen.obras}`), com o
comentário "guarda: nunca deixa avaliar quebrar".

**2. Falhar alto se a seleção vier incompleta**, logo após a linha 568:

```python
sel = dict(sel_final)
faltantes = [g for g in grupos if g not in sel]
if faltantes:
    raise RuntimeError(
        f"selecao incompleta apos o solve: {len(faltantes)} cidade(s) sem coluna "
        f"(ex.: {faltantes[:3]}). O modelo usa AddExactlyOne, entao isto indica "
        f"extracao a partir de um solve sem solucao valida."
    )
```

Isto é rede de segurança, não a correção: se o item 1 estiver certo, ela nunca
dispara. Se disparar, a mensagem diz o que investigar — ao contrário do
`KeyError` cru.

## O que NÃO recomendamos

Blindar o laço do reparo com `sel.get(g)`, pulando as ausentes. Faria o
`KeyError` sumir e o reparo passaria a otimizar sobre um subconjunto silencioso
das cidades, devolvendo um plano que respeita o teto por ter ignorado parte do
problema. Trocaria uma falha barulhenta por um resultado errado — que é a
troca que este pacote evita em vários outros pontos.

## Do nosso lado

`dev/worker.py` captura o `KeyError` e o traduz numa mensagem que diz o que
houve e que a rodada é reexecutável. É paliativo: o estouro acontece dentro do
motor, antes de ele devolver qualquer coisa, então não há o que consertar daqui.
