"""OS RECORTES QUE TODA CONSULTA REFAZ, escritos uma vez só.

Um repositório não importa o outro para pegar um pedaço de SQL: `cadastro` já
importa `pendencias`, e a primeira consulta que `pendencias` precisou do recorte
de cidades fechou o ciclo. O pedaço mora aqui, e os três leem daqui.

O recorte é `{i}`-parametrizado no schema e `$1` na unidade, como o resto do
`infra`: schema entra por f-string porque não dá para parametrizá-lo, e o id da
unidade entra como argumento porque dá.
"""

__all__ = ["CIDADES_DA_UNIDADE"]


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
