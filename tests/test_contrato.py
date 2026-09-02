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
    # Favoritar e POR USUARIO (`migracoes/009_favoritas.sql`). PUT/DELETE, e nao
    # POST/POST, porque os dois sao idempotentes: o estado pedido e o estado final.
    "PUT /runs/{}/favorita",
    "DELETE /runs/{}/favorita",
    # Comentario e COMPARTILHADO (`migracoes/010_run_comentario.sql`), ao contrario
    # da favorita logo acima — e por isso ele exige o mesmo recorte da LEITURA,
    # enquanto a favorita nao exige nenhum. Idempotentes pela mesma razao: o PUT
    # com texto vazio apaga, entao os dois verbos levam ao mesmo estado final.
    "PUT /runs/{}/comentario",
    "DELETE /runs/{}/comentario",
    "GET /runs/{}/meta",
    "GET /runs/{}/painel",
    "GET /runs/{}/ebitda",
    "GET /runs/{}/cidades",
    "GET /runs/{}/cidades/{}",
    # Explicabilidade agregada: a mesma pergunta do nivel 4 ("por que nao
    # fatura?"), respondida antes de escolher a sub-bacia. Duas rotas e nao uma
    # com `?cidade=`: a URL da cidade ja e `/cidades/{}`, e o recorte cola nela.
    "GET /runs/{}/explicabilidade",
    # "De quanto teria de ser o orcamento anual para fazer tudo na mesma janela."
    # Rota propria: a explicabilidade diz o que ficou fora; esta diz o preco de
    # nao deixar.
    "GET /runs/{}/cenario-anual",
    "GET /runs/{}/sensibilidade",
    "GET /runs/{}/cidades/{}/explicabilidade",
    # O nivel 3 (sistema). Deixou de ser filtro no cliente quando a resposta
    # virou AGREGADO por obra: agregado nao se filtra depois.
    "GET /runs/{}/sistemas/{}/explicabilidade",
    # A LISTA e o CRONOGRAMA vem antes de `/obras/{}` na declaracao, e a ordem e
    # parte do contrato: o FastAPI casa por ordem, e `/obras/{}` engoliria
    # "cronograma" como se fosse um id de obra.
    "GET /runs/{}/obras",
    "GET /runs/{}/obras/cronograma",
    "GET /runs/{}/sistemas/{}/topologia",
    "GET /runs/{}/subbacias/{}",
    "GET /runs/{}/obras/{}",
    # §4 — nova simulação
    "GET /unidades/{}/prontidao",
    "POST /runs",
    "GET /runs/{}/status",
    "POST /runs/{}/cancelar",
    # A MESMA SIMULACAO COM O ORCAMENTO ESCALADO — a analise de sensibilidade.
    # `{"fator": 1.1}` = +10% de CAPEX em cada ano, tudo o mais identico. Clona no
    # SERVIDOR de proposito: `POST /runs` recebe o corpo do front e o traduz para
    # as chaves do job, e reconstruir esse corpo a partir dos parametros gravados
    # poria a traducao inversa no cliente. Idempotente pelo `abrir_rodada`, entao
    # repetir a varredura nao gasta cluster.
    "POST /runs/{}/variacao",
    "POST /runs/{}/reexecutar",
    # DEPLOY.md §3 — cadastro. `input.override` ja existe e os PUT estao listados
    # abaixo. NAO ha POST nem DELETE de CTS: colocar e tirar CTS e mudanca de
    # TOPOLOGIA, e vai pelas rotas de topologia — mexer na ficha sem mexer no no
    # produz meia CTS. Ver `app/api/cadastro.py`.
    "GET /regionais",
    # A DIRETORIA e o nivel entre a regional e a unidade (migracao 017). A tela de
    # selecao escolhe regional -> diretoria -> unidade, e sem esta rota o passo do
    # meio nao existiria.
    "GET /regionais/{}/diretorias",
    "GET /regionais/{}/unidades",
    "GET /unidades/{}",
    "GET /unidades/{}/hierarquia",
    "GET /unidades/{}/contrato",
    "GET /unidades/{}/sub-bacias",
    "GET /unidades/{}/etes",
    "GET /unidades/{}/cts",
    # A trilha de auditoria do cadastro. Entrou junto com o diff calculado no
    # servidor: gravar mais e continuar sem como ler teria piorado o que ja era
    # ruim — a trilha existia desde a 001 e nunca foi lida por ninguem.
    "GET /unidades/{}/alteracoes",
    # DEPLOY.md §3 — cadastro (escrita). Uma ficha por vez; o corpo e a ficha
    # inteira, e a trilha de override viaja junto.
    #
    "PUT /unidades/{}/sub-bacias/{}",
    "PUT /unidades/{}/cts/{}",
    # A concessao e da EMPRESA desde 31/08; esta e a rota que a grava, e o
    # gatilho do banco a desce para os municipios dela.
    "PUT /unidades/{}/empresas/{}",
    "PUT /unidades/{}/contrato/{}",
    "PUT /unidades/{}/etes/{}",
    # A TOPOLOGIA — em que sistema o componente entra, e para onde ele escoa. Ela
    # nao vem do Databricks: de fora vem quais sub-bacias e qual ETE sao do
    # sistema, e todas as CTS; quem monta o sistema e a Regional. Sem estas duas
    # rotas a tela do Grupo 01 editava contra o `sessionStorage` do navegador.
    #
    # O `DELETE` NAO apaga a ficha: ele poe `sistema_id` nulo, e o componente
    # continua cadastrado, fora de qualquer sistema. Apagar a linha perderia o
    # nome, que so existe em `sistema_topologia`.
    "PUT /unidades/{}/topologia/{}",
    "DELETE /unidades/{}/topologia/{}",
    # E A MESMA TOPOLOGIA GRAVADA DE OUTRO JEITO: o sistema INTEIRO numa
    # transacao, com `componentes` sendo a lista completa (quem nao vem, sai). As
    # duas rotas acima validam cada mudanca contra o que esta gravado, e por isso
    # cobram do cliente uma ordem de envio que as vezes NAO EXISTE — tirar uma CTS
    # e reapontar quem escoava para ela e recusado nas duas ordens possiveis. Esta
    # confere o desenho final, com as mesmas regras. As de um componente por vez
    # ficam: sao o caminho certo para mexer numa linha so.
    "PUT /unidades/{}/topologia",
    # O que a UNIDADE declara sobre si — hoje so `usaCts`: marcada, CADA sistema
    # dela aceita uma CTS; desmarcada, aceitam varias. E regra de cadastro, e nao
    # de simulacao (o motor nunca contou CTS por sistema). Era uma rota por
    # sistema ate a migracao 016. O NOME e o WACC nao entram: vem do Databricks e
    # nao tem rota de escrita, como o resto dos nomes do Grupo 01.
    "PUT /unidades/{}",
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
    assert len(_expostas()) == len(FORMAS_DO_CONTRATO) == 46


@pytest.mark.parametrize("run_id", ["r1' OR 1=1", "../etc", "com espaco", ""])
def test_run_id_hostil_e_recusado(run_id):
    """O `run_id` atravessa este serviço até virar caminho de partição e literal SQL
    no job. A gramática é a mesma dos dois lados — ver `app/dominio/run_id.py`."""
    from app.dominio import run_id as rid

    assert not rid.valido(run_id)
