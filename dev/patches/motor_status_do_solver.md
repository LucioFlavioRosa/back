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
janela de CAPEX de 8 anos. Uma terceira rodada, igual em tudo menos a janela
(6 anos), concluiu normalmente.

**Sobre o tempo de solver:** as rodadas pediram `MAX_TIME_S: 400`, mas o solver
recebeu **45s**. O worker de desenvolvimento limita pelo `--tempo` da linha de
comando — `segundos = min(int(p.get("MAX_TIME_S") or tempo), tempo)`,
`dev/worker.py:406` —, e o log confirma: `solver: max_time_s=45`. Desses 45s, o
caminho lexicográfico dá 40% à primeira fase e 60% à segunda. Em produção, com o
tempo pedido de verdade, a probabilidade de estourar sem solução é menor — mas o
defeito de código continua lá.

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

| linha   | trecho                                                         | tratamento      |
| ------- | -------------------------------------------------------------- | --------------- |
| 523     | `st=sv.Solve(md); return _extrai(sv,y),st,"-",5,O0`            | **nenhum**      |
| 527-529 | `st1=s1.Solve(md1)`; `if st1==INFEASIBLE: return`              | só `INFEASIBLE` |
| 535-537 | `st=sv.Solve(md2)`; `if st==INFEASIBLE: return` → `_extrai(...)` | só `INFEASIBLE` |
| 541-543 | idem, caminho ponderado                                        | só `INFEASIBLE` |

`UNKNOWN` — que o CP-SAT **pode** devolver ao atingir um limite de busca antes de
determinar `FEASIBLE`/`OPTIMAL`/`INFEASIBLE` — passa por esses filtros. A linha
527 importa mesmo sem chamar `_extrai`: dela sai o `Mstar` que restringe a segunda
fase, e um `ObjectiveValue()` lido fora de status válido contamina o modelo
seguinte.

Isso é consistente com a correlação observada com a janela: 8 anos gera mais
colunas e mais restrições anuais que 6, com o mesmo orçamento de tempo.

> **Ressalva.** Separando o que se sabe do que se supõe:
>
> - **Confirmado por leitura:** `_extrai` é chamado sem garantir status válido; o
>   laço do reparo quebra com `sel_final` parcial; `AddExactlyOne` torna a seleção
>   parcial impossível a partir de uma solução consistente.
> - **Refutado:** mistura entre solves. `sel_final.clear()` (linha 476) zera a
>   cada extração, `_fase0_obrig` não chama `_extrai`, e cada caminho de `_run()`
>   o chama no máximo uma vez. Não há resíduo.
> - **NÃO confirmado:** que `UNKNOWN` produza especificamente uma seleção
>   **parcial** em vez de vazia ou de exceção. Não foi reproduzido, e a
>   documentação do OR-Tools não garante comportamento de `Value()` fora de
>   `OPTIMAL`/`FEASIBLE` — só diz que não se deve lê-lo. Com `sel_final` vazio, o
>   `if not ok and sel_final:` da linha 558 nem entraria no reparo.
>
> A correção é defensiva em duas camadas por causa do terceiro item: uma delas
> fecha o buraco mesmo que o mecanismo seja outro.

## Correção sugerida

**1. Não extrair de um solve sem solução.** Nas **quatro** chamadas, trocar o
filtro de `INFEASIBLE` por uma verificação positiva:

```python
# linha 523
st = sv.Solve(md)
if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    return None, st, "-", 5, O0
return _extrai(sv, y), st, "-", 5, O0

# linhas 527-529 — a PRIMEIRA fase do lexicografico. Não chama `_extrai`, mas dela
# sai o `Mstar` que restringe a segunda fase: um `ObjectiveValue()` lido fora de
# status válido contamina o modelo seguinte em silêncio.
st1 = s1.Solve(md1)
if st1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    return None, st1, None, None, O0

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

`dev/worker.py` captura o `KeyError` e o traduz numa mensagem que diz o que houve
e que a rodada é reexecutável. A tradução só vale quando a chave é **uma cidade do
cenário** — fora disso o erro segue cru, para o próximo defeito não chegar
disfarçado deste. É paliativo: o estouro acontece dentro do motor, antes de ele
devolver qualquer coisa, então não há o que consertar daqui.

A mensagem diz "observado quando o tempo de solver não basta", e não "causado
por": o mecanismo completo não foi reproduzido, e cravar a causa num texto que o
usuário lê seria afirmar mais do que se sabe.

---

# Motor: capacidade zero passa em silêncio (achado ao remover `ETE_FASEADA`)

Segundo item para o mesmo mantenedor, independente do anterior.

Ao fixar `ete_faseada=True` no chamador, a análise mostrou uma lacuna de
validação que hoje não morde — mas morderia com dado novo.

**ETE nova com `modulos` vazio/0.** Em `otimizador_capex_v62.py:1230`,
`cap_total = modulos * cap_modulo` vira **0**, e o gating da linha 468 cai no
fallback "um módulo basta": um pacote construído libera **qualquer** demanda. No
modo não-faseado a mesma ETE teria `cap_max = modulos * cap_modulo` (linha 169) e
`viavel` rejeitaria demanda acima da capacidade (680-681). Ou seja, o modo faseado
é mais permissivo justamente onde o dado está incompleto.

**ETE existente sem `capacidade_por_modulo`.** Linha 1203 converte para 0; 1240 e
468 caem no mesmo fallback. Esta já existe também no não-faseado (176, 1430).

**Sugestão:** validar na carga, ou levantar em `ler_banco`, que ETE nova tem
`modulos > 0` e `capacidade_por_modulo > 0`, e que ETE existente tem
`capacidade_por_modulo > 0`. Falhar alto é melhor que um plano que libera vazão
sem capacidade — o erro seria invisível no resultado.

**Na base de hoje não há nenhum caso**: 460 ETEs novas e 537 existentes, zero com
`modulos` ou `capacidade_por_modulo` ausente. A correção é preventiva.
