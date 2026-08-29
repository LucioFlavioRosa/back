"""COMO NÚMERO VIAJA — pt-BR estrito, na ida e na volta.

É regra de domínio, e não de transporte: o contrato diz que número atravessa a
API como string pt-BR (`1.234,5`), e as duas pontas têm de concordar. Já não
concordaram uma vez, e o efeito foi o pior possível — o `GET` emitia
`str(2497.7)` = `"2497.7"`, a escrita exigia pt-BR, e **ler uma ficha e salvá-la
de volta dava 500**, que é a operação mais comum do cadastro.

Estava metade em `infra/repositorios/cadastro.py` (a saída) e metade em
`cadastro_escrita.py` (a entrada), o que é exatamente o arranjo que deixa as duas
divergirem sem ninguém ver. Aqui as duas metades se olham.
"""

import re
from typing import Any

from app.dominio.erros import ValorInvalido


def pt_br(v: Any) -> str:
    """Número do banco -> string pt-BR, que é como o contrato manda ele viajar.

    Sem isto o `GET` devolvia `str(2497.7)` = `"2497.7"`, e a escrita — que exige
    pt-BR estrito — não reconhecia o próprio formato que acabara de emitir. O
    efeito era o pior possível: **ler uma ficha e salvá-la de volta dava 500**, que
    é a operação mais comum do cadastro. Um teste de uso real pegou; nenhum dos
    smokes pegava, porque todos montavam o corpo à mão em vez de reenviar o que
    o `GET` devolveu.

    `1234.5` -> `"1.234,5"`. Inteiro não ganha casa decimal: `244.0` -> `"244"`,
    senão a tela mostraria "244,0" numa quantidade de ligações.
    """
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if float(v).is_integer():
        return f"{int(v):,}".replace(",", ".")
    return f"{v:,.4f}".rstrip("0").rstrip(".").replace(",", "\x00").replace(".", ",").replace("\x00", ".")


#: Campos que sao ANO ou CODIGO, e nao quantidade: vao sem separador de milhar.
#: `pt_br(2049)` daria "2.049", e ano com ponto e erro de leitura na tela — alem de
#: `obra_obrigatoria_ano` ser codigo (0 = nao obrigatoria, -1 = qualquer ano).
SEM_SEPARADOR = {"fim", "ano", "anoObrig", "proibAte"}


def pt_br_ano(v: Any) -> str:
    """`2049` -> `"2049"`. Numero que nao e quantidade nao ganha separador."""
    if v is None:
        return ""
    if isinstance(v, (int, float)) and float(v).is_integer():
        return str(int(v))
    return pt_br(v)


# `1.234,5` -> 1234.5. O contrato e explicito: numero viaja como STRING pt-BR
# estrita, sem unidade nem simbolo. Mandar a string crua para uma coluna
# `double precision` faz o driver recusar; pior, `"2.497,70"` lido como ingles
# viraria 2.49770 — tres ordens de grandeza, sem erro nenhum pelo caminho.
PT_BR = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$")


def numero(v: Any, campo: str = "") -> Any:
    """String pt-BR vira float; o que ja e numero passa; o que nao e numero segue texto.

    String vazia vira None: no contrato, campo em branco e ausencia — e `wacc`
    vazio significa "usa o WACC medio da unidade". Zero seria outra coisa.
    """
    # `bool` ANTES de `int`: em Python `True` E `int`, entao `isinstance(True, int)`
    # e verdadeiro e um JSON `{"preco": true}` era aceito e gravado como 1. Nao ha
    # leitura razoavel de "preco verdadeiro" — e o cadastro passava a ter um valor
    # que ninguem digitou, indistinguivel de um preco real de R$ 1,00.
    if isinstance(v, bool):
        return str(v)  # segue como texto: `numerico` transforma em 422
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if not s:
        return None
    if not PT_BR.match(s):
        return v
    return float(s.replace(".", "").replace(",", "."))


#: Campos que NAO podem ser negativos. Vazao, preco, quantidade, receita e
#: populacao sao grandezas fisicas ou monetarias sem sentido abaixo de zero — e
#: uma vazao negativa nao para no cadastro: ela entra na simulacao e sai como um
#: plano que ninguem sabe que esta errado. `wacc` fica de fora de proposito
#: (taxa negativa e exotica mas existe), e `pot` (potencial de crescimento)
#: tambem, porque encolher e um cenario legitimo.
NAO_NEGATIVOS = {
    "preco", "vaz", "tarr", "ramp",
    "popU", "popA", "fat", "arr",
    "ligU", "ligA", "ligN", "ligURes", "ligARes",
    "ecoU", "ecoA", "ecoN", "ecoURes", "ecoARes",
    "obra.qtd", "obra.preco", "obra.opex", "obra.dur", "obra.tPred",
    #: Espera nao e negativa — mesma regra de `obra.tPred`, que ja estava aqui.
    #: `ete.anoObrig`/`ete.proibAte` NAO entram, e a assimetria e proposital:
    #: `obra_obrigatoria_ano` tambem e CODIGO (0 = nao obrigatoria, -1 = qualquer
    #: ano), entao um negativo ali e valor legitimo. As irmas de obra tambem estao
    #: fora desta lista, pela mesma razao.
    "ete.tPred",
}


#: Campos cuja coluna e INTEIRA. Decimal aqui nao e precisao a mais: e um valor
#: que o Postgres arredondaria na gravacao, devolvendo depois um numero que
#: ninguem digitou. Ligacao e economia se contam; tempo de ramp-up e de
#: arrecadacao sao meses inteiros; ano de obra e ano.
#:
#: A lista espelha o schema, e `tests/test_campos_inteiros.py` a confere contra o
#: `information_schema` — coluna que mudar de tipo sem passar por aqui reprova.
INTEIROS = {
    # coleta (subbacia_operacional / cts_operacional)
    "tarr", "ramp",
    "ligU", "ligA", "ligN", "ligURes", "ligARes",
    "ecoU", "ecoA", "ecoN", "ecoURes", "ecoARes",
    # obras (componentes_*_capex)
    "obra.tPred", "obra.dur", "obra.anoObrig", "obra.proibAte",
    # ETE
    #: As tres ultimas entraram junto com os campos de prazo/janela da ETE. Sem
    #: elas, `anoObrig: "2028,5"` respondia 200 e o banco guardava 2028 — a coluna
    #: e `integer`, e o truncamento acontecia sem ninguem ver. E a mesma perda
    #: silenciosa que este conjunto existe para impedir, e as colunas irmas de
    #: obra (`obra.anoObrig`, `obra.proibAte`) ja estavam protegidas ali em cima:
    #: era a ETE que tinha ficado de fora.
    "ete.tExec", "ete.modulos", "ete.tPred", "ete.anoObrig", "ete.proibAte",
    # contrato
    "cidade.fim", "meta.ano",
}


def numerico(v: Any, campo: str) -> Any:
    """Como `numero`, mas para coluna que SO aceita numero: texto vira 422.

    Booleano tambem: `numero` o devolve como texto justamente para cair aqui.

    E decimal em coluna inteira vira 422 tambem. Aceitar `3,7` num campo de meses
    parecia tolerancia e era perda silenciosa: o banco guardava `3`, a tela
    reabria mostrando `3`, e quem digitou nunca soube. Recusar devolve a decisao a
    quem sabe se era 3, 4, ou o campo errado.
    """
    n = numero(v)
    if isinstance(n, str):
        raise ValorInvalido(
            f"O campo {campo!r} precisa ser um número no formato 1.234,5 — recebi {v!r}."
        )
    if n is not None and n < 0 and campo in NAO_NEGATIVOS:
        raise ValorInvalido(f"O campo {campo!r} não pode ser negativo — recebi {v!r}.")
    if n is not None and campo in INTEIROS and float(n) != int(n):
        raise ValorInvalido(
            f"O campo {campo!r} é um número inteiro — recebi {v!r}."
        )
    return n


def texto(v: Any) -> str | None:
    """A trilha guarda TEXTO, e não o tipo original.

    Um override é o registro do que foi digitado, não um valor a recalcular. Texto
    sobrevive a mudança de tipo da coluna, guarda "0,5" como a pessoa escreveu, e
    não obriga a trilha a ter uma coluna por tipo.
    """
    return None if v is None else str(v)


def texto_trilha(v: Any, campo: str = "") -> str | None:
    """O valor como a tela o mostra. `None` continua `None` — ver `Alteracao`.

    **ANO e CÓDIGO não levam separador de milhar**, e a régua é a mesma da
    leitura (`cadastro.SEM_SEPARADOR`): `fim`, `ano`, `anoObrig`, `proibAte`. O
    ano também é CHAVE de meta (`meta:2044:pct`), e uma chave formatada não
    corresponde a registro nenhum.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return "Sim" if v else "Nao"
    if isinstance(v, (int, float)):
        return (pt_br_ano if campo in SEM_SEPARADOR else pt_br)(v)
    texto = str(v).strip()
    return texto or None
