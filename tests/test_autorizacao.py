"""Toda rota tem de estar coberta por alguma forma de recorte.

Este arquivo existe porque a `guarda_de_rota` reduz o numero de lugares onde da
para esquecer autorizacao, e nao o zera — e as duas maneiras de escapar dela ja
foram exploradas numa revisao:

  `POST /runs`                      recebe a unidade pelo CORPO. O guarda le
                                    `request.path_params` e nao a via. Quem tinha
                                    acesso a uma unidade disparava simulacao em
                                    outra e, como o disparo grava autoria, virava
                                    DONO do resultado.
  `/regionais/{regional_id}/...`    nao tem `unidade_id` no nome do parametro.
                                    Qualquer login, ate sem concessao nenhuma,
                                    enumerava regionais, unidades e resumos.

Nos dois casos o codigo funcionava. Autorizacao esquecida nao quebra nada — ela
so serve todo mundo, e o sintoma e a ausencia de sintoma.

Entao a regra aqui e: **rota nova nasce nesta lista ou o teste falha.** Quem
acrescentar um endpoint e obrigado a dizer, por escrito, sob qual regime ele cai.
Nao e burocracia: e o unico ponto do repositorio onde "esqueci de proteger" vira
vermelho em vez de silencio.
"""

import os

os.environ.setdefault("POSTGRES_URL", "postgresql://otim:otim@localhost:55432/otimizador")
os.environ.setdefault("SERVICE_BUS_CONN", "")

from main import app  # noqa: E402

#: Rotas que o guarda NAO cobre e que estao recortadas na mao, com onde olhar.
#: Entrar aqui e uma afirmacao: "eu conferi, e o recorte esta no proprio handler".
RECORTE_PROPRIO = {
    "/api/runs": "resultados.historico filtra por dono; simulacao.criar chama exigir_unidade",
    "/api/regionais": "cadastro.regionais filtra pelas unidades acessiveis",
    "/api/regionais/{regional_id}/unidades": "cadastro.unidades filtra a lista",
}

#: Rotas sem dado de nenhuma unidade — nao ha o que recortar.
#:
#: A documentacao entra aqui porque descreve a FORMA da API, e nao o conteudo.
#: Se um dia ela precisar ser fechada, o lugar e o `docs_url`/`openapi_url` do
#: FastAPI, e nao um recorte por usuario.
SEM_DADO = {
    "/healthz",
    "/readyz",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/api/openapi.json",
    "/api/docs",
    "/api/docs/oauth2-redirect",
    "/api/redoc",
}


def rotas():
    for r in app.routes:
        caminho = getattr(r, "path", None)
        if caminho and getattr(r, "methods", None):
            yield caminho


def test_toda_rota_tem_regime_de_acesso():
    """Sem excecao silenciosa: ou o guarda pega pelo caminho, ou esta declarada."""
    faltando = []
    for caminho in sorted(set(rotas())):
        if caminho in SEM_DADO or caminho in RECORTE_PROPRIO:
            continue
        # O guarda so age quando o parametro se chama assim — o nome faz parte do
        # contrato, e renomear sem perceber viraria buraco silencioso.
        if "{unidade_id}" in caminho or "{run_id}" in caminho:
            continue
        faltando.append(caminho)

    assert not faltando, (
        "rota(s) sem regime de acesso declarado: "
        + ", ".join(faltando)
        + ". Ou o caminho leva {unidade_id}/{run_id} (e a `guarda_de_rota` cobre), "
        "ou o handler recorta na mao e a rota entra em RECORTE_PROPRIO, "
        "ou ela nao serve dado de unidade e entra em SEM_DADO."
    )


def test_declaracoes_nao_envelhecem():
    """Rota declarada que deixou de existir vira mentira — some da lista."""
    existentes = set(rotas())
    mortas = [c for c in RECORTE_PROPRIO if c not in existentes]
    assert not mortas, f"declarada em RECORTE_PROPRIO e inexistente: {mortas}"
