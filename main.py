"""Ponto de entrada do backend do Otimizador de CAPEX.

    uvicorn main:app --reload          desenvolvimento
    uvicorn main:app --host 0.0.0.0    container (ver Dockerfile)

Tudo sob `/api`, porque o front chama caminho relativo na mesma origem: o Ingress
serve `/api` para este servico e `/` para o front. E o que evita CORS e evita ter
o dominio do backend embutido no bundle.
"""

import logging
from collections.abc import AsyncIterator
import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import cadastro, erros, resultados, saude, simulacao
from app.api.deps import guarda_de_rota
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
    vigia = asyncio.create_task(_vigiar())
    try:
        yield
    finally:
        vigia.cancel()
        await fila.fechar()
        await db.fechar_pool()


#: De quanto em quanto tempo o vigia procura lease vencido. Metade do lease (30s)
#: para que uma rodada abandonada não fique invisível por mais de um ciclo.
_INTERVALO_VIGIA = 15


async def _vigiar() -> None:
    """Recolhe rodadas cujo executor parou de renovar o lease.

    O contrato diz que as transições da rodada são do EXECUTOR, e isto é a
    exceção — declarada de propósito. O critério é estreito: `RODANDO` com
    `lease_ate` VENCIDO, nunca "sem progresso há N minutos". A materialização da
    maior unidade passa ~9,5 minutos sem escrever nada; matá-la por silêncio
    destruiria trabalho vivo.

    O que se recolhe é promessa vencida — alguém disse "estou nisso, me cobre em
    30 segundos" e parou de dizer. Sem isto, executor que TRAVA VIVO segura a
    rodada para sempre: a fila não reentrega (o lock está com ele) e nada percebe.
    Aconteceu, e a saída foi um UPDATE na mão.
    """
    from app.infra.repositorios import controle

    while True:
        try:
            await asyncio.sleep(_INTERVALO_VIGIA)
            recolhidas = await controle.recolher_abandonadas()
            for run in recolhidas:
                logging.getLogger(__name__).warning(
                    "rodada %s recolhida: lease vencido, executor nao responde", run
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Vigia que morre em silencio e pior que vigia nenhum: quem opera
            # passa a contar com uma protecao que nao existe mais.
            logging.getLogger(__name__).exception("o vigia de rodadas falhou")


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
# `guarda_de_rota` entra aqui, e nao endpoint a endpoint: e o que faz rota nova
# com `{unidade_id}` ou `{run_id}` nascer protegida. Ver `app/api/deps.py`.
_protegido = [Depends(guarda_de_rota)]
app.include_router(cadastro.router, prefix="/api", dependencies=_protegido)
app.include_router(simulacao.router, prefix="/api", dependencies=_protegido)
app.include_router(resultados.router, prefix="/api", dependencies=_protegido)
