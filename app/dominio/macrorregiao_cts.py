"""COMO VÁRIAS CTS VIRAM UMA, quando a unidade usa macrorregião de CTS.

Marcada a macrorregião (`input.unidade_regional.usa_macrorregiao_cts`), um coletor
atende à região e cada sistema comporta UMA CTS. As CTS que a origem entrega
separadas passam a ser lidas como uma só, e este módulo é a régua dessa fusão.

## Só o que vem do Databricks agrega

A ficha de coleta tem dois blocos, e a divisão está em `campos.py`:

  DO_DATABRICKS   medida da base comercial — travada na tela
  CAMPOS_PARAMS   o que a Regional preenche

**Só o primeiro bloco agrega.** Os `params` e as quatro obras são PREENCHIMENTO,
e o preenchimento é da macrorregião: quem cadastra informa o preço por ligação, os
tempos e a vazão do conjunto, uma vez, em vez de informá-los por coletor para
depois a máquina tentar adivinhar uma média.

Isso não é economia de código, é o que torna a regra defensável. Somar
`preco_por_ligacao` daria R$ 3.049 por ligação em `e2s83`; somar
`tempo_arrecadacao` daria 16 meses em `e1s27`, onde o maior dos dois é 12; somar
`potencial_crescimento` daria 2,30 num fator que é 1,15. Nenhuma dessas contas
precisa existir, porque nenhuma dessas colunas é do Databricks.

## O que agrega são todas somas

Receita faturada e arrecadada, ligações e economias (universo, atuais e novas
obras) e o recorte residencial — as doze que a tela mostra —, mais
`populacao_novas_obras`, que ela não mostra. Todas medem QUANTIDADE numa área; a
área da macrorregião é a união das áreas dos membros, e a medida da união é a
soma.

A décima terceira é somada ao NASCER e não entra no alarme de divergência: ver
`COLUNAS_COMPARAVEIS`.

O RECORTE RESIDENCIAL soma ENTRE MEMBROS, e nunca se soma ao total: ele já está
dentro de `ligacoes_atuais`. É parcela apurada, não estimativa.

## `ticket` não está aqui, e é de propósito

`ticket` é conta — `receita_arrecadada ÷ ligacoes_atuais` —, feita na leitura da
ficha (`repositorios/cadastro.py`). Agregadas a receita e as ligações, o ticket do
conjunto sai certo sozinho. Recalculá-lo aqui criaria a segunda definição da mesma
divisão, para envelhecer em ritmo diferente da primeira.
"""

from typing import Any

#: A INTERFACE PÚBLICA deste módulo.
__all__ = [
    "COLUNAS_COMPARAVEIS",
    "COLUNAS_DA_REGIONAL",
    "COLUNAS_DE_IDENTIDADE",
    "COLUNAS_QUE_SOMAM",
    "SEPARADOR",
    "agregar",
    "agrupar",
    "desmontar_id",
    "divergencias",
    "id_da_macrorregiao",
    "livres",
    "nome_da_macrorregiao",
]


#: O que a macrorregião SOMA: as doze medidas que a ficha apresenta, na ordem
#: delas, mais `populacao_novas_obras` — que a tela não mostra.
#:
#: `ligacoes_novas_obras` e `economias_novas_obras` somam como as demais, e o
#: resultado é honesto — mas o MOTOR as ignora e deriva de `universo - atuais`
#: (`otimizador_capex_v62.py`, em `ler_banco`). Somá-las aqui mantém a ficha
#: coerente com o que a tela mostra; não somá-las deixaria dois números do mesmo
#: payload contando histórias diferentes.
COLUNAS_QUE_SOMAM = (
    "receita_faturada_media_mensal",
    "receita_arrecadada_media_mensal",
    "universo_ligacoes",
    "ligacoes_atuais",
    "ligacoes_novas_obras",
    "universo_economias",
    "economias_atuais",
    "economias_novas_obras",
    "universo_ligacoes_residencial",
    "ligacoes_atuais_residencial",
    "universo_economias_residencial",
    "economias_atuais_residencial",
    # NÃO é modelada pelo front nem escrita pelo `PUT` (`campos.NAO_MODELADOS`),
    # e por isso é a mais fácil de esquecer: ninguém a vê na tela. Soma como as
    # irmãs de população, senão a macrorregião nasce com nulo em silêncio.
    "populacao_novas_obras",
)


#: O que a Regional preenche NA MACRORREGIÃO — não agrega.
#:
#: Mesma natureza das quatro obras: é informação de quem cadastra sobre o
#: conjunto, e não medida da base sobre cada parte.
COLUNAS_DA_REGIONAL = (
    "preco_por_ligacao",
    "tempo_arrecadacao",
    "tempo_ramp_up",
    "vazao_contribuicao",
    "potencial_crescimento",
    "universo_populacao",
    "populacao_atual",
)


#: Identidade, chave e carimbo. Nenhuma soma.
#:
#: `cidade_id` é chave estrangeira para `input.cidade`, e a macrorregião PODE
#: cruzar município — decidido em 10/09/2026. Como a ficha expõe UMA cidade, a
#: escolha precisa de regra; ver `_cidade_dominante`.
COLUNAS_DE_IDENTIDADE = ("cts", "cidade_id", "atualizado_em", "atualizado_por")


def _cidade_dominante(membros: list[dict[str, Any]]) -> str | None:
    """A cidade da macrorregião: a do membro com MAIS LIGAÇÕES ATUAIS.

    A macrorregião pode cruzar município, e a ficha expõe uma cidade só — então
    escolher é obrigatório, e a escolha tem de ter significado. "Onde está a maior
    parte das ligações" é a resposta que alguém consegue conferir olhando a base;
    "a primeira que o banco devolveu" não é resposta, é ordem de consulta virando
    dado.

    NÃO MUDA O RECORTE DA UNIDADE. O agrupamento inclui `emp_codigo`, e empresa
    pertence a uma unidade só — qualquer cidade dos membros levaria à mesma
    unidade. A escolha aqui decide o que a TELA mostra, e não a quem a ficha
    pertence.

    Empate se resolve pelo id da cidade, para o resultado não depender da ordem de
    entrada: duas leituras da mesma base têm de dar a mesma ficha.
    """
    por_cidade: dict[str, float] = {}
    for m in membros:
        cidade = m.get("cidade_id")
        if cidade is None:
            continue
        por_cidade[cidade] = por_cidade.get(cidade, 0.0) + float(m.get("ligacoes_atuais") or 0)
    if not por_cidade:
        return None
    return max(sorted(por_cidade), key=lambda c: por_cidade[c])


def agregar(membros: list[dict[str, Any]]) -> dict[str, Any]:
    """As medidas do Databricks somadas, para uma macrorregião.

    Devolve SÓ o que agrega. Os `params`, as quatro obras e o `cts` da
    macrorregião entram por quem chama — este módulo não inventa identidade nem
    preenchimento.

    `None` some da soma, e não vira zero: uma CTS sem receita informada não
    afirma receita nula, e somar zero por ela mentiria para baixo. Coluna em que
    NENHUM membro tem valor sai `None`, pela mesma razão.

    Membros de cidades diferentes são ACEITOS — a macrorregião pode cruzar
    município. `cidade_id` sai da regra de `_cidade_dominante`.
    """
    if not membros:
        raise ValueError("uma macrorregião precisa de pelo menos um membro")

    somado: dict[str, Any] = {}
    for coluna in COLUNAS_QUE_SOMAM:
        valores = [m[coluna] for m in membros if m.get(coluna) is not None]
        somado[coluna] = sum(valores) if valores else None

    somado["cidade_id"] = _cidade_dominante(membros)
    return somado


def agrupar(ctss: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """As CTS de uma unidade, agrupadas em macrorregiões.

    A chave é o par `(sistema_cts, emp_codigo)`. Os dois, e não só o primeiro:
    o id vem do Databricks e nada garante que ele seja único entre empresas — e se
    a mesma macrorregião atravessasse duas, fundir as duas metades criaria um
    coletor que nenhuma das empresas opera sozinha.

    CTS SEM `sistema_cts` FICA DE FORA, e não vira um grupo de um. Ela não
    pertence a macrorregião nenhuma, e devolvê-la aqui faria a tela oferecê-la
    como se fosse uma — o que é justamente o estado que a macrorregião substitui.
    Quem chama decide o que fazer com o resto; este módulo só diz o que agrupa.

    A ordem dentro de cada grupo é a de entrada: agregar é soma, e soma não
    depende de ordem — mas quem monta o id da macrorregião a partir dos membros
    precisa de estabilidade, e ordenar aqui esconderia essa necessidade.
    """
    grupos: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in ctss:
        macro = (c.get("sistema_cts") or "").strip()
        empresa = (c.get("emp_codigo") or "").strip()
        if not macro or not empresa:
            continue
        grupos.setdefault((macro, empresa), []).append(c)
    return grupos


#: O que separa as três partes do id de uma macrorregião. Nenhum id de coletor
#: do Databricks (`cts_196`, `cts_d1b100_1_1`) e nenhum código de empresa ou de
#: unidade o contém — é o que deixa `desmontar_id` distinguir macrorregião de
#: coletor só pelo id.
SEPARADOR = "|"


def id_da_macrorregiao(nome: str, emp_codigo: str, unidade_id: str) -> str:
    """O ID DA MACRORREGIÃO É NOME + EMPRESA + UNIDADE — e não o nome sozinho.

    O nome (`sistema_cts`) vem da origem e se repete: duas unidades podem ter,
    cada uma, uma macrorregião "Sarapuí" — coletores diferentes, cidades
    diferentes, empresas diferentes. Mas a macrorregião só ganha linha quando é
    colocada num sistema, e essa linha vive em `cts_operacional.cts`, chave
    primária do banco INTEIRO. Com o nome como id, a segunda unidade a colocar
    "Sarapuí" encontrava a linha da primeira — e ouvia "pertence a outra
    unidade", ou, se a primeira já a tivesse tirado do sistema, herdava em
    silêncio a soma dos coletores da outra.

    A empresa já determina a unidade (`empresa.unidade_id`); a unidade entra no
    id mesmo assim, por decisão do dono do produto (20/09/2026): a chave se lê
    sozinha, sem consulta.

    O nome não pode conter o separador — a origem nunca o usou, e uma que use
    seria um dado a arrumar, não uma ambiguidade para resolver aqui.
    """
    for parte, rotulo in ((nome, "nome"), (emp_codigo, "empresa"), (unidade_id, "unidade")):
        if not parte or not str(parte).strip():
            raise ValueError(f"macrorregião sem {rotulo}: id impossível")
        if SEPARADOR in str(parte):
            raise ValueError(f"{rotulo} de macrorregião com {SEPARADOR!r}: {parte!r}")
    return f"{nome.strip()}{SEPARADOR}{emp_codigo.strip()}{SEPARADOR}{unidade_id.strip()}"


def desmontar_id(componente_id: str) -> tuple[str, str, str] | None:
    """`(nome, emp_codigo, unidade_id)` de um id de macrorregião — ou `None` se
    o id não é de macrorregião (um coletor, uma sub-bacia, uma ETE)."""
    if not componente_id or componente_id.count(SEPARADOR) != 2:
        return None
    nome, empresa, unidade = componente_id.split(SEPARADOR)
    if not (nome and empresa and unidade):
        return None
    return nome, empresa, unidade


def nome_da_macrorregiao(componente_id: str) -> str:
    """O nome (`sistema_cts`) de um id de macrorregião; o próprio id se não é um."""
    partes = desmontar_id(componente_id)
    return partes[0] if partes else componente_id


def livres(
    grupos: dict[tuple[str, str], list[dict[str, Any]]],
    ja_colocadas: set[str],
    unidade_id: str,
) -> list[dict[str, Any]]:
    """As macrorregiões que a tela de montar o sistema pode oferecer.

    LIVRE TEM DOIS LADOS, e faltar um deles a oferece quando não devia:

      * nenhum MEMBRO está em sistema (`colocada`) — colocar a macrorregião é
        colocar os coletores dela, e um deles já colocado por fora torna isso
        impossível. Oferecê-la assim seria recusar depois, longe daqui;
      * a MACRORREGIÃO não está em sistema (`ja_colocadas`) — depois de colocada
        ela tem linha própria e os membros continuam soltos, então olhar só para
        eles a manteria na lista de disponíveis com ela já montada noutro lugar.

    O segundo lado só passou a existir quando a macrorregião ganhou linha ao ser
    colocada; antes disso ela não tinha onde estar colocada, e a regra de um lado
    só bastava.

    O ID É COMPOSTO — nome, empresa e unidade (`id_da_macrorregiao`) — e `nome` vai
    ao lado, para a tela mostrar. Um nome que duas empresas usam vira dois ids, e
    as duas são oferecidas, cada uma nos sistemas da própria empresa. `ja_colocadas`
    é conjunto de ids compostos, como `cts_operacional.cts` os guarda.

    `cidId` é a cidade de `agregar` — a do membro com mais ligações —, e não a
    primeira que o banco devolveu: a ficha expõe uma cidade só, e duas leituras da
    mesma base têm de dar a mesma resposta.

    `empId` é a EMPRESA da macrorregião — a outra metade da chave —, e é por ela
    que a tela recorta. Um coletor se recorta pela cidade do sistema; uma
    macrorregião cruza município por definição, então cidade não é a régua dela.
    Recortá-la pela cidade dominante a escondia dos sistemas dos demais municípios;
    não recortar nada a oferecia a duas cidades de distância, e a lista do modo
    macrorregião ficava MAIOR que a do modo coletor. A empresa é o recorte que a
    chave `(sistema_cts, emp_codigo)` sempre disse: ela é ofertável nos sistemas
    da empresa que a opera.
    """
    saida = []
    for (macro, empresa), membros in grupos.items():
        composto = id_da_macrorregiao(macro, empresa, unidade_id)
        if composto in ja_colocadas or any(m.get("colocada") for m in membros):
            continue
        saida.append(
            {"id": composto, "nome": macro, "cidId": agregar(membros)["cidade_id"],
             "empId": empresa}
        )
    return sorted(saida, key=lambda m: (m["nome"], m["id"]))


#: O que o ALARME de divergência compara — e é menos do que se soma.
#:
#: `populacao_novas_obras` fica de fora, e a razão é a mesma que a mantém em
#: `NAO_MODELADOS`: a escrita nunca a toca. Ela seria, portanto, a única coluna
#: capaz de gerar um aviso que a ação recomendada — gravar a ficha de novo — não
#: conserta, e um alarme que não apaga é um alarme que ensina a ignorar alarmes.
#:
#: Ela continua sendo SOMADA quando a macrorregião nasce (`COLUNAS_QUE_SOMAM`):
#: nascer com o valor certo é diferente de prometer mantê-lo atualizado. E o motor
#: a deriva de `universo - atuais` de qualquer jeito.
COLUNAS_COMPARAVEIS = tuple(
    c for c in COLUNAS_QUE_SOMAM if c != "populacao_novas_obras"
)


#: A FOLGA da comparação de somas, em valor absoluto.
#:
#: As colunas de receita são `double precision`, e somar quatro delas em ordens
#: diferentes pode dar resultados que diferem na última casa. Um alarme que
#: dispara por 0,0000001 é um alarme que ninguém lê depois da terceira vez.
_FOLGA = 0.01


def divergencias(
    guardada: dict[str, Any], membros: list[dict[str, Any]]
) -> dict[str, tuple[Any, Any]]:
    """As medidas em que a ficha GRAVADA já não é a soma dos membros de HOJE.

    Colocada a macrorregião, a linha vira a ficha e não é mais recalculada: é ela
    que a Regional preenche, que a trilha audita e que o motor lê. O preço dessa
    escolha é este — uma recarga do Databricks que mude os membros deixa a linha
    para trás, e nada na tela diria.

    Esta função é o alarme. Ela não conserta: quem conserta é a próxima gravação
    da ficha, que reescreve as somas a partir dos membros atuais
    (`cadastro_escrita.salvar_coleta`). Consertar na leitura seria escrever numa
    leitura, e faria a divergência sumir sem ninguém saber que existiu.

    Devolve `{coluna: (gravado, soma_de_hoje)}` — só as que diferem, para quem
    chama poder dizer QUAL medida mudou em vez de "algo mudou".
    """
    fresca = agregar(membros)
    fora: dict[str, tuple[Any, Any]] = {}
    for coluna in COLUNAS_COMPARAVEIS:
        antes, agora = guardada.get(coluna), fresca.get(coluna)
        if antes is None and agora is None:
            continue
        if antes is None or agora is None:
            fora[coluna] = (antes, agora)
            continue
        if abs(float(antes) - float(agora)) > _FOLGA:
            fora[coluna] = (antes, agora)
    return fora
