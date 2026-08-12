"""Ciclo de vida de uma rodada, e quem pode reexecutar quando.

O estado observavel de uma rodada e `controle.run_status.status`. A publicacao do
job e atomica — `public.otim_*` e `status = SUCESSO` entram na mesma transacao —
entao "ja foi publicado" e um FATO CONSULTAVEL, e nao um julgamento sobre a
intencao de quem disparou. E por isso que a regra de imutabilidade se apoia nele.

CANCELADA foi por um tempo um estado que o banco recusava: o CHECK de
`controle.run_status` listava os outros cinco, e o UPDATE falharia. Nesse periodo
`POST /runs/{id}/cancelar` respondia 501 em vez de tentar e capturar o erro — o
usuario veria "cancelado" e a rodada continuaria consumindo cluster.
`migracoes/008_lease_e_executores.sql` pos o valor no CHECK, e o endpoint passou a
cancelar de verdade.
"""

from enum import StrEnum


class Status(StrEnum):
    PENDENTE = "PENDENTE"
    RODANDO = "RODANDO"
    SUCESSO = "SUCESSO"
    FALHOU_QUALIDADE = "FALHOU_QUALIDADE"
    ERRO = "ERRO"
    CANCELADA = "CANCELADA"


#: Terminais: o front para o polling nestes.
TERMINAIS = frozenset({Status.SUCESSO, Status.FALHOU_QUALIDADE, Status.ERRO, Status.CANCELADA})

#: Em voo: existe trabalho acontecendo (ou esperando na fila).
EM_VOO = frozenset({Status.PENDENTE, Status.RODANDO})


def congelada(status: str | None) -> bool:
    """A rodada publicou? Entao o `run_id` dela nao aceita mais nenhuma execucao."""
    return status == Status.SUCESSO


def pode_reexecutar(status: str | None) -> bool:
    """Retry tecnico: so enquanto nada foi publicado.

    `None` (rodada sem linha em run_status) conta como "nao publicou" — e o intervalo
    entre o INSERT da run_request e o job pegar a mensagem.
    """
    return not congelada(status) and status not in EM_VOO


def motivo_para_recusar_reexecucao(status: str | None) -> str | None:
    """A mensagem que vai no corpo do 409. Ela e lida pelo usuario, entao diz o que
    fazer, e nao so o que aconteceu."""
    if congelada(status):
        return (
            "Esta rodada já foi publicada e não pode ser reexecutada. "
            "Crie uma nova simulação — o resultado publicado precisa continuar "
            "existindo para quem já o consultou."
        )
    if status in EM_VOO:
        return f"Esta rodada ainda está em execução (status {status}). Aguarde o término."
    return None


def pode_cancelar(status: str | None) -> bool:
    """So se ha o que interromper.

    `None` (sem linha em `run_status`) NAO conta: e a janela entre o INSERT da
    `run_request` e o job pegar a mensagem, e nao ha o que marcar — o UPDATE
    condicional nao acharia linha e responderia o mesmo 409.
    """
    return status in EM_VOO


def motivo_para_recusar_cancelamento(status: str | None) -> str | None:
    """A mensagem do 409. Ela e lida pelo usuario, entao diz o que ja aconteceu —
    a acao que ele queria (parar a rodada) ja esta feita, de um jeito ou de outro."""
    if status is None:
        return "Rodada sem estado registrado — não há execução para cancelar."
    if status == Status.CANCELADA:
        return "Esta rodada já foi cancelada."
    if status in TERMINAIS:
        return (
            f"Esta rodada já terminou (status {status}) e não há mais o que cancelar. "
            "Para rodar de novo, crie uma nova simulação."
        )
    return None
