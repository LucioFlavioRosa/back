"""O CAMINHO ATÉ A ETE — as regras de forma do sistema, sem banco nenhum.

Estas regras estavam dentro de `infra/repositorios/cadastro_escrita.py`, junto do
adaptador Postgres, e a suíte denunciava o encaixe errado: `test_topologia`
importava `id_ou_nada` e `pedido_do_corpo` — ajudantes PRIVADOS de um módulo de
infraestrutura. Testar por baixo da interface é sinal de que o módulo tem a forma
errada, não de que o teste é malfeito.

Aqui elas são a interface. O repositório passou a ser um cliente delas, como
qualquer outro, e os testes falam com o domínio pelo nome público.

São de GRAFO, e é isso que as separa das regras de pertencimento ("este sistema é
desta unidade?", "este componente existe?"), que continuam na escrita porque
dependem de consulta. Estas aqui só precisam do desenho, e é por isso que dá para
exercitá-las em Python puro — que é o que `tests/test_topologia.py` faz.

O motor percorre `jusante` com trava em 200 saltos e NÃO verifica que chegou na
ETE: caminho quebrado não levanta erro, só deixa de somar as obras do trecho, e o
plano sai mais barato e continua plausível. Um ciclo é pior — ele repete o mesmo
trecho até 200 vezes e infla os requisitos. Nada disso aparece no resultado como
defeito, e é por isso que a recusa é na gravação.
"""

#: A INTERFACE PÚBLICA deste módulo. Tudo o que não está aqui é implementação,
#: e pode mudar sem aviso — a lista é o contrato com quem importa.
__all__ = [
    "id_ou_nada",
    "ciclo_ao_ligar",
    "voltas_do_sistema",
    "problemas_do_sistema",
    "pedido_do_corpo",
]

from typing import Any

from app.dominio.erros import FichaIncompleta, TopologiaInvalida


def id_ou_nada(v: Any) -> str | None:
    """Id em branco é AUSÊNCIA, e não um id chamado `""`.

    `texto` não serve aqui: ele existe para a trilha, onde o que importa é
    preservar o que foi digitado, e devolve `""` para string vazia. Aqui vazio tem
    significado — `jusante` em branco é caminho ainda não montado, `sisId` em
    branco é componente fora de sistema —, e tratá-lo como valor faria a validação
    procurar um componente de id vazio. É a mesma régua de `numero`, que também
    transforma campo em branco em `None`.
    """
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def ciclo_ao_ligar(
    escoa: dict[str, str | None], de: str, para: str | None
) -> list[str]:
    """Ligar `de → para` fecharia um ciclo? Devolve a volta, ou lista vazia.

    `escoa` é o sistema como está no banco (componente → jusante); a ligação nova
    é aplicada por cima, e o passeio começa do componente que está mudando. A
    volta devolvida começa e termina no mesmo componente, porque é isso que se
    mostra para quem monta: `A → B → C → A` diz qual ligação desfazer, e "ciclo
    detectado" não diz.

    É separado do banco de propósito: é a única regra aqui que é de GRAFO, e não
    de pertencimento — e é a que erra em silêncio se estiver errada. O motor
    percorre `jusante` com trava em 200 saltos, então um ciclo não trava nada: ele
    repete o mesmo trecho até 200 vezes e infla os requisitos da sub-bacia.
    """
    if para is None:
        return []
    caminho = {**escoa, de: para}
    visto = [de]
    atual: str | None = para
    while atual is not None:
        if atual in visto:
            return visto[visto.index(atual):] + [atual]
        visto.append(atual)
        atual = caminho.get(atual)
    return []


def voltas_do_sistema(escoa: dict[str, str | None]) -> list[list[str]]:
    """Todos os ciclos do sistema, cada um uma vez — a versão de ESTADO FINAL.

    `ciclo_ao_ligar` responde "esta ligação nova fecharia um ciclo?", que é a
    pergunta de quem grava um componente por vez. Gravando o sistema inteiro a
    pergunta muda: não há ligação "nova", há um desenho pronto que ou termina na
    ETE ou não termina. Perguntar ligação por ligação recusaria caminho legítimo —
    inverter um trecho passa por um estado intermediário que nunca chega ao banco.

    Cada volta é devolvida como `A → B → A`, começando e terminando no mesmo
    componente, pela mesma razão de lá: é a volta que diz qual ligação desfazer.
    Duas voltas com os mesmos componentes são a mesma volta, e aparecem uma vez.
    """
    estado: dict[str, str] = {}
    achadas: list[list[str]] = []
    ja_vistas: set[frozenset[str]] = set()
    for inicio in escoa:
        if estado.get(inicio):
            continue
        caminho: list[str] = []
        atual: str | None = inicio
        # Anda enquanto houver para onde ir e o nó ainda não tiver sido fechado
        # por um passeio anterior — sem isso o custo vira quadrático num sistema
        # em forma de bacia, onde todo mundo desemboca no mesmo tronco.
        while atual is not None and estado.get(atual) is None:
            estado[atual] = "andando"
            caminho.append(atual)
            atual = escoa.get(atual)
        # Parou num nó do passeio ATUAL: é ciclo. Parou num nó já fechado, ou em
        # nada, é caminho que termina — inclusive o que termina na ETE.
        if atual is not None and estado.get(atual) == "andando":
            volta = caminho[caminho.index(atual):] + [atual]
            if len(set(volta)) > 1 and (chave := frozenset(volta)) not in ja_vistas:
                ja_vistas.add(chave)
                achadas.append(volta)
        for componente in caminho:
            estado[componente] = "ok"
    return achadas


def problemas_do_sistema(
    escoa: dict[str, str | None],
    *,
    sistema_id: str,
    etes: set[str],
    ctss: set[str],
    usa_cts: bool,
) -> list[str]:
    """As regras do sistema conferidas sobre o desenho INTEIRO. Vazio é aceito.

    São as mesmas regras de `salvar_topologia`, na mesma ordem, vistas de outro
    ângulo: lá cada uma pergunta "esta mudança quebra alguma coisa?", aqui todas
    perguntam "o desenho que chegou está de pé?". A diferença não é de rigor — é
    de momento. Nenhuma regra foi afrouxada; o que sumiu foi a exigência de que
    cada passo intermediário também estivesse de pé, que é o que recusava
    reorganização legítima (tirar uma CTS e reapontar quem escoava para ela).

    Devolve TODOS os problemas, e não o primeiro: quem está reorganizando um
    sistema quer a lista inteira de uma vez. Um por gravação transformaria a
    correção numa fila de tentativas, que é exatamente o que este endpoint veio
    desfazer.

    `escoa` é o sistema completo (componente → jusante), e as chaves são a
    fronteira: quem não está aqui não está no sistema.
    """
    problemas: list[str] = []

    for componente, jusante in escoa.items():
        if jusante is None:
            continue
        if jusante == componente:
            problemas.append(f"{componente!r} não pode escoar para si mesmo.")
        elif jusante not in escoa:
            # Cobre os dois casos de uma vez, e de propósito: componente de outro
            # sistema e componente que acabou de sair deste chegam aqui iguais —
            # em ambos o caminho sairia da fronteira e terminaria numa ETE que não
            # é a do sistema. A frase diz o que fazer sem afirmar qual dos dois é.
            problemas.append(
                f"{componente!r} escoa para {jusante!r}, que não está no sistema "
                f"{sistema_id!r}. O caminho até a ETE não atravessa a fronteira do "
                "sistema: ou traga o destino para cá, ou aponte para outro."
            )

    for volta in voltas_do_sistema(escoa):
        problemas.append(
            "Isso fecharia um ciclo: " + " → ".join(volta) + ". O caminho precisa "
            "terminar na ETE, e um ciclo faria o motor repetir o mesmo trecho."
        )

    # A ETE e o FIM do caminho: na base inteira nao ha uma com jusante.
    for ete in sorted(e for e in etes if escoa.get(e) is not None):
        problemas.append(
            f"{ete!r} é a ETE do sistema — ela é o fim do caminho e não escoa "
            "para lugar nenhum."
        )
    # O motor guarda UMA ETE por sistema (`ete_do_sis[sis] = comp`): a segunda
    # sobrescreveria a primeira em silencio.
    if len(no_sistema := sorted(etes & set(escoa))) > 1:
        problemas.append(
            f"O sistema {sistema_id!r} ficaria com {len(no_sistema)} ETEs ("
            + ", ".join(repr(e) for e in no_sistema)
            + "). Um sistema tem uma ETE só."
        )
    # UMA CTS POR SISTEMA, quando a unidade usa macrorregiao de CTS. A regra e do
    # CADASTRO, e nao do motor — para ele uma ou duas CTS sao nos como quaisquer
    # outros.
    if usa_cts and len(cts_aqui := sorted(ctss & set(escoa))) > 1:
        problemas.append(
            f"A unidade usa macrorregião de CTS, e o sistema {sistema_id!r} ficaria "
            f"com {len(cts_aqui)} (" + ", ".join(repr(c) for c in cts_aqui) + "). "
            "Desmarque a opção na unidade para ter mais de uma CTS por sistema."
        )
    return problemas


def pedido_do_corpo(corpo: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """Lê o corpo e devolve `sistema → {componente: jusante}`. Só forma, sem banco.

    Cada bloco traz a LISTA COMPLETA de componentes do sistema, e não os que
    mudaram: é o que dá sentido à ausência. Componente que estava no sistema e não
    veio na lista SAI dele — é assim que "tirar a CTS do sistema" se expressa aqui,
    sem uma segunda rota de remoção. Lista vazia esvazia o sistema, e é legítima.

    É a mesma régua do resto do cadastro (`exigir_ficha_inteira`): o corpo é o
    estado, não um patch, e por isso reenviá-lo não acumula efeito.
    """
    sistemas = corpo.get("sistemas")
    if not isinstance(sistemas, list) or not sistemas:
        raise FichaIncompleta(
            "O corpo precisa trazer `sistemas` com pelo menos um sistema."
        )
    pedido: dict[str, dict[str, str | None]] = {}
    de_quem: dict[str, str] = {}
    for bloco in sistemas:
        if not isinstance(bloco, dict):
            raise FichaIncompleta("Cada item de `sistemas` precisa ser um objeto.")
        sistema_id = id_ou_nada(bloco.get("id"))
        if sistema_id is None:
            raise FichaIncompleta("Todo sistema do corpo precisa de `id`.")
        if sistema_id in pedido:
            raise FichaIncompleta(
                f"O sistema {sistema_id!r} aparece duas vezes no corpo. Junte os "
                "componentes num bloco só — o segundo apagaria o primeiro."
            )
        componentes = bloco.get("componentes")
        if not isinstance(componentes, list):
            raise FichaIncompleta(
                f"O sistema {sistema_id!r} precisa trazer `componentes` como lista. "
                "Lista vazia esvazia o sistema; chave ausente é corpo incompleto, e "
                "aceitá-la esvaziaria o sistema por engano."
            )
        mapa: dict[str, str | None] = {}
        for item in componentes:
            if not isinstance(item, dict):
                raise FichaIncompleta(
                    f"Cada componente de {sistema_id!r} precisa ser um objeto com "
                    "`id` e `jusante`."
                )
            componente_id = id_ou_nada(item.get("id"))
            if componente_id is None:
                raise FichaIncompleta(f"Um componente de {sistema_id!r} veio sem `id`.")
            if componente_id in mapa:
                raise FichaIncompleta(
                    f"{componente_id!r} aparece duas vezes em {sistema_id!r}."
                )
            # UM COMPONENTE, UM SISTEMA — e a checagem tem de ser aqui, e nao no
            # laco de gravacao: `depois[componente]` guarda um valor so, entao o
            # segundo bloco venceria em silencio e o sistema que o pediu primeiro
            # ficaria sem ele, sem nada na resposta dizendo isso.
            if (outro := de_quem.get(componente_id)) is not None:
                raise TopologiaInvalida(
                    f"{componente_id!r} veio em dois sistemas do mesmo envio "
                    f"({outro!r} e {sistema_id!r}). Um componente está num sistema só."
                )
            de_quem[componente_id] = sistema_id
            mapa[componente_id] = id_ou_nada(item.get("jusante"))
        pedido[sistema_id] = mapa
    return pedido
