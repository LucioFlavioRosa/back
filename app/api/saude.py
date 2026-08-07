"""Probes do Kubernetes.

Os dois endpoints respondem perguntas DIFERENTES, e trocar um pelo outro causa
estrago em direcoes opostas:

  - `/healthz` (liveness): "o processo esta vivo?". Nao toca no banco. Se tocasse,
    uma indisponibilidade do Postgres faria o kubelet MATAR todos os pods — que e
    a pior reacao possivel a um banco fora do ar, porque nada volta quando o banco
    voltar.
  - `/readyz` (readiness): "da para me mandar trafego?". Ai sim consulta o banco:
    sem ele nenhum endpoint funciona, e e melhor sair do balanceador.

Ficam fora do prefixo `/api` porque quem chama e o kubelet, direto no pod.
"""

from fastapi import APIRouter, Response, status

from app.config import config
from app.infra import db

router = APIRouter(tags=["saúde"], include_in_schema=False)


@router.get("/healthz")
async def vivo() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def pronto(resposta: Response) -> dict[str, object]:
    banco = await db.saudavel()
    cfg = config()
    if not banco:
        resposta.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "banco": banco,
        "fila": bool(cfg.service_bus_conn),
        # Denuncia o modo sem autenticacao. Um servico que sobe em producao com o
        # SSO desligado atende qualquer um e nada no comportamento denuncia isso —
        # entao ele se denuncia aqui, onde o time de plataforma olha.
        "autenticacao": "entra-id" if cfg.exige_auth else "DESLIGADA",
        "ambiente": cfg.ambiente,
    }
