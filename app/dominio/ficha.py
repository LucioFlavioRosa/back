"""A FICHA DE COLETA — o que ela tem de trazer, e o que se deriva dela.

O corpo é a ficha INTEIRA, e não um patch. É o que torna o `PUT` idempotente, e é
uma regra de domínio: `exigir_ficha_inteira` recusa o corpo incompleto em vez de
gravar só o que veio (um PATCH com nome de PUT) ou de zerar o que faltou (que
honraria o contrato apagando dado por causa de bug de cliente).

`capex` é derivado — quantidade × preço unitário —, e o motor avisa quando o valor
gravado discorda da conta. Deriva aqui, e não no SQL, porque é regra.
"""

from typing import Any

from app.dominio.campos import CAMPOS_DB, CAMPOS_PARAMS
from app.dominio.erros import FichaIncompleta, ValorInvalido
from app.dominio.formato import numerico


#: Componentes da ficha de obra -> colunas. `capex` NÃO entra: é calculado
#: (`qtd × preco`) e o contrato diz que não viaja no payload. Recebê-lo seria
#: aceitar uma segunda opinião sobre a mesma conta.
OBRA = {
    "qtd": "quantidade",
    "un": "unidade",
    "preco": "preco_unitario",
    "opex": "opex",
    "tPred": "tempo_predecessoras",
    "dur": "tempo_execucao",
    "anoObrig": "obra_obrigatoria_ano",
    "proibAte": "obra_proibida_ate",
    "wacc": "wacc",
}


#: Nomes do tipo `Ete` do front -> colunas. Tem de casar com o que `cadastro.etes`
#: devolve, senao a ficha lida nao pode ser salva de volta.
ETE = {
    "capMod": "capacidade_por_modulo",
    "capexMod": "capex_por_modulo",
    "opexMod": "opex_por_modulo",
    "tExec": "tempo_de_execucao",
    "capNom": "capacidade_nominal_atual",
    "vazOp": "vazao_de_operacao_atual",
    "nova": "nova",
    "terreno": "capex_terreno",
    "modulos": "modulos",
    "wacc": "wacc",
    #: Prazo e janela da obra da ETE — ver o comentario gemeo na leitura
    #: (`cadastro.py`, MAPA de `etes`). O motor le as tres; faltava a tela poder
    #: escrever. `capacidade_ociosa` fica de fora por ser derivada.
    "tPred": "tempo_predecessoras",
    "anoObrig": "obra_obrigatoria_ano",
    "proibAte": "obra_proibida_ate",
}


#: Colunas de ETE que sao numero — as demais (`nova`) sao texto.
#: As tres novas sao INTEGER na tabela (ano e quantidade de anos), entao entram
#: aqui: sem isso `numerico` nao roda e o driver recebe string num campo `integer`.
ETE_NUM = {"capMod","capexMod","opexMod","tExec","capNom","vazOp","terreno","modulos","wacc",
            "tPred","anoObrig","proibAte"}


def capex(o: dict[str, Any]) -> float | None:
    """`quantidade × preco_unitario` — a única conta que existe para o CAPEX.

    A REGRA não nasce aqui, e é por isso que ela é esta: o motor já a aplica. Em
    `otimizador_capex_v62.py:1165` — *"CAPEX pode vir DECOMPOSTO em quantidade x
    preco unitario; se vier, ELE MANDA"* — e a linha 1192 loga aviso quando a
    coluna do banco discorda da multiplicação. Guardar no cadastro um `capex` que
    a simulação ignora é manter dois números para uma pergunta só.

    A tela nunca manda `capex` (não está em `OBRA`, nem viaja no `GET`), e o
    front não o calcula: quem materializa é este arquivo, e a constraint
    `capex_e_derivado` (`migracoes/005_capex_derivado.sql`) recusa quem discordar
    por mais de um centavo.

    Sem `or 0`, que estava aqui e inventava valor: quantidade ausente não é
    quantidade zero. Zero afirmaria "esta obra não custa nada" — um número que
    ninguém digitou, gravado com cara de cadastro. Nulo diz o que é verdade, e a
    falta do fator já é pendência (`pendencias.py:OBRA`), que trava a unidade.

    `numerico` e nao `numero`: o segundo devolvia a string crua quando nao
    reconhecia o formato, e a multiplicacao estourava
    `TypeError: can't multiply sequence by non-int` -> 500. Numero torto numa obra
    e erro de quem chamou, e merece 422 dizendo o campo.
    """
    qtd = numerico(o.get("qtd"), "obra.qtd")
    preco = numerico(o.get("preco"), "obra.preco")
    if qtd is None or preco is None:
        return None
    return qtd * preco


def valor_de_obra(o: dict[str, Any], campo: str) -> Any:
    """Um campo da obra, validado como os demais campos numéricos da ficha.

    `un` é a unidade de medida (`m`, `un`, `ligacao`) e segue como texto. O resto
    passa por `numerico`, e não por `numero`: só `qtd` e `preco` eram validados
    — pelo caminho de `capex` —, então `dur: "abc"` chegava ao driver e virava
    500, e `dur: "3,7"` era arredondado em silêncio numa coluna inteira.
    """
    if campo == "un":
        return o.get(campo)
    return numerico(o.get(campo), f"obra.{campo}")


def obras_da_ficha(
    override: Any,
    atual: dict[str, dict[str, Any]],
    *,
    esperadas: int,
    rotulo: str,
) -> list[dict[str, Any]]:
    """O que vai para o banco: a linha GRAVADA, com o que o corpo mudou por cima.

    **Não há mais base literal.** Havia duas — uma aqui e outra em
    `src/cadastro/domain/` —, e elas eram a violação mais cara das regras R1 e R2:
    um componente ausente no banco reaparecia com `qtd 0 | preco 900 | dur 15 |
    wacc 0,067`, números plausíveis que ninguém digitou, indo direto para a
    simulação. Corrupção silenciosa é pior que perda silenciosa, porque a
    plausibilidade impede a desconfiança.

    Sem a base, a materialização tem uma fonte só: `atual`, que é o que
    `_obras_gravadas` leu de `componentes_*_capex`. O corpo só sobrepõe campo.

    **Cardinalidade ausente é RECUSA, e não preenchimento.** Se a ficha tem menos
    componentes que os `esperadas`, gravá-la exigiria inventar os que faltam — e
    inventar é o que acabou de sair daqui. A régua é a mesma que a prontidão usa
    (`pendencias.OBRAS_SUBBACIA`/`OBRAS_CTS`), então uma ficha que o `/prontidao`
    denuncia como incompleta é exatamente a que este `PUT` recusa. Duas respostas
    diferentes para o mesmo estado seriam um convite a acreditar na mais gentil.

    A recusa por componente OMITIDO no corpo continua: a gravação substitui as
    obras em bloco, a tela não oferece remover obra, logo a omissão não é intenção.
    """
    if isinstance(override, list):
        return override  # forma antiga; os smokes locais ainda a usam
    override = override or {}

    if len(atual) != esperadas:
        raise ValorInvalido(
            f"A ficha de {rotulo} tem {len(atual)} componentes gravados e a "
            f"simulação exige {esperadas}. Não dá para gravar: os que faltam não "
            "existem no banco, e completá-los aqui seria inventar obra. Veja em "
            "/prontidao qual componente falta e corrija o cadastro na origem."
        )

    faltando = sorted(set(atual) - set(override), key=int)
    if faltando:
        nomes = ", ".join(atual[i].get("nome") or f"índice {i}" for i in faltando)
        raise ValorInvalido(
            f"A ficha tem {len(atual)} componentes e o corpo trouxe {len(override)}. "
            f"Faltou: {nomes}. A gravacao substitui as obras em bloco, "
            "entao componente omitido seria APAGADO — e a tela nao oferece remover "
            "obra, logo a omissao nao e intencao."
        )

    sobrando = sorted(set(override) - set(atual), key=int)
    if sobrando:
        raise ValorInvalido(
            f"O corpo trouxe os índices {sobrando}, que não existem nesta ficha. "
            "O índice é a POSIÇÃO do componente, e gravar um que o banco não tem "
            "criaria obra a partir do payload — que é o que a base literal fazia."
        )

    return [
        {**atual[i], **(override.get(i) or {})} for i in sorted(atual, key=int)
    ]


def exigir_ficha_inteira(corpo: dict[str, Any]) -> None:
    """`params` e `db` precisam vir COMPLETOS — e o que faz o PUT ser substituicao.

    Bloco AUSENTE passa: um `PUT` que so mande `{"obrasOverride": {...}}` esta
    corrigindo obra sem tocar em `params` nem `db`, e exigir os dois blocos ali
    seria exigir que o cliente reenvie dado que nao esta mudando. Bloco PRESENTE,
    porem, tem de estar inteiro.
    """
    faltando: list[str] = []
    for bloco, esperados in (("params", CAMPOS_PARAMS), ("db", CAMPOS_DB)):
        if bloco not in corpo:
            continue
        recebidos = set(corpo[bloco] or {})
        faltando += [f"{bloco}.{c}" for c in esperados if c not in recebidos]
    if faltando:
        raise FichaIncompleta(
            "O corpo precisa trazer a ficha inteira. Faltaram: "
            + ", ".join(sorted(faltando))
            + ". Campo vazio deve vir como string vazia, não ausente."
        )
