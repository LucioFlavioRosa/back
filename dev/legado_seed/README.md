# Legado do seed sintético

Estes scripts foram escritos contra `seed.sql` / `seed_u2.sql` — um cadastro de
mentira com `u1`, `b38_1`, `c_rio`. O banco hoje é carregado da planilha real
(`dev/recarregar_tudo.py`), onde esses ids não existem: rodá-los daqui falha com
lista vazia ou `KeyError`, e a falha **não** é regressão do serviço.

Estão aqui, e não apagados, porque o que eles cobrem não tem substituto:

| script | o que prova |
|---|---|
| `smoke_seguranca.py` | token exigido, unidade não lê ficha de outra, trilha imutável |
| `smoke_concorrencia.py` | dois PUT simultâneos na mesma ficha, lock por unidade |
| `smoke_auditoria.py` | última alteração e autor por ficha, autor vindo do token |
| `smoke_fila.py` | Service Bus, dedup de rodada em voo |
| `smoke_recorte.py` | recorte por unidade nos payloads de resumo |
| `smoke_pendencias.py` | completude reagindo a campo apagado |
| `smoke_escrita.py` | os quatro PUT ponta a ponta |

## Para usar

Suba um banco **separado**, aplique o seed e aponte `POSTGRES_URL` para ele:

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml exec -T db \
  psql -U otim -d otimizador_seed -f /dev/legado_seed/seed.sql
POSTGRES_URL=postgresql://otim:otim@localhost:55432/otimizador_seed python dev/legado_seed/smoke_seguranca.py
```

Não rode contra o banco real: vários deles fazem `UPDATE` e `DELETE` diretos, e
`smoke_escrita.py` ainda chama `POST`/`DELETE /cts`, que hoje respondem **405** —
essas asserções eram válidas quando as rotas existiam.

## O caminho de volta

O conserto certo não é adaptar os ids: é fazer cada um **descobrir** unidade e
ficha pela própria API, como `../smoke_ida_e_volta.py` e `../smoke_incons.py` fazem.
Aí eles voltam para `dev/` e rodam contra qualquer banco. Enquanto isso não
acontece, ficam aqui — visíveis e desarmados, em vez de no meio dos que
funcionam, falhando e ensinando todo mundo a ignorar falha de smoke.
