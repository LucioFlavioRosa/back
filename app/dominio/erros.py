"""Os erros de domínio do cadastro — o que a escrita recusa, e por quê.

Vivem aqui, e não no repositório que os levanta, porque a REGRA é que os define:
"o corpo tem de trazer a ficha inteira", "número viaja em pt-BR estrito", "o
caminho termina na ETE". Quem os traduz em status HTTP é `app/api/erros.py`;
quem os levanta é a escrita; quem decide que existem é o domínio.
"""

#: A INTERFACE PÚBLICA deste módulo. Tudo o que não está aqui é implementação,
#: e pode mudar sem aviso — a lista é o contrato com quem importa.
__all__ = [
    "FichaIncompleta",
    "ValorInvalido",
    "FichaDeOutraUnidade",
    "TopologiaInvalida",
]

class FichaIncompleta(ValueError):
    """O corpo nao trouxe a ficha inteira — 422, com os campos que faltaram.

    O contrato e explicito: "o corpo carrega a ficha INTEIRA (idempotente), nao um
    patch", e "`params` viaja sempre inteiro". A implementacao, no entanto, gravava
    so as colunas presentes no corpo — ou seja, um PATCH com nome de PUT.

    Enquanto o front manda tudo, os dois coincidem. A divergencia mordia noutro
    lugar: um cliente que ESQUECESSE um campo teria o valor antigo preservado em
    silencio, e o bug dele ficaria invisivel; e ninguem conseguia raciocinar sobre
    o endpoint pelo contrato, porque o contrato descrevia outra coisa.

    Recusar e melhor que aceitar E ZERAR o que faltou: o segundo tambem honraria o
    contrato, mas apagaria dado de verdade por causa de um bug de cliente. Aqui, o
    pior caso e uma requisicao recusada com a lista do que falta.
    """

class ValorInvalido(ValueError):
    """Numero fora do formato pt-BR — 422, e nao 500.

    `numero` devolvia a string crua quando ela nao casava com o formato, e o
    driver estourava com DataError la no `INSERT` -> 500 generico. `"123abc"` num
    campo de preco e erro do usuario, e a resposta precisa dizer o campo.
    """

class FichaDeOutraUnidade(LookupError):
    """A ficha nao pertence a unidade do caminho — vira 404 no endpoint."""

class TopologiaInvalida(ValueError):
    """A ligacao pedida deixaria o cadastro incoerente — 422, com o motivo.

    Separada de `ValorInvalido` porque nao fala de formato de numero: fala de
    forma do grafo. A mensagem nomeia os componentes envolvidos, porque quem
    monta um sistema de sete componentes precisa saber QUAL deles fechou o ciclo.
    """
