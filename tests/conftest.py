"""Ambiente minimo para os testes importarem a aplicacao.

`app.config.Config` exige `POSTGRES_URL` e `SERVICE_BUS_CONN` sem default — o que
e proposital, para um servico nunca subir apontando para o lugar errado. Aqui os
valores sao de mentira: nenhum teste desta suite abre conexao, eles olham a
SUPERFICIE da API (quais rotas existem) e as regras de dominio (Python puro).
"""

import os

os.environ.setdefault("POSTGRES_URL", "postgresql://teste:teste@localhost:5432/teste")
os.environ.setdefault("SERVICE_BUS_CONN", "")
