# Testes de integração — exigem serviço e banco DE PÉ

São **scripts**, e não testes de pytest. Cada arquivo fala com a API em
`localhost:8000` e com o Postgres real; sem eles no ar, falha por conexão e não
por regressão. Rodam com `python <arquivo>`, um a um.

Nenhum se chama `test_*.py`, e isso é deliberado: em Python esse prefixo é
reservado — significa "o pytest me coleta". Dois deles se chamaram assim por
algumas horas, e `pytest testes_de_integracao` disparava a bateria inteira
contra a API durante a COLETA, terminando o processo num `SystemExit`. Todos
ganharam também o guarda `if __name__ == "__main__":`, para que importar o
arquivo não execute nada.

A separação é por DEPENDÊNCIA, e não por gosto:

| onde | depende de | como roda |
|---|---|---|
| `tests/` | nada | `python -m pytest` |
| `testes_de_integracao/` | API + Postgres (+ Service Bus em alguns) | um a um, ver abaixo |

Eles moravam em `dev/`, misturados com carga de banco e o executor local. O nome
escondia o que eram: `formas.py` parecia um módulo de formas e era um teste de
contrato de payload, e ninguém que abrisse `dev/` saberia o que rodar para
conferir o serviço.

## Os dois de contrato

Conferem, campo a campo, se a resposta bate com o que o front declara. São a
rede contra a regressão mais cara do produto: o backend muda um nome e a tela
mostra `—` sem erro nenhum.

```bash
python testes_de_integracao/contrato_de_resultado.py   # os endpoints de RESULTADO
UNIDADE=uA1 python testes_de_integracao/contrato_de_cadastro.py
```

## Os smokes

```bash
python testes_de_integracao/smoke.py             # 21 GET + 1 POST: nenhum pode dar 5xx
python testes_de_integracao/smoke_incons.py      # as CTS que existem pela metade
python testes_de_integracao/smoke_ida_e_volta.py # ler a ficha e salvá-la de volta
```

Os de `smoke_auditoria`, `smoke_concorrencia`, `smoke_escrita`, `smoke_fila`,
`smoke_pendencias`, `smoke_recorte` e `smoke_seguranca` vieram de
`dev/legado_seed/` e esperam o banco daquele seed — ver `dev/legado_seed/README.md`.

## Depois de rodar

`python dev/limpar_rodadas_de_teste.py` — o `smoke.py` termina com um `POST /runs`
que deixa rodada no histórico.
