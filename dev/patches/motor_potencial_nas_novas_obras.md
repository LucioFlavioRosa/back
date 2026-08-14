# Motor: o potencial de crescimento deve entrar nas `*_novas_obras`

**Para quem mantém o `Otimizador_CAPEX_v62_pacote`.** Mudança de REGRA DE NEGÓCIO
pedida pelo dono do produto, não correção de defeito. **Nada foi aplicado** — o
pacote não é versionado por nós.

Arquivo: `otimizador_capex_v62.py`, função `ler_banco`. Linhas do `rev11`.

## A regra hoje

`potencial_crescimento` (default 1,0) multiplica **só o universo agregado**, que é
o denominador da meta:

```python
# linha 1329
_un_ef = num(_un) * _pt          # universo COM potencial
maxlig[_sn] += _un_ef * _fu2     # denominador da meta
baselig[_sn] += num(_la) * _fu2  # base NAO cresce
```

E o comentário da linha 1310 declara a intenção atual:

> Afeta SO o universo (denominador da meta): base atendida e **ligacoes novas nao
> mudam**.

Enquanto isso, as "novas das obras" são derivadas sem o fator:

```python
# linhas 1082-1090
for _un, _at, _nv in (("universo_ligacoes",  "ligacoes_atuais",  "ligacoes_novas_obras"),
                      ("universo_economias", "economias_atuais", "economias_novas_obras"),
                      ("universo_populacao", "populacao_atual",  "populacao_novas_obras")):
    _der = max(0.0, num(_d.get(_un)) - num(_d.get(_at)))
    _d[_nv] = _der
```

## O que muda, e por quê

**A meta sobe e o meio de alcançá-la, não.** Uma sub-bacia com crescimento
previsto passa a exigir mais cobertura — o denominador cresce —, mas as obras dela
continuam habilitando o mesmo número de ligações. O plano fica estruturalmente
incapaz de bater a meta que a própria configuração criou.

A regra nova: **as novas das obras já consideram a expansão.**

```python
_der = max(0.0, num(_d.get(_un)) * _pot(_d) - num(_d.get(_at)))
```

Vale para as **três** medidas — ligações, economias e população. A régua da meta
varia por cidade, e aplicar o fator só em ligações deixaria as três incoerentes
entre si.

## Ordem: `_pot` precisa existir antes

A derivação está na linha 1082; `_pot` só é definido na 1313. Uma das duas tem de
mudar de lugar. O menor movimento é subir `_pot` (é função pura de um dicionário,
sem dependência do que vem entre as duas):

```python
def _pot(_d):
    _p = _d.get("potencial_crescimento", _d.get("fator_crescimento", _d.get("potencial")))
    try:
        _p = float(_p)
        return _p if _p > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0
```

## Três consequências que o autor da mudança deve conhecer

**1. O VPL muda, não só a cobertura.** `ligacoes_novas_obras` alimenta a obra de
coleta (`kw.update(ligacoes=lig_novas, ...)`, linha ~1186) e vira receita de
ticket. Mais ligações novas = mais receita. O efeito não fica contido no
denominador da meta, e rodadas antigas deixam de ser comparáveis com as novas.

**2. Uma verificação de consistência fica vazia.** A linha 1330 avisa quando

```python
if _un_ef + 1e-6 < num(_la) + _no:   # universo(efetivo) < atuais + novas
```

Com a regra nova, `_no = _un_ef - _la` por construção, então `_la + _no == _un_ef`
sempre e o aviso **nunca mais dispara**. Ele existe para denunciar dado
inconsistente do Databricks — vale reescrevê-lo sobre o dado BRUTO
(`universo < atuais`, antes do fator) em vez de deixá-lo morto.

**3. O aviso de divergência muda de significado.** A linha 1092 avisa quando a
coluna do banco diverge do derivado. Com o fator na conta, toda sub-bacia com
`potencial > 1` passará a divergir do que o Databricks trouxe — o aviso vai
disparar em massa e deixar de sinalizar problema. Convém silenciá-lo quando a
divergência for explicada pelo fator.

## Do nosso lado — já aplicado

`ligacoes_novas_obras` e `economias_novas_obras` eram exibidos na tela **como
vieram do banco**, enquanto o motor os recalculava: tela e simulação já discordavam
sobre o mesmo número, em silêncio. Os dois viraram campo calculado (ƒ), com a
conta nova e sem override — `novasDeObras()` em `cadastro/domain/subbacia.ts`.
`populacao_novas_obras` já era calculado e passou a considerar o fator.

Com `potencial = 1` (o caso de quase toda a base hoje) o número exibido não muda,
porque 1 é o neutro da multiplicação.

**Uma divergência deliberada entre os dois lados**, apontada na revisão e mantida:
o motor trunca em zero (`max(0.0, ...)`); a tela **não**. Quando `atuais >
universo × potencial` — dado inconsistente do Databricks, ou potencial menor que 1
— a tela mostra o negativo, porque escondê-lo não ajuda quem precisa corrigir o
cadastro. Para a tela não sugerir que a simulação usará o negativo, a célula
carrega a nota `negativo: a simulação usa 0`. O número denuncia; a nota diz o que
roda.

**E "sem override" vale só na interface.** O `db` continua viajando inteiro no
`PUT`, com `ligN`/`ecoN` como vieram do servidor. Override já gravado nesses dois
campos não some nem volta a ser editável pela tela — limpá-los, se for a decisão,
é trabalho de backend/migração.
