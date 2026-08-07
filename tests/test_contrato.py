"""A superfície da API não pode derivar do contrato do front.

O `CONTRATO.md` vive no repositório do front e é verificado lá contra o código
DELE (`src/contrato.test.ts`). Este teste fecha o outro lado: garante que este
serviço expõe exatamente os endpoints que aquele documento promete.

Copiar o `CONTRATO.md` para cá criaria duas verdades que envelhecem em ritmos
diferentes. Em vez disso, a lista abaixo é a transcrição das formas — e mexer nela
é um ato consciente, que aparece no diff e no code review. Um endpoint que some
sem alguém tocar nesta lista quebra o build; um que apareça sem estar aqui,
também.

Se a lista mudar, o `CONTRATO.md` muda junto. Os dois testes falham por lados
opostos da mesma divergência, que é exatamente o que se quer.
"""

import re

import pytest

from main import app

# CONTRATO.md §3 (resultados) e §4 (nova simulação) + DEPLOY.md §3 (cadastro).
FORMAS_DO_CONTRATO = {
    # §3 — leitura de uma rodada
    "GET /runs",
    "DELETE /runs/{}",
    "GET /runs/{}/meta",
    "GET /runs/{}/painel",
    "GET /runs/{}/ebitda",
    "GET /runs/{}/cidades",
    "GET /runs/{}/cidades/{}",
    "GET /runs/{}/sistemas/{}/topologia",
    "GET /runs/{}/subbacias/{}",
    "GET /runs/{}/obras/{}",
    # §4 — nova simulação
    "GET /unidades/{}/prontidao",
    "POST /runs",
    "GET /runs/{}/status",
    "POST /runs/{}/cancelar",
    "POST /runs/{}/reexecutar",
    # DEPLOY.md §3 — cadastro (leitura). A escrita esta bloqueada pela ausencia da
    # tabela de trilha de override; quando ela existir, os 6 PUT/POST/DELETE
    # entram aqui e o teste passa a cobri-los.
    "GET /regionais",
    "GET /regionais/{}/unidades",
    "GET /unidades/{}",
    "GET /unidades/{}/hierarquia",
    "GET /unidades/{}/contrato",
    "GET /unidades/{}/sub-bacias",
    "GET /unidades/{}/etes",
    "GET /unidades/{}/cts",
    # DEPLOY.md §3 — cadastro (escrita). Uma ficha por vez; o corpo e a ficha
    # inteira, e a trilha de override viaja junto.
    #
    # NAO ha POST nem DELETE de CTS: ela e no da topologia, e criar/remover no e
    # mudanca de cadastro estrutural, nao acao de tela. Ver `app/api/cadastro.py`.
    "PUT /unidades/{}/sub-bacias/{}",
    "PUT /unidades/{}/cts/{}",
    "PUT /unidades/{}/contrato/{}",
    "PUT /unidades/{}/etes/{}",
}


def _forma(caminho: str) -> str:
    """`/api/runs/{run_id}/meta` -> `/runs/{}/meta`.

    O nome do parâmetro é escolha de quem escreve; a forma é o contrato.
    """
    return re.sub(r"\{[^}]*\}", "{}", caminho).replace("/api", "", 1).rstrip("/") or "/"


def _expostas() -> set[str]:
    fora = ("/docs", "/openapi.json", "/redoc", "/healthz", "/readyz")
    achadas = set()
    for rota in app.routes:
        metodos = getattr(rota, "methods", None)
        if not metodos or any(p in rota.path for p in fora):
            continue
        for metodo in metodos - {"HEAD", "OPTIONS"}:
            achadas.add(f"{metodo} {_forma(rota.path)}")
    return achadas


def test_todo_endpoint_do_contrato_existe():
    faltando = sorted(FORMAS_DO_CONTRATO - _expostas())
    assert faltando == [], f"o front chama e o serviço não atende: {faltando}"


def test_nenhum_endpoint_a_mais():
    # O outro sentido da deriva: rota que ninguém pediu vira superfície pública
    # sem contrato, sem teste do lado do front e sem quem a mantenha.
    sobrando = sorted(_expostas() - FORMAS_DO_CONTRATO)
    assert sobrando == [], f"exposto sem estar no contrato: {sobrando}"


def test_a_lista_nao_esta_vazia():
    # Guarda contra o teste passar por não encontrar rota nenhuma — se `_expostas`
    # quebrar com uma mudança do FastAPI, os dois testes acima passariam vazios.
    assert len(_expostas()) == len(FORMAS_DO_CONTRATO) == 27


@pytest.mark.parametrize("run_id", ["r1' OR 1=1", "../etc", "com espaco", ""])
def test_run_id_hostil_e_recusado(run_id):
    """O `run_id` atravessa este serviço até virar caminho de partição e literal SQL
    no job. A gramática é a mesma dos dois lados — ver `app/dominio/run_id.py`."""
    from app.dominio import run_id as rid

    assert not rid.valido(run_id)
