"""A base de obras do backend e a do front têm de dizer a mesma coisa.

POR QUE ISTO EXISTE

A ficha que o front envia carrega só o que DIFERE da obra-base — `obrasOverride`.
Quem materializa a linha completa é o backend, com a base dele. As duas bases são
listas literais, uma em TypeScript e outra em Python, e nada as ligava:

    o front muda o preço unitário da rede coletora
    o usuário não toca no campo (não vira override)
    o backend grava o preço ANTIGO
    ninguém vê erro nenhum

O modo de falha é o pior possível — número errado no cadastro, sem sintoma, indo
para a simulação. Este teste transforma isso numa falha vermelha.

O QUE ELE COMPARA, E O QUE NÃO

Compara os campos NUMÉRICOS exatamente: quantidade, preço, opex, prazos, WACC. É
onde uma divergência muda resultado.

Para `nome` e `un`, compara ignorando ACENTO — e essa diferença é real e
deliberada:

    banco/planilha  "Ligacao de esgoto"   ← identidade: é a string que o motor
                                            casa com `componentes_*_capex`
    tela            "Ligação de esgoto"   ← rótulo: português correto para quem lê

Igualar as duas quebraria uma das pontas: acentuar o banco desalinha da planilha,
tirar o acento da tela mostra português errado.

E há um segundo desencontro, também real: as duas tabelas do banco usam
VOCABULÁRIOS DIFERENTES para o mesmo componente físico.

    componentes_subbacias_capex   "Coletor tronco", "Estacao elevatoria (EEE)"
    componentes_cts_capex         "Tronco",         "EEE"

O backend segue cada tabela — é o que faz a gravação casar. O front usa o
vocabulário da sub-bacia nas duas telas, porque é o rótulo que o usuário lê. Esses
pares estão em `APELIDOS` abaixo, com nome e sobrenome: encobri-los com uma
comparação frouxa esconderia também um rename de verdade, que é justamente o que
este teste existe para pegar.

Ler o arquivo do front daqui tem precedente: `test_contrato.py` já lê o
`DEPLOY.md`. Se o repositório do front não estiver ao lado, o teste é pulado, e
não falha — CI de um repo só não pode ficar vermelho por ausência do outro.
"""

import json
import re
import unicodedata
from pathlib import Path

import pytest

from app.infra.repositorios.cadastro_escrita import _BASE_CTS, _BASE_SUBBACIA

FRONT = Path(__file__).resolve().parents[2] / "otimizador-cadastro-web"
SUBBACIA_TS = FRONT / "src/cadastro/domain/subbacia.ts"
CTS_TS = FRONT / "src/cadastro/domain/cts.ts"

#: Onde uma diferença muda o plano que sai da simulação.
NUMERICOS = ["qtd", "preco", "opex", "tPred", "dur", "anoObrig", "proibAte", "wacc"]

#: Diferenças CONHECIDAS e deliberadas entre a identidade (banco) e o rótulo
#: (tela), já sem acento. Cada par é uma afirmação: "conferi, e são a mesma coisa".
#: Qualquer outra diferença de nome falha — inclusive um rename acidental.
APELIDOS = {
    ("tronco", "coletor tronco"),  # `componentes_cts_capex` chama só "Tronco"
    ("eee", "estacao elevatoria (eee)"),
    ("linha de recalque", "linha de recalque (lr)"),
}


def mesmo_componente(py: str, ts: str) -> bool:
    a, b = sem_acento(py), sem_acento(ts)
    return a == b or (a, b) in APELIDOS


def sem_acento(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def base_do_front(arquivo: Path, constante: str) -> list[dict]:
    """Extrai a lista literal do TypeScript.

    Sem parser de TS: a lista é um literal de objetos com chaves nuas e aspas
    simples, então dá para converter para JSON com duas substituições. Se o
    formato mudar (template, spread, valor computado), o `json.loads` estoura — e
    estourar é melhor que comparar contra uma leitura silenciosamente errada.
    """
    texto = arquivo.read_text(encoding="utf-8")
    m = re.search(rf"{constante}[^=]*=\s*(\[.*?\n\])", texto, re.S)
    assert m, f"não achei `{constante}` em {arquivo.name}"
    bruto = re.sub(r"(\w+):", r'"\1":', m.group(1)).replace("'", '"')
    bruto = re.sub(r",(\s*[\]}])", r"\1", bruto)  # vírgula sobrando
    return json.loads(bruto)


def pares():
    yield "sub-bacia", _BASE_SUBBACIA, SUBBACIA_TS, "BASE_OBRAS"
    yield "CTS", _BASE_CTS, CTS_TS, "BASE_OBRAS_CTS"


@pytest.mark.skipif(not SUBBACIA_TS.exists(), reason="repositório do front não está ao lado")
@pytest.mark.parametrize("rotulo,base_py,arquivo,constante", list(pares()))
def test_bases_dizem_a_mesma_coisa(rotulo, base_py, arquivo, constante):
    base_ts = base_do_front(arquivo, constante)

    assert len(base_py) == len(base_ts), (
        f"{rotulo}: o backend tem {len(base_py)} obras-base e o front {len(base_ts)}. "
        "A ficha é montada por ÍNDICE, então um a mais de um lado desloca tudo."
    )

    for i, (py, ts) in enumerate(zip(base_py, base_ts, strict=True)):
        assert mesmo_componente(py["nome"], ts["nome"]), (
            f"{rotulo}[{i}]: nome diferente — backend {py['nome']!r}, front {ts['nome']!r}. "
            "Acento e os apelidos conhecidos podem divergir (identidade x rótulo); "
            "um nome novo, não — se for deliberado, entre em APELIDOS com o motivo."
        )
        assert sem_acento(py["un"]) == sem_acento(ts["un"]), f"{rotulo}[{i}]: unidade diferente"

        for campo in NUMERICOS:
            assert py[campo] == ts[campo], (
                f"{rotulo}[{i}] ({ts['nome']}): `{campo}` diverge — "
                f"backend {py[campo]!r}, front {ts[campo]!r}. "
                "O usuário que não tocar neste campo vai gravar o valor do BACKEND, "
                "sem ver erro nenhum."
            )


def test_o_extrator_de_typescript_ainda_funciona():
    """Guarda-corpo do próprio teste.

    Se o formato do arquivo do front mudar e o extrator passar a devolver lista
    vazia, os testes acima passariam sem comparar nada — o pior tipo de teste
    verde.
    """
    assert len(base_do_front(SUBBACIA_TS, "BASE_OBRAS")) == 5
    assert len(base_do_front(CTS_TS, "BASE_OBRAS_CTS")) == 4
