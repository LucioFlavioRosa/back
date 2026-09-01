"""O `run_id` — quem o cunha, com que forma, e quando ele congela.

Este modulo carrega duas regras que vieram de fora e que o backend e o unico lugar
capaz de garantir.

1. A GRAMATICA. O `run_id` vira caminho de particao no blob e literal SQL na
   substituicao da rodada em Delta, do lado do job (`docs/02-integracao-backend.md`
   do pacote de producao). A coluna e `text` sem CHECK, entao a barreira e aqui.
   Um `run_id` com aspa simples faria o `replaceWhere` do Delta casar com TUDO e o
   overwrite levaria a tabela inteira; `/` e `..` desviariam o diretorio apagado.

2. A IMUTABILIDADE. Um `run_id` congela na primeira publicacao bem-sucedida
   (`CONTRATO.md` 2.1). Antes do SUCESSO, reexecutar pode reusar o id — e o retry
   tecnico, ninguem viu resultado. Depois, execucao nova recebe id novo, porque o
   job le o cadastro no instante em que roda: os mesmos parametros, depois de uma
   correcao no cadastro, produzem outro plano, e republicar apagaria o resultado
   que alguem aprovou em reuniao.
"""

#: A INTERFACE PÚBLICA deste módulo. Tudo o que não está aqui é implementação,
#: e pode mudar sem aviso — a lista é o contrato com quem importa.
__all__ = [
    "FORMA",
    "novo",
    "valido",
    "RunIdInvalido",
    "exigir_valido",
]

import re
import uuid
from datetime import datetime, timezone

# Mesma gramatica que `persistencia._exigir_run_id_seguro` aplica do outro lado.
# Se uma das duas mudar sem a outra, rodada valida aqui e recusada la.
FORMA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def novo(prefixo: str = "run") -> str:
    """`run_20260806_214500_a1b2c3` — ordenavel por nome e unico sem consultar o banco.

    O sufixo aleatorio existe porque duas rodadas podem ser disparadas no mesmo
    segundo; sem ele o `INSERT` colidiria na PK e o usuario veria um erro que nao e
    dele.
    """
    agora = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefixo}_{agora}_{uuid.uuid4().hex[:6]}"


def valido(run_id: str) -> bool:
    return bool(FORMA.match(run_id or ""))


class RunIdInvalido(ValueError):
    """`run_id` fora da gramatica — 404, e nao 500.

    `exigir_valido` levantava `ValueError` cru, que caia no handler generico: um
    `GET /runs/com espaco/status` respondia 500 com "erro interno" e enchia o log
    de traceback. Para quem chamou, um id malformado e um recurso que nao existe.
    """


def exigir_valido(run_id: str) -> str:
    if not valido(run_id):
        raise RunIdInvalido(
            f"run_id fora da forma aceita: {run_id!r}. "
            "Use [A-Za-z0-9._-], comecando por alfanumerico, ate 128 caracteres."
        )
    return run_id
