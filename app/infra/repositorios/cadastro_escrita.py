"""Escrita do cadastro — a ficha e a trilha, na mesma transação.

Separado da leitura porque as duas têm riscos opostos: ler errado mostra número
errado numa tela; gravar errado apaga o trabalho de alguém. Ficam em arquivos
diferentes para que quem vier mexer saiba em qual dos dois está.

Duas regras atravessam tudo aqui:

  - **uma ficha por vez, e o corpo é a ficha INTEIRA** — não um patch. Isso torna o
    PUT idempotente: reenviar o mesmo corpo não acumula efeito, e uma reconexão no
    meio do salvamento não deixa meia ficha gravada.
  - **ficha e trilha entram na MESMA transação.** Separá-las abriria a janela em que
    o dado já foi corrigido e a auditoria ainda não sabe — e é justamente nessa
    janela que um processo cai.
"""

import re
from typing import Any, NamedTuple

from app.config import config
from app.infra import db
from app.infra.repositorios.cadastro import (
    _COLETA,
    _DO_DATABRICKS,
    CAMPOS_DB,
    CAMPOS_PARAMS,
    NAO_MODELADOS,
    _ficha_coleta,
    pt_br,
    pt_br_ano,
    SEM_SEPARADOR,
)

# A cardinalidade vem de `pendencias`, e nao de um numero repetido aqui: e a MESMA
# regua que o `/prontidao` usa para denunciar obra ausente. Duas copias dela
# fariam a tela dizer que a ficha esta incompleta e o `PUT` aceita-la — ou o
# contrario, que e pior.
from app.infra.repositorios.pendencias import OBRAS_CTS, OBRAS_SUBBACIA


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

    `_numero` devolvia a string crua quando ela nao casava com o formato, e o
    driver estourava com DataError la no `INSERT` -> 500 generico. `"123abc"` num
    campo de preco e erro do usuario, e a resposta precisa dizer o campo.
    """


class FichaDeOutraUnidade(LookupError):
    """A ficha nao pertence a unidade do caminho — vira 404 no endpoint."""


# `1.234,5` -> 1234.5. O contrato e explicito: numero viaja como STRING pt-BR
# estrita, sem unidade nem simbolo. Mandar a string crua para uma coluna
# `double precision` faz o driver recusar; pior, `"2.497,70"` lido como ingles
# viraria 2.49770 — tres ordens de grandeza, sem erro nenhum pelo caminho.
_PT_BR = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$")


def _numero(v: Any, campo: str = "") -> Any:
    """String pt-BR vira float; o que ja e numero passa; o que nao e numero segue texto.

    String vazia vira None: no contrato, campo em branco e ausencia — e `wacc`
    vazio significa "usa o WACC medio da unidade". Zero seria outra coisa.
    """
    # `bool` ANTES de `int`: em Python `True` E `int`, entao `isinstance(True, int)`
    # e verdadeiro e um JSON `{"preco": true}` era aceito e gravado como 1. Nao ha
    # leitura razoavel de "preco verdadeiro" — e o cadastro passava a ter um valor
    # que ninguem digitou, indistinguivel de um preco real de R$ 1,00.
    if isinstance(v, bool):
        return str(v)  # segue como texto: `_numerico` transforma em 422
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if not s:
        return None
    if not _PT_BR.match(s):
        return v
    return float(s.replace(".", "").replace(",", "."))


#: Campos que NAO podem ser negativos. Vazao, preco, quantidade, receita e
#: populacao sao grandezas fisicas ou monetarias sem sentido abaixo de zero — e
#: uma vazao negativa nao para no cadastro: ela entra na simulacao e sai como um
#: plano que ninguem sabe que esta errado. `wacc` fica de fora de proposito
#: (taxa negativa e exotica mas existe), e `pot` (potencial de crescimento)
#: tambem, porque encolher e um cenario legitimo.
_NAO_NEGATIVOS = {
    "preco", "vaz", "vazInd", "tarr", "ramp",
    "popU", "popA", "fat", "arr", "fatInd", "arrInd",
    "ligU", "ligA", "ligN", "ligUInd", "ligAInd", "ecoU", "ecoA", "ecoN",
    "obra.qtd", "obra.preco", "obra.opex", "obra.dur", "obra.tPred",
}


def _numerico(v: Any, campo: str) -> Any:
    """Como `_numero`, mas para coluna que SO aceita numero: texto vira 422.

    Booleano tambem: `_numero` o devolve como texto justamente para cair aqui.
    """
    n = _numero(v)
    if isinstance(n, str):
        raise ValorInvalido(
            f"O campo {campo!r} precisa ser um número no formato 1.234,5 — recebi {v!r}."
        )
    if n is not None and n < 0 and campo in _NAO_NEGATIVOS:
        raise ValorInvalido(f"O campo {campo!r} não pode ser negativo — recebi {v!r}.")
    return n


def _i() -> str:
    return config().schema_input


def _texto(v: Any) -> str | None:
    """A trilha guarda TEXTO, e não o tipo original.

    Um override é o registro do que foi digitado, não um valor a recalcular. Texto
    sobrevive a mudança de tipo da coluna, guarda "0,5" como a pessoa escreveu, e
    não obriga a trilha a ter uma coluna por tipo.
    """
    return None if v is None else str(v)


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


def _origem_do_campo(nome: str) -> str:
    """`fat` veio do Databricks; `preco` é da Regional. A régua é uma só.

    `_DO_DATABRICKS` é a mesma lista que decide o que a tela trava e o que ela
    deixa editar (`cadastro.py`) — se as duas divergissem, a trilha chamaria de
    correção o que a tela nem oferece corrigir.
    """
    return DATABRICKS if nome in _DO_DATABRICKS else REGIONAL


def _igual(a: Any, b: Any) -> bool:
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


def _texto_trilha(v: Any, campo: str = "") -> str | None:
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
        if _igual(a, b):
            continue
        saida.append(
            Alteracao(
                campo=f"{prefixo}{chave}",
                # A chave NUA decide o formato, e não o campo com prefixo:
                # `obra:Rede coletora:anoObrig` continua sendo um `anoObrig`.
                antes=_texto_trilha(a, chave),
                depois=_texto_trilha(b, chave),
                origem=de_origem(chave),
            )
        )
    return saida


async def _registrar(
    con: Any,
    *,
    tipo: str,
    ficha_id: str,
    unidade_id: str,
    autor: str,
    mudancas: list[Alteracao],
) -> int:
    """Acrescenta a trilha — APPEND-ONLY. Nada aqui apaga nada.

    Cada gravação acrescenta só as diferenças que o servidor observou na
    transação atual; as linhas antigas conservam o autor, a data e o id que
    tiveram. Auditoria que reescreve a data do fato não é auditoria.

    **Quem compara é o servidor**, que tem as duas pontas: o que está gravado e o
    que chegou no corpo. O cliente não informa o que mudou — auditoria que
    pergunta ao auditado tem o defeito no desenho, e um cliente com bug apagaria
    o rastro sem sinal.

    Não há deduplicação, e nem é preciso: comparando com o dado gravado, salvar a
    mesma ficha dez vezes não produz diferença nenhuma.
    """
    if not mudancas:
        return 0
    await con.executemany(
        f"""INSERT INTO {_i()}.override
                (tipo, ficha_id, unidade_id, campo, valor_antigo, valor_novo,
                 autor, origem)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        [
            (
                tipo,
                ficha_id,
                unidade_id,
                m.campo,
                m.antes,
                m.depois,
                # O autor vem SEMPRE do token, nunca do corpo: quem pudesse
                # escolher o nome que assina poderia assinar a correcao de outro,
                # e uma trilha assim nao vale nada.
                autor,
                m.origem,
            )
            for m in mudancas
        ],
    )
    return len(mudancas)


#: Componentes da ficha de obra -> colunas. `capex` NÃO entra: é calculado
#: (`qtd × preco`) e o contrato diz que não viaja no payload. Recebê-lo seria
#: aceitar uma segunda opinião sobre a mesma conta.
_OBRA = {
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


async def _obras_gravadas(
    con: Any, tabela: str, chave: str, ficha_id: str
) -> dict[str, dict[str, Any]]:
    """As obras que a ficha JA TEM, na forma `{indice: {campo: valor}}`.

    Mesma forma que o `GET` devolve em `obrasOverride`, para o merge abaixo ser
    campo a campo. E o BANCO — nao um literal — que preenche o que o corpo omitir.

    O `nome` vem junto, e e ele que volta para a coluna `componente` na gravacao.
    Antes vinha da base literal, e a base usava o vocabulario da SUB-BACIA nas
    duas tabelas: regravar uma CTS trocava `Tronco` por `Coletor tronco` e `EEE`
    por `Estacao elevatoria (EEE)`, e o motor deixava de reconhecer o componente
    (`otimizador_capex_v62.py:1136` casa pelo nome). Vindo da linha gravada, cada
    tabela conserva o vocabulario dela sem ninguem precisar saber disso.
    """
    from app.infra.repositorios.cadastro import _INDICE_CTS, _INDICE_SUBBACIA

    linhas = await con.fetch(
        f"SELECT * FROM {_i()}.{tabela} WHERE {chave} = $1", ficha_id
    )
    pos = _INDICE_CTS if "cts" in tabela else _INDICE_SUBBACIA
    atual: dict[str, dict[str, Any]] = {}
    for l in linhas:
        i = pos.get(l["componente"])
        if i is not None:
            atual[i] = {
                "nome": l["componente"],
                **{campo: l[col] for campo, col in _OBRA.items() if col in l},
            }
    return atual


def _obras_da_ficha(
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


def _capex(o: dict[str, Any]) -> float | None:
    """`quantidade × preco_unitario` — a única conta que existe para o CAPEX.

    A REGRA não nasce aqui, e é por isso que ela é esta: o motor já a aplica. Em
    `otimizador_capex_v62.py:1165` — *"CAPEX pode vir DECOMPOSTO em quantidade x
    preco unitario; se vier, ELE MANDA"* — e a linha 1192 loga aviso quando a
    coluna do banco discorda da multiplicação. Guardar no cadastro um `capex` que
    a simulação ignora é manter dois números para uma pergunta só.

    A tela nunca manda `capex` (não está em `_OBRA`, nem viaja no `GET`), e o
    front não o calcula: quem materializa é este arquivo, e a constraint
    `capex_e_derivado` (`migracoes/005_capex_derivado.sql`) recusa quem discordar
    por mais de um centavo.

    Sem `or 0`, que estava aqui e inventava valor: quantidade ausente não é
    quantidade zero. Zero afirmaria "esta obra não custa nada" — um número que
    ninguém digitou, gravado com cara de cadastro. Nulo diz o que é verdade, e a
    falta do fator já é pendência (`pendencias.py:_OBRA`), que trava a unidade.

    `_numerico` e nao `_numero`: o segundo devolvia a string crua quando nao
    reconhecia o formato, e a multiplicacao estourava
    `TypeError: can't multiply sequence by non-int` -> 500. Numero torto numa obra
    e erro de quem chamou, e merece 422 dizendo o campo.
    """
    qtd = _numerico(o.get("qtd"), "obra.qtd")
    preco = _numerico(o.get("preco"), "obra.preco")
    if qtd is None or preco is None:
        return None
    return qtd * preco


async def _gravar_obras(
    con: Any,
    *,
    tabela: str,
    chave: str,
    ficha_id: str,
    obras: list[dict[str, Any]],
    atual: dict[str, dict[str, Any]],
) -> list[Alteracao]:
    """As obras da ficha, substituídas em bloco.

    `capex` não vem do corpo: é derivado (`_capex`) porque a tela não o manda e
    porque o motor não o leria de qualquer forma. Calcular no servidor mantém uma
    conta só — se os dois lados calculassem, divergiriam por arredondamento e
    ninguém saberia qual está no plano.

    `anoObrig` e `proibAte` são CÓDIGOS, não anos quaisquer (`0` = sem restrição,
    `-1` = obrigatória em qualquer ano). Por isso vão como vieram, sem `or 0`:
    tratar ausência como zero afirmaria "sem restrição" onde a resposta é silêncio.

    ## O diff sai ANTES do `DELETE`, e é por isso que ele existe aqui

    A gravação é `DELETE` + `INSERT` do bloco inteiro, e depois do `DELETE` não há
    com o que comparar: a informação de quem mudou o quê desaparece com as linhas.
    Por isso `atual` — o que `_obras_gravadas` já tinha lido para materializar a
    ficha — entra como parâmetro em vez de ser relido: é o mesmo retrato, dentro
    da mesma transação, e reler abriria janela para ele mudar no meio.

    O campo na trilha é `obra:<componente>:<campo>` porque a obra não tem
    identidade própria na tela — quem a identifica é o NOME do componente, que é o
    que a pessoa lê na linha da tabela. Índice (`obra:2:qtd`) seria mais curto e
    não diria nada a quem consulta a auditoria seis meses depois.
    """
    novas = {
        str(i): {"nome": o.get("nome"), **{k: _numero(o.get(k)) for k in _OBRA}}
        for i, o in enumerate(obras)
    }
    mudancas: list[Alteracao] = []
    for indice in sorted(set(atual) | set(novas), key=int):
        antiga = atual.get(indice) or {}
        nova = novas.get(indice) or {}
        nome = nova.get("nome") or antiga.get("nome") or f"índice {indice}"
        mudancas += diferencas(
            {k: antiga.get(k) for k in _OBRA},
            {k: nova.get(k) for k in _OBRA},
            prefixo=f"obra:{nome}:",
            # Obra é cadastro da Regional inteiro: não há número de obra vindo do
            # Databricks para "corrigir".
            origem=REGIONAL,
        )

    await con.execute(f"DELETE FROM {_i()}.{tabela} WHERE {chave} = $1", ficha_id)
    if not obras:
        return mudancas

    colunas = [chave, "componente", *_OBRA.values(), "capex"]
    marc = ", ".join(f"${i + 1}" for i in range(len(colunas)))
    linhas = [
        (ficha_id, o.get("nome"), *[_numero(o.get(k)) for k in _OBRA], _capex(o))
        for o in obras
    ]
    await con.executemany(
        f"INSERT INTO {_i()}.{tabela} ({', '.join(colunas)}) VALUES ({marc})", linhas
    )
    return mudancas


async def _gravar_coleta(
    con: Any,
    *,
    tabela: str,
    chave: str,
    ficha_id: str,
    params: dict[str, Any],
    bloco_db: dict[str, Any],
) -> list[Alteracao]:
    """A ficha de coleta (sub-bacia ou CTS) — os dois blocos na mesma linha.

    `params` viaja sempre inteiro, inclusive `popU`/`popA`. A régua de cobertura da
    cidade decide se esses dois APARECEM na tela e se contam pendência; não se são
    gravados. Trocar a régua de uma cidade não pode apagar o que alguém preencheu.

    Devolve o que MUDOU. A leitura de antes acontece dentro da mesma transação e
    depois do lock da ficha (`salvar_coleta`), então ninguém escreve entre ler e
    comparar — sem isso a trilha registraria como "de X" um X que já não era o
    valor no instante da gravação.
    """
    juntos = {**bloco_db, **params}
    frente_para_coluna = {v: k for k, v in _COLETA.items()}
    colunas = [
        frente_para_coluna[k]
        for k in juntos
        if k in frente_para_coluna and k not in NAO_MODELADOS
    ]
    if not colunas:
        return []
    valores = [_numerico(juntos[_COLETA[c]], _COLETA[c]) for c in colunas]

    # O ANTES, pelas mesmas colunas que vão ser escritas. Ficha que ainda não
    # existe devolve linha nenhuma, e aí todo campo preenchido é criação — que é
    # a leitura certa, e é diferente de "mudou de vazio para X".
    linha = await con.fetchrow(
        f"SELECT {', '.join(colunas)} FROM {_i()}.{tabela} WHERE {chave} = $1",
        ficha_id,
    )
    antes = {_COLETA[c]: (linha[c] if linha else None) for c in colunas}
    depois = {_COLETA[c]: v for c, v in zip(colunas, valores, strict=True)}

    marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
    await con.execute(
        f"""INSERT INTO {_i()}.{tabela} ({chave}, {", ".join(colunas)})
            VALUES ($1, {marc})
            ON CONFLICT ({chave}) DO UPDATE SET {sets}""",
        ficha_id,
        *valores,
    )
    return diferencas(antes, depois, origem=_origem_do_campo)


# ---------------------------------------------------------------- pertencimento
#: De que unidade e cada tipo de ficha. O `unidade_id` do CAMINHO nao pode ser
#: acreditado: ele so dizia em nome de quem gravar, e nao QUE ficha podia ser
#: gravada — dava para escrever na sub-bacia de outra unidade so trocando o id da
#: URL, e a trilha ainda registrava a unidade errada como dona.
_DONO = {
    "sub-bacia": """
        SELECT s.unidade_id
          FROM {i}.sistema_topologia t
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.superintendencia_cidade c ON c.cidade_id = cs.cidade_id
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE t.componente_sistema_id = $1""",
    "cidade": """
        SELECT s.unidade_id
          FROM {i}.superintendencia_cidade c
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE c.cidade_id = $1""",
    "cts": """
        SELECT s.unidade_id
          FROM {i}.subbacia_cts p
          JOIN {i}.sistema_topologia t ON t.componente_sistema_id = p.sub_bacia
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.superintendencia_cidade c ON c.cidade_id = cs.cidade_id
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE p.cts = $1""",
    # A ETE percorre o MESMO caminho da sub-bacia, e nao um caminho proprio: em
    # `sistema_topologia` ela e um componente do sistema como qualquer outro. O que
    # a distingue e o id dela tambem existir em `ete_capex` — e assim que o motor a
    # identifica (`otimizador_capex_v62.py:1111`):
    #
    #     if comp in ete_ids: ete_do_sis[d["sistema_id"]] = comp
    #
    # Eu tinha escrito no README que o vinculo era "por convencao de nome" e que o
    # esquema nao tinha caminho ate a unidade. Estava errado: o caminho sempre
    # existiu, e era o mesmo. O `JOIN` com `ete_capex` no fim so garante que o id
    # pedido e mesmo uma ETE, e nao uma sub-bacia entrando pela rota errada.
    "ete": """
        SELECT s.unidade_id
          FROM {i}.sistema_topologia t
          JOIN {i}.ete_capex e ON e.ete_id = t.componente_sistema_id
          JOIN {i}.cidade_sistema cs USING (sistema_id)
          JOIN {i}.superintendencia_cidade c ON c.cidade_id = cs.cidade_id
          JOIN {i}.regional_superintendencia s USING (superintendencia_id)
         WHERE t.componente_sistema_id = $1""",
}


async def exigir_dona(tipo: str, ficha_id: str, unidade_id: str) -> None:
    """A ficha existe E pertence a esta unidade? Senao, 404.

    404 e nao 403 de proposito: responder "existe, mas nao e sua" ja conta quais
    ids existem noutra unidade. Para quem esta no lugar certo o efeito e o mesmo.

    Cobre os quatro tipos. A ETE entrou depois das outras tres: eu achava que o
    esquema nao tinha caminho dela ate a unidade, e tinha — ela e um componente de
    `sistema_topologia` como a sub-bacia, so que com ficha em `ete_capex`.
    """
    sql = _DONO.get(tipo)
    if sql is None:
        return
    linha = await db.buscar_um(sql.format(i=_i()), ficha_id)
    if linha is None or linha["unidade_id"] != unidade_id:
        raise FichaDeOutraUnidade(f"{tipo} {ficha_id!r} nao pertence a unidade {unidade_id!r}")


def _exigir_ficha_inteira(corpo: dict[str, Any]) -> None:
    """`params` e `db` precisam vir COMPLETOS — e o que faz o PUT ser substituicao.

    Bloco AUSENTE passa: `{"overrides": [...]}` sozinho e uma correcao de trilha
    sem tocar na ficha, e exigir os dois blocos ali seria exigir que o cliente
    reenvie dado que nao esta mudando. Bloco PRESENTE, porem, tem de estar inteiro.
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


async def _marcar_autoria(
    con: Any, *, tabela: str, chave: str, ficha_id: str, autor: str
) -> dict[str, str]:
    """Quem gravou esta ficha, e quando. Em TODA gravação.

    **O autor vem do TOKEN, e o parâmetro `autor` é o mesmo que a trilha usa
    (`_registrar`).** Nunca do corpo: um cliente que pudesse escolher o nome que
    assina transformaria a auditoria em decoração.

    `now()` e não `clock_timestamp()`: dentro da transação, `now()` é o instante
    em que ela COMEÇOU, então a ficha, suas obras e sua trilha ficam com o mesmo
    carimbo. Três horários com milissegundos de diferença para uma gravação só
    fariam parecer que houve três.

    `INSERT ... ON CONFLICT` e não `UPDATE`: a ficha operacional pode não existir
    ainda — o `PUT` de uma sub-bacia que nunca teve linha em
    `subbacia_operacional` a cria. Com `UPDATE`, a primeira gravação de uma ficha
    nova seria justamente a que não deixaria rastro.

    **Devolve o carimbo** porque a resposta do `PUT` o leva de volta para a tela:
    sem isso a ficha exibiria a alteração anterior logo depois de você salvar, até
    alguém recarregar.
    """
    linha = await con.fetchrow(
        f"""INSERT INTO {_i()}.{tabela} ({chave}, atualizado_em, atualizado_por)
            VALUES ($1, now(), $2)
            ON CONFLICT ({chave}) DO UPDATE
              SET atualizado_em  = EXCLUDED.atualizado_em,
                  atualizado_por = EXCLUDED.atualizado_por
            RETURNING atualizado_em, atualizado_por""",
        ficha_id,
        autor,
    )
    from app.infra.repositorios.cadastro import _auditoria

    return _auditoria(dict(linha))


# ------------------------------------------------------------------ as fichas
async def salvar_coleta(
    *, unidade_id: str, ficha_id: str, corpo: dict[str, Any], autor: str, e_cts: bool
) -> dict[str, Any]:
    """PUT de sub-bacia ou de CTS — são a mesma ficha em duas tabelas."""
    tabela = "cts_operacional" if e_cts else "subbacia_operacional"
    chave = "cts" if e_cts else "sub_bacia"
    tab_obra = "componentes_cts_capex" if e_cts else "componentes_subbacias_capex"
    tipo = "cts" if e_cts else "sub-bacia"
    await exigir_dona(tipo, ficha_id, unidade_id)

    async with db.transacao() as con:
        # O lock SERIALIZA os PUTs da mesma ficha. Ele nunca detectou conflito —
        # quem fazia isso era o 409, que saiu —, mas continua necessário: sem ele
        # duas gravações simultâneas intercalam o `DELETE`+`INSERT` das obras e a
        # ficha termina com metade de cada uma. Ordenar não é o mesmo que barrar.
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", ficha_id)
        _exigir_ficha_inteira(corpo)
        mudancas = await _gravar_coleta(
            con,
            tabela=tabela,
            chave=chave,
            ficha_id=ficha_id,
            params=corpo.get("params") or {},
            bloco_db=corpo.get("db") or {},
        )
        # `in` e não `or []`: ficha SEM a chave não mexe nas obras; ficha COM a
        # chave e lista vazia apaga todas. São intenções diferentes.
        if "obrasOverride" in corpo:
            gravadas = await _obras_gravadas(con, tab_obra, chave, ficha_id)
            mudancas += await _gravar_obras(
                con,
                tabela=tab_obra,
                chave=chave,
                ficha_id=ficha_id,
                obras=_obras_da_ficha(
                    corpo.get("obrasOverride"),
                    gravadas,
                    esperadas=OBRAS_CTS if e_cts else OBRAS_SUBBACIA,
                    rotulo=tipo,
                ),
                atual=gravadas,
            )
        n = await _registrar(
            con,
            tipo=tipo,
            ficha_id=ficha_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
        auditoria = await _marcar_autoria(
            con, tabela=tabela, chave=chave, ficha_id=ficha_id, autor=autor
        )
    return {"id": ficha_id, "alteracoesGravadas": n, **auditoria}


async def _diff_da_cidade(
    con: Any, cidade_id: str, corpo: dict[str, Any]
) -> list[Alteracao]:
    """O que muda na ficha de cidade — as três tabelas, antes de qualquer escrita.

    Tem de rodar ANTES, e não depois: metas e faixas são apagadas e reinseridas em
    bloco, e depois do `DELETE` não sobra com o que comparar.

    ## Metas e faixas são COLEÇÕES, e por isso a chave importa

    A meta é identificada pelo ANO, e a faixa pela COBERTURA — não pela posição na
    lista. Comparar por posição diria que remover a primeira meta mudou todas as
    outras, quando o que houve foi uma remoção só.

    Com a chave certa, a leitura sai limpa nos três casos, e a convenção de NULL
    da migração 007 dá conta dos dois extremos:

        meta:2030:pct   ""   -> "85"    a meta passou a existir
        meta:2030:pct   "80" -> "85"    o valor mudou
        meta:2030:pct   "80" -> NULL    a meta foi removida
    """
    mudancas: list[Alteracao] = []
    cidade = corpo.get("cidade") or {}

    linha = await con.fetchrow(
        f"""SELECT data_fim_concessao, unidade_cobertura
              FROM {_i()}.cidade_operacional WHERE cidade_id = $1""",
        cidade_id,
    )
    mudancas += diferencas(
        {
            "fim": linha["data_fim_concessao"] if linha else None,
            "cob": linha["unidade_cobertura"] if linha else None,
        },
        {
            "fim": _numerico(cidade.get("fim"), "cidade.fim"),
            "cob": cidade.get("cob"),
        },
        origem=REGIONAL,
    )

    if "metas" in corpo:
        antes = {
            _texto_trilha(l["ano"], "ano"): l["cobertura_pct"]
            for l in await con.fetch(
                f"SELECT ano, cobertura_pct FROM {_i()}.metas_cobertura WHERE cidade_id = $1",
                cidade_id,
            )
        }
        depois = {
            _texto_trilha(_numerico(m.get("ano"), "meta.ano"), "ano"): _numerico(
                m.get("pct"), "meta.pct"
            )
            for m in corpo.get("metas") or []
        }
        mudancas += [
            Alteracao(f"meta:{a.campo}:pct", a.antes, a.depois, REGIONAL)
            for a in diferencas(antes, depois, origem=REGIONAL)
        ]

    if "fator" in corpo:
        antes = {
            _texto_trilha(l["cobertura_pct"]): l["paridade"]
            for l in await con.fetch(
                f"SELECT cobertura_pct, paridade FROM {_i()}.fator_esgoto WHERE cidade_id = $1",
                cidade_id,
            )
        }
        depois = {
            _texto_trilha(_numerico(f.get("cob"), "fator.cob")): _numerico(
                f.get("par"), "fator.par"
            )
            for f in corpo.get("fator") or []
        }
        mudancas += [
            Alteracao(f"faixa:{a.campo}:paridade", a.antes, a.depois, REGIONAL)
            for a in diferencas(antes, depois, origem=REGIONAL)
        ]

    return mudancas


async def salvar_contrato(
    *, unidade_id: str, cidade_id: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    """PUT da ficha de cidade: a cidade, suas metas e suas faixas de paridade.

    Metas e faixas são substituídas em bloco, e não mescladas: a tela edita a
    tabela inteira, e mesclar deixaria viva no banco uma linha que o usuário
    apagou na tela — a meta removida continuaria valendo na simulação.
    """
    cidade = corpo.get("cidade") or {}
    await exigir_dona("cidade", cidade_id, unidade_id)
    async with db.transacao() as con:
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", cidade_id)
        mudancas = await _diff_da_cidade(con, cidade_id, corpo)
        await con.execute(
            f"""INSERT INTO {_i()}.cidade_operacional
                    (cidade_id, data_fim_concessao, unidade_cobertura)
                VALUES ($1, $2, $3)
                ON CONFLICT (cidade_id) DO UPDATE
                  SET data_fim_concessao = EXCLUDED.data_fim_concessao,
                      unidade_cobertura  = EXCLUDED.unidade_cobertura""",
            cidade_id,
            _numerico(cidade.get("fim"), "cidade.fim"),
            cidade.get("cob"),
        )
        if "metas" in corpo:
            await con.execute(
                f"DELETE FROM {_i()}.metas_cobertura WHERE cidade_id = $1", cidade_id
            )
            await con.executemany(
                f"""INSERT INTO {_i()}.metas_cobertura (cidade_id, ano, cobertura_pct)
                    VALUES ($1, $2, $3)""",
                [
                    (cidade_id, _numerico(m.get("ano"), "meta.ano"),
                     _numerico(m.get("pct"), "meta.pct"))
                    for m in corpo.get("metas") or []
                ],
            )
        if "fator" in corpo:
            await con.execute(
                f"DELETE FROM {_i()}.fator_esgoto WHERE cidade_id = $1", cidade_id
            )
            await con.executemany(
                f"""INSERT INTO {_i()}.fator_esgoto
                        (cidade_id, cidade_name, cobertura_pct, paridade)
                    VALUES ($1, $2, $3, $4)""",
                [
                    (
                        cidade_id,
                        cidade.get("nome"),
                        _numerico(f.get("cob"), "fator.cob"),
                        _numerico(f.get("par"), "fator.par"),
                    )
                    for f in corpo.get("fator") or []
                ],
            )
        n = await _registrar(
            con,
            tipo="cidade",
            ficha_id=cidade_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
        # A ficha de cidade sai de três tabelas e o carimbo mora só em
        # `cidade_operacional`. É de propósito: quem editou uma meta editou a ficha
        # da cidade, e é a ficha que a tela mostra. Três carimbos separados
        # responderiam uma pergunta que ninguém faz.
        auditoria = await _marcar_autoria(
            con,
            tabela="cidade_operacional",
            chave="cidade_id",
            ficha_id=cidade_id,
            autor=autor,
        )
    return {"id": cidade_id, "alteracoesGravadas": n, **auditoria}


#: Nomes do tipo `Ete` do front -> colunas. Tem de casar com o que `cadastro.etes`
#: devolve, senao a ficha lida nao pode ser salva de volta.
_ETE = {
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
}
#: Colunas de ETE que sao numero — as demais (`nova`) sao texto.
_ETE_NUM = {"capMod","capexMod","opexMod","tExec","capNom","vazOp","terreno","modulos","wacc"}


def _nova_para_texto(v: Any) -> Any:
    """`ete_capex.nova` e TEXT no cadastro, e o front manda booleano.

    O motor le assim (`otimizador_capex_v62.py:1222`):

        str(d.get("nova","Nao")).strip().lower() in ("sim","s","true","1")

    Ou seja, ele aceita varias grafias, mas a coluna e texto e um `True` do Python
    estoura no driver antes de chegar la. A traducao mora aqui porque este e o
    unico ponto onde a convencao da tela e a do banco se encontram — e "Sim"/"Nao"
    e o que um humano abrindo a tabela espera ver.
    """
    if isinstance(v, bool):
        return "Sim" if v else "Nao"
    return v


async def salvar_ete(
    *, unidade_id: str, ete_id: str, corpo: dict[str, Any], autor: str
) -> dict[str, Any]:
    await exigir_dona("ete", ete_id, unidade_id)
    ete = dict(corpo.get("ete") or {})
    if "nova" in ete:
        ete["nova"] = _nova_para_texto(ete["nova"])
    presentes = [k for k in _ETE if k in ete]
    async with db.transacao() as con:
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", ete_id)
        mudancas: list[Alteracao] = []
        if presentes:
            colunas = [_ETE[k] for k in presentes]
            valores = [
                _numerico(ete[k], f"ete.{k}") if k in _ETE_NUM else ete[k]
                for k in presentes
            ]
            # O upsert toca SÓ os campos presentes, e o diff segue a mesma régua:
            # campo que o corpo não trouxe não foi alterado, e afirmar que foi
            # encheria a trilha de mudanças que ninguém fez.
            linha = await con.fetchrow(
                f"SELECT {', '.join(colunas)} FROM {_i()}.ete_capex WHERE ete_id = $1",
                ete_id,
            )
            mudancas = diferencas(
                {k: (linha[_ETE[k]] if linha else None) for k in presentes},
                dict(zip(presentes, valores, strict=True)),
                origem=REGIONAL,
            )
            marc = ", ".join(f"${i + 2}" for i in range(len(colunas)))
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(colunas))
            await con.execute(
                f"""INSERT INTO {_i()}.ete_capex (ete_id, {", ".join(colunas)})
                    VALUES ($1, {marc})
                    ON CONFLICT (ete_id) DO UPDATE SET {sets}""",
                ete_id,
                *valores,
            )
        n = await _registrar(
            con,
            tipo="ete",
            ficha_id=ete_id,
            unidade_id=unidade_id,
            autor=autor,
            mudancas=mudancas,
        )
        auditoria = await _marcar_autoria(
            con, tabela="ete_capex", chave="ete_id", ficha_id=ete_id, autor=autor
        )
    return {"id": ete_id, "alteracoesGravadas": n, **auditoria}


# ---------------------------------------------------------------------- CTS
# `criar_cts` e `apagar_cts` sairam: criar/remover CTS e mudanca de TOPOLOGIA, e a
# topologia vem do cadastro. Ver o comentario em `app/api/cadastro.py`.
