"""A TRILHA — o que mudou, quem mudou, e de que origem.

**Quem compara é o servidor**, que tem as duas pontas: o gravado e o que chegou no
corpo. O cliente não informa o que mudou — auditoria que pergunta ao auditado tem
o defeito no desenho, e um cliente com bug apagaria o rastro sem sinal.

O CÁLCULO da diferença é regra pura e mora aqui; a GRAVAÇÃO dela, na mesma
transação da ficha, é do repositório. Era tudo um arquivo só, e `test_trilha_cadastro`
denunciava o encaixe importando `igual` e `origem_do_campo`, privados da infra.
"""

from typing import Any, NamedTuple

from app.dominio.campos import DO_DATABRICKS
from app.dominio.formato import texto_trilha


class Alteracao(NamedTuple):
    """Uma linha da trilha, antes de ir para o banco.

    `antes`/`depois` já em TEXTO, e no formato que a tela mostra (pt-BR): a trilha
    é lida por gente, meses depois, e `2497.7` numa reunião obriga quem lê a
    traduzir de cabeça o que a tela sempre mostrou como `2.497,70`.

    `None` tem significado nos dois lados, e são significados diferentes:
    `antes=None` é "não existia" (foi criado), `depois=None` é "deixou de existir"
    (foi removido). Ver `migracoes/007_trilha_do_cadastro.sql`.
    """

    campo: str
    antes: str | None
    depois: str | None
    origem: str


#: As duas origens. `databricks` é correção de número que veio de fora;
#: `regional` é campo que a Regional preenche. Na tela viram verbos diferentes.
DATABRICKS = "databricks"


REGIONAL = "regional"


def origem_do_campo(nome: str) -> str:
    """`fat` veio do Databricks; `preco` é da Regional. A régua é uma só.

    `DO_DATABRICKS` é a mesma lista que decide o que a tela trava e o que ela
    deixa editar (`cadastro.py`) — se as duas divergissem, a trilha chamaria de
    correção o que a tela nem oferece corrigir.
    """
    return DATABRICKS if nome in DO_DATABRICKS else REGIONAL


def igual(a: Any, b: Any) -> bool:
    """Os dois valores dizem a mesma coisa?

    Número compara como NÚMERO: o banco devolve `float` e o corpo traz string
    pt-BR, e `"244" != 244.0` como texto — comparar assim geraria uma linha de
    trilha a cada salvamento, dizendo que 244 virou 244.

    A tolerância é de ponto flutuante, e não de negócio: existe porque
    `2472.6 != 2472.5999999999995` depois de uma ida e volta pelo driver.
    """
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a) == str(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return str(a) == str(b)


def diferencas(
    antes: dict[str, Any],
    depois: dict[str, Any],
    *,
    prefixo: str = "",
    origem: Any = None,
) -> list[Alteracao]:
    """As chaves em que os dois dicionários discordam, na ordem em que aparecem.

    Serve os quatro caminhos de gravação porque todos acabam na mesma pergunta:
    o que estava lá, o que chegou, e em que eles diferem. Chave presente num só
    dos lados também é diferença — é criação ou remoção.

    `origem` aceita uma função (para decidir campo a campo, como na ficha de
    coleta, que mistura Databricks e Regional na mesma linha) ou uma string
    (quando a resposta é a mesma para o bloco inteiro, como nas obras).
    """
    de_origem = origem if callable(origem) else (lambda _c: origem or REGIONAL)
    saida: list[Alteracao] = []
    for chave in list(antes) + [k for k in depois if k not in antes]:
        a, b = antes.get(chave), depois.get(chave)
        if igual(a, b):
            continue
        saida.append(
            Alteracao(
                campo=f"{prefixo}{chave}",
                # A chave NUA decide o formato, e não o campo com prefixo:
                # `obra:Rede coletora:anoObrig` continua sendo um `anoObrig`.
                antes=texto_trilha(a, chave),
                depois=texto_trilha(b, chave),
                origem=de_origem(chave),
            )
        )
    return saida
