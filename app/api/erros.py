"""Todo erro sai no formato que a tela sabe exibir.

O `CONTRATO.md` 1.1 e explicito: em 400/422/409 o corpo e `{"erro": "mensagem"}`,
e ESSA mensagem aparece para o usuario. O default do FastAPI e `{"detail": ...}`,
e com `detail` a tela cai no texto generico ("Não foi possível...") — o backend
teria explicado e ninguem leria.

Duas regras que valem a leitura antes de mexer:

  - 5xx NUNCA carrega detalhe tecnico. O contrato manda tratar como o 404: sem
    detalhe ao usuario. O traceback vai para o log e para o App Insights, onde
    quem pode agir sobre ele consegue ver.
  - a mensagem e escrita para quem apertou o botao, nao para quem escreveu o
    codigo. "Esta rodada já foi publicada; crie uma nova simulação" resolve o
    problema do usuario; "409 conflict on run_status" nao.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.dominio.parametros import ParametrosInvalidos
from app.infra.fila import FilaIndisponivel

log = logging.getLogger(__name__)


def _corpo(mensagem: str) -> dict[str, str]:
    return {"erro": mensagem}


# `JSONResponse(status, content=...)` NAO funciona: o primeiro posicional da
# classe e `content`, entao a chamada passa o status como corpo E como content, e
# estoura TypeError. O efeito era cruel — todo 4xx/5xx virava 500 com traceback,
# ou seja, o modulo que existe para padronizar erro era o unico caminho em que
# erro nenhum saia padronizado. So aparece com um erro de verdade acontecendo,
# e foi o primeiro achado do smoke contra Postgres real.


def registrar(app: FastAPI) -> None:
    @app.exception_handler(ParametrosInvalidos)
    async def _parametros(_: Request, e: ParametrosInvalidos) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=_corpo(str(e)))

    @app.exception_handler(FilaIndisponivel)
    async def _fila(_: Request, e: FilaIndisponivel) -> JSONResponse:
        # 503 e nao 500: e temporario e o usuario pode tentar de novo.
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=_corpo(str(e)))

    @app.exception_handler(RequestValidationError)
    async def _validacao(_: Request, e: RequestValidationError) -> JSONResponse:
        # O erro do Pydantic e uma lista de dicts; a tela espera uma frase. Pega o
        # primeiro problema e nomeia o campo — mais que isso vira ruido no toast.
        primeiro = e.errors()[0] if e.errors() else {}
        campo = ".".join(str(p) for p in primeiro.get("loc", ()) if p != "body")
        msg = primeiro.get("msg", "conteúdo inválido")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_corpo(f"{campo}: {msg}" if campo else msg),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        detalhe = e.detail if isinstance(e.detail, str) else "Requisição recusada."
        return JSONResponse(status_code=e.status_code, content=_corpo(detalhe))

    @app.exception_handler(Exception)
    async def _inesperado(req: Request, e: Exception) -> JSONResponse:
        log.exception("erro nao tratado em %s %s", req.method, req.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_corpo("Erro interno. A equipe foi notificada."),
        )
