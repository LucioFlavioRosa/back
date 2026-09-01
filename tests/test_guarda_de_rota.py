"""Toda rota sob `/api` nasce com a `guarda_de_rota`.

Ate 29/08/2026 isso se via a olho: as tres linhas de `include_router` no
`main.py` passavam `dependencies=[Depends(guarda_de_rota)]`, e um roteador sem a
dependencia saltava do diff. Depois que prefixo e guarda passaram para dentro de
cada `APIRouter` — que e onde eles descrevem o roteador, e nao onde ele e
montado —, essa visao de conjunto se perdeu.

Este teste a devolve, e melhor: nao depende de alguem olhar. Ele varre as rotas
MONTADAS, que e o que de fato responde a requisicao, e nao a fonte.

Nao substitui a autorizacao: `guarda_de_rota` le `{unidade_id}`/`{run_id}` do
caminho e recusa quem nao alcanca aquele recorte. O que este teste garante e que
ela esta la — rota nova que a esqueca quebra o build.
"""

import pytest
from fastapi.routing import APIRoute

from app.api.deps import guarda_de_rota
from main import app


def _rotas_da_api():
    """As rotas de ENDPOINT sob /api.

    `APIRoute` de proposito: `/api/docs` e `/api/openapi.json` tambem moram sob
    esse prefixo (ver `docs_url`/`openapi_url` no `main.py`), mas sao `Route` do
    Starlette — documentacao, nao endpoint. Exigir recorte de unidade delas seria
    exigir de uma pagina que nao le dado nenhum.
    """
    for rota in app.routes:
        caminho = getattr(rota, "path", "")
        if caminho.startswith("/api") and isinstance(rota, APIRoute):
            yield caminho, rota


def _tem_guarda(rota) -> bool:
    # `dependencies` traz as declaradas no roteador e na propria rota; o
    # `dependant` traz a arvore resolvida. Olhar as duas evita depender de um
    # detalhe interno do FastAPI.
    if any(d.dependency is guarda_de_rota for d in getattr(rota, "dependencies", [])):
        return True
    return any(
        d.call is guarda_de_rota for d in getattr(rota.dependant, "dependencies", [])
    )


def test_ha_rotas_para_conferir():
    # Sem isto, o teste abaixo passaria por nao encontrar rota nenhuma — que e
    # exatamente o cenario em que ele precisaria falhar.
    assert len(list(_rotas_da_api())) > 30


@pytest.mark.parametrize("caminho,rota", list(_rotas_da_api()), ids=lambda v: v if isinstance(v, str) else "")
def test_rota_sob_api_tem_guarda(caminho, rota):
    assert _tem_guarda(rota), f"{caminho} esta sob /api sem `guarda_de_rota`"


def test_saude_fica_fora_da_guarda():
    # As probes do k8s nao passam pelo Ingress e nao carregam identidade: se
    # `/healthz` exigisse recorte, o pod nunca ficaria pronto.
    for rota in app.routes:
        if getattr(rota, "path", "") in ("/healthz", "/readyz"):
            assert not _tem_guarda(rota)
