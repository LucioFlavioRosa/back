"""OS RECORTES QUE TODA CONSULTA REFAZ, escritos uma vez só.

Um repositório não importa o outro para pegar um pedaço de SQL: `cadastro` já
importa `pendencias`, e a primeira consulta que `pendencias` precisou do recorte
de cidades fechou o ciclo. O pedaço mora aqui, e todos leem daqui.

ELE JÁ FOI SETE ESCRITAS: a definição que morava em `cadastro`, mais seis cópias
à mão — quatro em `pendencias`, uma em `cadastro_escrita` e uma em `controle`.
Todas equivalentes, e nenhuma igual à outra na forma: CTE ou JOIN, uma coluna ou
três, saindo de `cidade` ou de `cidade_empresa`.
Nenhuma divergia em resultado no dia em que foram unificadas, o que é justamente o
que torna esse tipo de cópia perigoso: ela não erra hoje. Erra no dia em que a
hierarquia ganha um nível — como ganhou a diretoria na migração 017 — e alguém
corrige cinco das seis.

O RECORTE ERRADO NÃO DÁ ERRO: ele mostra dado de outra unidade, sem sinal nenhum.
`tests/test_recorte_da_unidade.py` é o guarda-corpo contra a sétima cópia.

O recorte é `{i}`-parametrizado no schema e `$1` na unidade, como o resto do
`infra`: schema entra por f-string porque não dá para parametrizá-lo, e o id da
unidade entra como argumento porque dá.
"""

__all__ = ["CIDADES_DA_UNIDADE", "SISTEMAS_DA_UNIDADE"]


#: AS CIDADES DE UMA UNIDADE, pela hierarquia
#: `unidade → empresa → cidade`.
#:
#: Traz `emp_codigo` junto porque metade dos chamadores precisa dele em seguida —
#: a macrorregião de CTS é agrupada por `(sistema_cts, emp_codigo)`, e buscá-lo
#: depois seria um segundo `JOIN` sobre a mesma tabela.
#:
#: Vive extraído desde que a hierarquia ganhou a DIRETORIA (migração 017): a
#: mudança tocou uma definição em vez de todas as consultas que recortam por
#: unidade, e é essa a razão de o pedaço existir.
CIDADES_DA_UNIDADE = """
    SELECT c.cidade_id, c.cidade_name, ce.emp_codigo
      FROM {i}.cidade c
      JOIN {i}.cidade_empresa ce ON ce.cidade_id = c.cidade_id
      JOIN {i}.empresa e ON e.emp_codigo = ce.emp_codigo
     WHERE e.unidade_id = $1
"""


#: OS SISTEMAS DE UMA UNIDADE — um por linha, e não um por cidade.
#:
#: Desde a migração 022 um sistema pode estar em várias cidades, e
#: `cidade_sistema` tem uma linha por par. Juntar `sistema_topologia` a ela pelo
#: `sistema_id`, como toda consulta fazia, passou a MULTIPLICAR o componente pelo
#: número de cidades do sistema: pendência em dobro, árvore duplicada, trilha
#: contada duas vezes. Este recorte é o `DISTINCT` que cada uma delas teria de
#: escrever — e que uma esqueceria.
#:
#: Quem precisa da CIDADE do sistema (a lista de sistemas da hierarquia, o rail
#: de navegação) não usa isto: para essas a linha por cidade é o que se quer.
SISTEMAS_DA_UNIDADE = """
    SELECT DISTINCT cs.sistema_id, cs.sistema_name
      FROM {i}.cidade_sistema cs
      JOIN {i}.cidade_empresa ce ON ce.cidade_id = cs.cidade_id
      JOIN {i}.empresa e ON e.emp_codigo = ce.emp_codigo
     WHERE e.unidade_id = $1
"""
