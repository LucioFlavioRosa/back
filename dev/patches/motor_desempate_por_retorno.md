# Motor: entre planos que batem a mesma meta, escolher o de melhor retorno

> **IMPLEMENTADO.** Esta mudança deixou de ser proposta: ela está no repositório de
> produção do motor (`LucioFlavioRosa/otimzador_capex`, branch
> `desempate-por-retorno-e-convergencia`), com testes. O documento continua aqui
> porque o pacote `rev11` que a máquina local carrega por padrão **não** a tem — e é
> por isso que `dev/worker.py` detecta, por assinatura, qual geração do motor está
> em uso antes de escolher como chamar.


**Para quem mantém o `Otimizador_CAPEX_v62_pacote`.** Este arquivo descreve uma
melhoria no pacote e a mudança sugerida. **Nada foi aplicado ao pacote** — ele não
é versionado por nós.

Arquivo: `otimizador_capex_cpsat63.py`, função `resolver_por_sistema`, função
interna `_run()`. Linhas conferem com o `rev11`.

## O sintoma

Duas rodadas com **parâmetros idênticos** devolvem planos com VPL muito
diferente. Medido na `uA3` (67 cidades, verba 120 Mi/ano, horizonte 8):

| rodada | obrigatórias | metas | cobertura | VPL |
|---|---|---|---|---|
| referência | 126/126 | 47/69 | 47,8905% | **154,89 Mi** |
| repetição, mesmos parâmetros | 126/126 | 47/69 | — | **150,27 Mi** |
| gap 0,5% | 126/126 | 47/69 | 47,7267% | **142,68 Mi** |
| gap 2% | 126/126 | 47/69 | 47,5104% | **117,92 Mi** |

Obrigatórias e metas **nunca variam**. Cobertura varia 0,38 pp. O VPL varia 24%
— e 4,6 Mi dessa variação aparece **entre duas execuções idênticas**, sem
nenhuma mudança de parâmetro.

## A causa

O desempate lexicográfico tem três níveis e termina no terceiro:

```
fase 0  (linha 508)  maximiza obrigatórias construídas          -> O*
fase 1  (linha 524)  minimiza metas não atingidas               -> M*
fase 2  (linha 530)  trava M*, maximiza cobertura (idx 5)       -> C*   <- fim
```

`_run()` retorna em seguida (linha 537). **Não há fase que, travando C\*, escolha
entre os planos empatados o de melhor retorno.**

Isso torna o VPL uma variável livre: o solver chega a C* e devolve o primeiro
plano que o atinge. Entre um plano que rende 154 Mi e outro que rende 118 Mi com
a mesma cobertura, ele não tem preferência — e qual dos dois sai depende da ordem
de busca e do timing das threads do portfólio paralelo.

Não é viés, é **dispersão**: um viés constante preservaria a ordem entre
cenários; dispersão embaralha. Para quem usa o otimizador para **comparar
planos**, isso é o defeito que importa — dois cenários só são distinguíveis se a
diferença entre eles superar a dispersão.

## A correção sugerida

Uma quarta fase, com o termo que **já existe**: `_termos(y, 0)` é o VPL de
objetivo, e o caminho ponderado (linha 539) já o usa.

Depois da fase 2, com `Cstar` sendo o valor de objetivo que ela alcançou:

```python
Cstar = int(round(sv.ObjectiveValue()))
plano2 = _extrai(sv, y2)                    # guarda o plano da fase 2

# FASE 3: entre os planos que mantem O*, M* e C*, o de melhor retorno.
md3, y3 = _base(); _obrig_floor(md3, y3, O0)
MV3, MC3 = _termos(y3, 4)
if MV3: md3.Add(cp_model.LinearExpr.WeightedSum(MV3, MC3) <= Mstar)
CV, CC = _termos(y3, _idx2)
if CV: md3.Add(cp_model.LinearExpr.WeightedSum(CV, CC) >= Cstar)
RV, RC = _termos(y3, 3)          # idx 3 = VPL PURO. NAO use idx 0 — ver abaixo.
md3.Maximize(cp_model.LinearExpr.WeightedSum(RV, RC))
s3 = cp_model.CpSolver()
s3.parameters.max_time_in_seconds = max(5.0, float(max_time_s) * 0.4)
s3.parameters.num_search_workers = int(workers)
st3 = s3.Solve(md3)
if st3 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    return _extrai(s3, y3), st3, Mstar, _idx2, O0
return plano2, st, Mstar, _idx2, O0          # fase 3 falhou: devolve o da fase 2
```

Pontos de cuidado:

- **O índice é o 3 (`vpl`), e não o 0 (`vpl_obj`).** Esta foi a primeira versão da
  implementação, e estava errada: `vpl_obj = vpl - peso_cobertura * penalidade`, e
  com `foco_cobertura=1.0` o motor faz `peso_cobertura = capex_total * 10`. O
  objetivo fica dominado pela penalidade, e a fase "de retorno" re-otimiza
  COBERTURA sob outro nome — com a cobertura já travada pela restrição acima dela.
  Quando a penalidade é zero (todas as metas cumpridas) os dois índices coincidem,
  então o erro não aparece em cenário fácil.
- **A fase 3 é sempre viável**: o plano da fase 2 satisfaz `>= Cstar`, então o
  modelo tem pelo menos uma solução. Ainda assim o `if` acima guarda o caso de
  ela não terminar, devolvendo o plano da fase 2 — nunca pior que hoje.
- **`Cstar` é o incumbente, não o ótimo**, quando a fase 2 para por tempo ou por
  gap. Isso é o correto: travar o que foi de fato alcançado.
- **A repartição do tempo muda.** Hoje as frações são 0,35 / 0,4 / 0,6 e somam
  1,35× o `max_time_s`. Com a quarta fase é preciso re-repartir — a sugestão
  acima soma 1,75×, o que provavelmente é demais. Ver o outro patch,
  `motor_criterio_de_convergencia.md`: com critério de convergência as fases 0 e
  1 terminam em ~1s cada, e o orçamento real fica quase todo nas fases 2 e 3.

## Por que isso importa mais do que parece

As duas mudanças se completam. O critério de convergência (outro patch) troca
tempo por uma folga na cobertura — e hoje essa folga **sangra no VPL**, porque
ninguém está guardando o VPL. Com a fase 3, a folga fica onde foi pedida: perde-se
até X% de cobertura, e o retorno é maximizado dentro do que sobrou.

Ou seja: a fase 3 é o que torna o gap seguro. Sem ela, apertar o tempo é uma
loteria de VPL; com ela, é uma troca declarada e limitada.

## Alternativa considerada e descartada

O modo **ponderado** (`foco_cobertura < 0,95`, linha 539) já maximiza `vpl_obj`
com a cobertura entrando por peso. Ele resolve a dispersão, mas responde outra
pergunta: mistura retorno e cobertura numa função só, e aí não há garantia de que
as metas sejam priorizadas — que é exatamente o que o modo lexicográfico existe
para garantir. Quem escolhe "cobertura primeiro" quer as metas travadas, e só
depois o melhor retorno possível. É o que a fase 3 entrega.

## Do nosso lado

Não há contorno possível: isto é a estrutura do problema de otimização, dentro do
motor. Diferente do gap — que dá para forçar de fora, mesmo que feio —, uma fase
lexicográfica nova não tem por onde ser injetada.
