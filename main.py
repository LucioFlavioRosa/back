"""Ponto de entrada do backend do Otimizador de CAPEX.

    uvicorn main:app --reload          desenvolvimento
    uvicorn main:app --host 0.0.0.0    container (ver Dockerfile)

Tudo sob `/api`, porque o front chama caminho relativo na mesma origem: o Ingress
serve `/api` para este servico e `/` para o front. E o que evita CORS e evita ter
o dominio do backend embutido no bundle.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import cadastro, erros, resultados, saude, simulacao
from app.config import config
from app.infra import db, fila

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def ciclo(_: FastAPI) -> AsyncIterator[None]:
    """Pool e fila abrem UMA vez e vivem com o processo.

    Abrir no lifespan, e nao no primeiro request, faz o pod so entrar em
    `readyz` depois que o banco respondeu: o Kubernetes nao manda trafego para um
    pod que ainda vai descobrir que a senha esta errada.
    """
    await db.abrir_pool()
    await fila.abrir()
    try:
        yield
    finally:
        await fila.fechar()
        await db.fechar_pool()


app = FastAPI(
    title="Otimizador de CAPEX — API",
    version="0.1.0",
    description=(
        "Cadastro, disparo de simulação e leitura de resultados. "
        "O contrato desta API é o CONTRATO.md do repositório do front."
    ),
    lifespan=ciclo,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

erros.registrar(app)

if config().origens_cors:
    # Vazio em producao, de proposito: front e API na mesma origem nao precisam de
    # CORS. A lista so existe para desenvolvimento com o Vite em outra porta.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config().origens_cors,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(saude.router)  # fora do /api: as probes do k8s nao passam pelo Ingress
app.include_router(cadastro.router, prefix="/api")
app.include_router(simulacao.router, prefix="/api")
app.include_router(resultados.router, prefix="/api")
