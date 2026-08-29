# asyncpg direto, sem ORM

Quase todo o trabalho deste serviço é agregação de leitura sobre 14 tabelas
`public.otim_*` que o motor publica, com a forma do payload ditada pelo contrato
do front — não por linhas mapeadas a objetos. Um ORM cobraria uma camada de
tradução em cima de SQL que já é a resposta, e as consultas de nível 1 (cascata,
explicabilidade, cronograma) sairiam piores expressas em Python do que em
`GROUP BY`.

## Consequences

Não há migrações geradas nem modelos de tabela: o schema é dono do Databricks e
das migrações em `migracoes/*.sql`. Em troca, toda consulta é literal e revisável,
e o custo de mudar de driver é reescrever transporte, não domínio.
