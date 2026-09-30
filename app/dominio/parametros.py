"""Traduz o corpo do `POST /runs` (front) para o `params` do `controle.run_request`.

Esta e a peca central do disparo, e ela existe porque as duas pontas falam idiomas
diferentes:

    front  -> snake_case, vocabulario da TELA        (CONTRATO.md 4.2)
    job    -> MAIUSCULAS, vocabulario do NOTEBOOK    (job_databricks.MAPA_PARAMS)

E o job e ESTRITO: `_params_para_ler_banco` levanta ValueError em chave
desconhecida, de proposito — um `orcamento` minusculo passando batido faria a
rodada sair sem teto de CAPEX e ninguem notaria. Entao mandar chave a mais aqui
nao e desleixo, e uma rodada que morre em producao.

------------------------------------------------------------------------------
A tradução que NENHUMA das duas pontas faz
------------------------------------------------------------------------------
`redistribuir_orcamento` e `teto_execucao_anual` estao no contrato do front, mas
NAO existem no motor: conferi a assinatura de `ler_banco` e nenhum dos dois esta
la. Eles sao PRE-PROCESSAMENTO, feito na celula 3 do notebook
(`Otimizador_CAPEX_v61_dashboard.ipynb`), que e a fonte de verdade deste fluxo:

    if isinstance(ORCAMENTO, dict) and REDISTRIBUIR_ORCAMENTO:
        _total = sum(ORCAMENTO.values())
        _teto  = TETO_EXECUCAO_ANUAL or max(ORCAMENTO.values())
        _orc_arg = {ano: _teto for ano in ORCAMENTO}
        _orc_total = _total
    else:
        _orc_arg, _orc_total = ORCAMENTO, None

Ou seja: com redistribuicao ligada, cada ano recebe o MESMO teto (o pico, ou o que
o usuario informou) e a SOMA da janela fica travada em `ORCAMENTO_TOTAL`. E assim
que "deixe o otimizador escolher em que ano gastar" e expresso num motor que so
entende teto por ano.

Se isto ficasse no notebook, a tela ofereceria dois controles que o job receberia
como chave desconhecida — e a rodada iria a ERRO com uma mensagem sobre `params`,
sem relacao visivel com o botao que o usuario apertou.
"""

#: A INTERFACE PÚBLICA deste módulo. Tudo o que não está aqui é implementação,
#: e pode mudar sem aviso — a lista é o contrato com quem importa.
__all__ = [
    "CHAVES_DO_JOB",
    "CHAVES_ACEITAS",
    "ETE_FASEADA_SEMPRE",
    "ParametrosInvalidos",
    "montar_params",
    "mes_ano",
]

from typing import Any

#: DE EXECUCAO, e nao do pedido — nao vao para o `ler_banco`, ficam com o job.
#:
#: Era um comentario dentro de `CHAVES_ACEITAS`, e virou constante quando algo
#: passou a precisar da distincao: `controle.parametros` devolve os parametros de
#: uma rodada para que o front possa CLONA-LA, e mandar de volta o `USUARIO`
#: original faria a rodada nova nascer assinada por outra pessoa.
CHAVES_DO_JOB = frozenset({"USUARIO", "MAX_TIME_S", "WORKERS"})

#: A RODADA E SEMPRE FASEADA — a regra do produto, numa definicao so.
#:
#: `ete_faseada` e o interruptor que faz a ETE existir como MODULOS: a nova entra como
#: pacote inicial e ganha modulos de expansao se a vazao pedir; a que ja existe e expandida
#: conforme a demanda. Sem ele a ETE nao vira obra construivel, nunca fica pronta, e o
#: motor recusa a receita de TODA sub-bacia do sistema — na uA1, 142 sub-bacias faturando e
#: R$ 744.050.138,78 de receita com `True` contra ZERO e R$ 0,00 com `False` (29/09/2026).
#:
#: CONSTANTE, e nao um literal em cada lugar, porque sao DOIS caminhos de criacao: o
#: `POST /runs` (`montar_params`) e a variacao de sensibilidade, que CLONA os params da
#: rodada de origem (`variacao.params_da_variacao`) — uma variacao de rodada antiga nascia
#: sem a chave. Duas copias de uma regra divergem em silencio, e esta ja divergiu: ver o
#: bloco onde ela e afirmada em `montar_params`.
ETE_FASEADA_SEMPRE = True

# Espelha `job_databricks.MAPA_PARAMS` + `CHAVES_DO_JOB`. Se o job ganhar um
# parametro novo e este conjunto nao acompanhar, o backend simplesmente nao
# consegue envia-lo — o que e melhor que enviar errado.
CHAVES_ACEITAS = frozenset(
    {
        "ORCAMENTO",
        "ORCAMENTO_TOTAL",
        "HORIZONTE_CAPEX",
        "ETE_FASEADA",
        "ETE_FIXO",
        "METAS_COBERTURA",
        "PESO_COBERTURA",
        "FOCO_COBERTURA",
        "PENALIDADE_COBERTURA",
        "PESO_CIDADE",
        "DATA_INICIO",
        "REGIONAL",
        "UNIDADE",
        "CURVA_ADOCAO",
        "BASE_RECEITA",
        "USAR_CTS",
        "ANOS_EXTRA_CONCLUSAO",
        "COBERTURA_SO_RESIDENCIAL",
        "CTS_NA_COBERTURA",
        "UNIDADE_COBERTURA",
    }
    | CHAVES_DO_JOB
)


class ParametrosInvalidos(ValueError):
    """Recusa que o usuario consegue entender — vira 422 com a mensagem no corpo."""


def _orcamento_por_ano(corpo: dict[str, Any]) -> dict[int, float]:
    """O cronograma anual, venha ele em qual dos dois modos vier.

    O contrato do front (4.2) diz que os dois blocos sao exclusivos: ou
    `orcamento` (cronograma por ano), ou `orcamento_anual` + `horizonte_capex`.
    Aceitar os dois juntos seria escolher um em silencio.
    """
    cronograma = corpo.get("orcamento")
    anual = corpo.get("orcamento_anual")

    # `"abc"` em vez de dict passava direto e estourava la na frente com AttributeError
    # -> 500. Recusar aqui devolve 422 com o que fazer.
    if cronograma is not None and not isinstance(cronograma, dict):
        raise ParametrosInvalidos(
            "O orçamento por ano precisa ser um objeto {ano: valor}."
        )

    if cronograma and anual:
        raise ParametrosInvalidos(
            "Informe o cronograma por ano OU um valor anual único, nunca os dois."
        )

    if cronograma:
        # As chaves chegam como string no JSON ("2026"); o motor so reconhece
        # cronograma por ano se elas forem int. Sem esta conversao, o valor cai no
        # ramo "orcamento por unidade", a unidade nao e encontrada e o teto vira
        # infinito — rodada sem restricao anual, que estoura no CP-SAT.
        try:
            por_ano = {int(ano): float(v) for ano, v in cronograma.items()}
        except (TypeError, ValueError) as e:
            raise ParametrosInvalidos(f"Cronograma de orçamento inválido: {e}") from e
        if not por_ano:
            raise ParametrosInvalidos("O cronograma de orçamento está vazio.")
        return por_ano

    if anual:
        horizonte = corpo.get("horizonte_capex")
        if not horizonte:
            raise ParametrosInvalidos(
                "Com valor anual único é preciso informar o horizonte de CAPEX."
            )
        # O ano-base sai do cadastro, do lado do motor. Aqui o que importa e o
        # NUMERO de anos: o job repassa `horizonte_capex` e o motor monta a janela.
        return {}

    raise ParametrosInvalidos("A rodada precisa de teto anual: informe o orçamento.")


def montar_params(corpo: dict[str, Any], unidade_id: str, usuario: str) -> dict[str, Any]:
    """O `params` que vai para `controle.run_request`, pronto e validado.

    `usuario` vem do token, nunca do corpo: e ele que amarra a rodada a uma pessoa
    no historico, e aceita-lo do cliente seria aceitar que qualquer um assinasse
    a simulacao de outro.
    """
    params: dict[str, Any] = {"UNIDADE": unidade_id, "USUARIO": usuario}

    por_ano = _orcamento_por_ano(corpo)

    if por_ano and corpo.get("redistribuir_orcamento"):
        # Pre-processamento da celula 3 do notebook — ver o docstring do modulo.
        #
        # A TELA NAO OFERECE MAIS a redistribuicao (decisao de produto, reversivel),
        # entao este ramo nao e alcancado pelo produto hoje. Ele FICA, e nao vira
        # codigo morto por isso: e a capacidade que o backend tem de oferecer se o
        # controle voltar, esta coberta por teste, e some do produto sem sumir da
        # documentacao. Corpo que ainda mande os dois campos e tratado como sempre.
        total = sum(por_ano.values())
        teto = corpo.get("teto_execucao_anual") or max(por_ano.values())
        if teto <= 0:
            raise ParametrosInvalidos(
                "O teto de execução anual precisa ser maior que zero. "
                "Deixe em branco para usar o pico do cronograma."
            )
        params["ORCAMENTO"] = {ano: float(teto) for ano in por_ano}
        params["ORCAMENTO_TOTAL"] = total
    elif por_ano:
        params["ORCAMENTO"] = por_ano
    else:
        # Redistribuir so faz sentido sobre um CRONOGRAMA: e ele que tem uma soma a
        # travar e picos a achatar. Com valor anual unico todo ano ja tem o mesmo
        # teto, entao a opcao nao teria efeito — e ignorar em silencio faria a tela
        # oferecer um controle que nao muda nada.
        if corpo.get("redistribuir_orcamento") or corpo.get("teto_execucao_anual"):
            raise ParametrosInvalidos(
                "Redistribuir o orçamento só vale para o cronograma por ano. "
                "Com valor anual único, todo ano já tem o mesmo teto."
            )
        params["ORCAMENTO"] = float(corpo["orcamento_anual"])
        params["HORIZONTE_CAPEX"] = int(corpo["horizonte_capex"])

    # Repasse direto: nome do front -> nome do job. Chave ausente no corpo NAO
    # entra no params, para o job usar o default do `ler_banco` — se o backend
    # inventasse default proprio, o mesmo pedido daria planos diferentes aqui e no
    # notebook, que foi exatamente o bug mais caro da revisao do pacote.
    # `PESO_CIDADE` NAO esta aqui, e a ausencia E o padrao pedido: todas as
    # cidades pesam 1. O motor multiplica a contribuicao de cada uma por
    # `peso_cidade.get(cidade, 1.0)` (`otimizador_capex_cpsat63.py`), entao sem o
    # parametro o multiplicador e 1 para todas. Mandar `{}` daria no mesmo e
    # sugeriria que ha escolha.
    #
    # `ETE_FIXO` NAO esta aqui, e a ausencia e regra de negocio: o tratamento da ETE
    # sai da FICHA dela, e nao da rodada. ETE com terreno e numero de modulos
    # informados e NOVA e entra como pacote inicial; a que ja existe e expandida em
    # modulos conforme a vazao passa da capacidade ociosa. O motor decide isso por
    # ETE (`nova=Sim` ou `capex_terreno > 0`).
    #
    # `ETE_FASEADA` ESTAVA AQUI e SAIU DAQUI em 29/09/2026 — ver o bloco onde ele e
    # afirmado, mais abaixo. A regra dizia "quem afirma `True` e o executor", e dois
    # executores nao a cumpriam igual.
    DIRETO = {
        "foco_cobertura": "FOCO_COBERTURA",
        "penalidade_cobertura": "PENALIDADE_COBERTURA",
        "base_receita": "BASE_RECEITA",
        "curva_adocao": "CURVA_ADOCAO",
        "usar_cts": "USAR_CTS",
        "cobertura_so_residencial": "COBERTURA_SO_RESIDENCIAL",
        # A REGUA DA COBERTURA — ligacoes | economias | populacao. Era coluna de
        # cadastro POR CIDADE ate a migracao 019, e nao era dado: e a lente com
        # que se olha o mesmo cadastro. Vale para a unidade inteira, porque
        # comparar dois planos da mesma unidade medidos em reguas diferentes por
        # cidade nao respondia pergunta nenhuma.
        "unidade_cobertura": "UNIDADE_COBERTURA",
        "data_inicio": "DATA_INICIO",
    }
    for origem, destino in DIRETO.items():
        if origem in corpo:
            params[destino] = corpo[origem]

    # A CTS CONTA NA COBERTURA? (29/09/2026)
    #
    # A pergunta só existe com `USAR_CTS` ligada: sem CTS não há nó de coletor, e a
    # resposta não muda nada. Por isso ela NÃO entra no `DIRETO` — ali a chave viajaria
    # sempre, e duas rodadas idênticas que diferissem só neste campo inútil deixariam de
    # deduplicar, gastando cluster para produzir o mesmo resultado.
    #
    # RECUSA EM VEZ DE IGNORAR quando vem sem CTS: a tela só oferece o botão com CTS
    # ligada, então um corpo com os dois discordando é erro de quem chama, e aceitar em
    # silêncio gravaria um pedido que promete um recorte que não aconteceu.
    _cts_lig = bool(params.get("USAR_CTS", True))
    if "cts_na_cobertura" in corpo:
        if not _cts_lig and not bool(corpo["cts_na_cobertura"]):
            raise ParametrosInvalidos(
                "Tirar a CTS da cobertura só faz sentido com a CTS ligada na rodada. "
                "Ligue o uso de CTS ou deixe a opção de cobertura em branco."
            )
        if _cts_lig:
            params["CTS_NA_COBERTURA"] = bool(corpo["cts_na_cobertura"])

    # `METAS_COBERTURA` NAO E PRODUZIDO AQUI, e a ausencia e a regra de negocio:
    # as metas vem SEMPRE da base. O unico descarte legitimo e por ANO — meta fora
    # da janela de CAPEX nao e cobrada —, e quem o aplica e o motor, na avaliacao
    # (`otimizador_capex_v62.py`: `idx >= anos_capex -> continue`). Nao e escolha
    # de quem dispara a rodada, entao nao vira parametro.
    #
    # Chave ausente e exatamente como se pede o comportamento certo: sem ela o
    # motor usa o proprio default, que e carregar as metas da planilha.
    #
    # HOUVE uma escolha aqui, e ela quebrou duas vezes. Primeiro `None if valor in
    # (None, "cadastro")` colapsava "ignorar" e "usar o cadastro" no mesmo valor —
    # e como `metas_cobertura=None` manda o motor CARREGAR, quem pedia para ignorar
    # rodava com as metas enquanto a tela avisava o contrario. Corrigido o colapso,
    # a opcao passou a funcionar: produzia rodada sem meta nenhuma, que a regra nao
    # admite. Saiu inteira. Corpo antigo que ainda mande `metas_cobertura` e
    # ignorado em silencio — o resultado e o mesmo que a regra pede.

    # ANOS_EXTRA_CONCLUSAO E FIXO EM ZERO, e a tela nao o oferece mais: a obra
    # inicia e conclui DENTRO da janela de CAPEX, sem rabo custeado pela sobra
    # acumulada.
    #
    # AFIRMADO, e nao omitido — aqui a receita do `metas_cobertura` faria o
    # contrario do pedido. O default de `anos_extra_conclusao` no `ler_banco` e
    # **3**; chave ausente daria tres anos de rabo, nao zero. E o mesmo cuidado do
    # `ete_faseada`, so que do outro lado: la a omissao desligaria o que se quer,
    # aqui ela ligaria o que nao se quer.
    #
    # Viaja no `params` de proposito, em vez de o executor fixar: assim o
    # historico REGISTRA o que a rodada usou, e o modal de detalhes o mostra. Uma
    # rodada antiga com 3 continua contando a verdade dela.
    params["ANOS_EXTRA_CONCLUSAO"] = 0

    # ETE_FASEADA SEMPRE True, E AFIRMADO AQUI — a regra do produto, no pedido.
    #
    # E o interruptor que faz a ETE existir como MODULOS: com ele, a ETE nova entra
    # como PACOTE INICIAL (terreno + os modulos do cadastro) e ganha modulos de
    # expansao faseados se a vazao passar da capacidade; a que ja existe e expandida
    # em modulos conforme a demanda. Sem ele, a ETE nao vira obra construivel, nunca
    # fica pronta, e o motor recusa a receita de TODA sub-bacia do sistema — medido na
    # uA1 em 29/09/2026: 142 sub-bacias faturando e R$ 744.050.138,78 de receita com
    # `True`, contra ZERO sub-bacias e R$ 0,00 com `False`. Nao e outra modelagem; e
    # rodada quebrada.
    #
    # ANTES ELE NAO VIAJAVA, e a regra era "quem afirma `True` e o executor". Duas
    # linhas de codigo se contradiziam:
    #
    #   `dev/worker.py`         afirmava `ete_faseada=True`
    #   `job_databricks.py`     nao afirmava nada, por regra propria ("chave ausente
    #                           nao vira default do job"), e o `ler_banco` defaulta
    #                           False
    #
    # Ou seja: o MESMO pedido rodava faseado no executor local e nao-faseado no job.
    # Achado em 29/09/2026, perseguindo uma suspeita da revisao do Codex.
    #
    # AFIRMAR NO PEDIDO resolve a causa, e nao o sintoma — e e o mesmo padrao do
    # `ANOS_EXTRA_CONCLUSAO` acima e do `MAX_TIME_S` abaixo, pela mesma razao: sem a
    # chave, cada consumidor usa o proprio default e a mesma rodada roda outro
    # problema conforme quem a executa. De brinde, o historico passa a REGISTRAR o
    # modo, e ele entra no digest da deduplicacao: se um dia alguem quiser comparar
    # faseado com nao-faseado, as duas rodadas deixam de ser "a mesma simulacao".
    params["ETE_FASEADA"] = ETE_FASEADA_SEMPRE

    # MAX_TIME_S FIXO EM 1000s, e a tela nao o oferece mais: quanto tempo o solver
    # tem e afinacao de execucao, nao decisao de negocio — quem dispara a rodada
    # nao tem como calibrar isso.
    #
    # AFIRMADO, como o `ANOS_EXTRA_CONCLUSAO` e ao contrario do `PESO_CIDADE`: sem
    # a chave, cada consumidor usaria o proprio default (o executor local cai no
    # `--tempo` da linha de comando), e a mesma rodada teria tempos diferentes
    # conforme quem a executa. Viaja no `params` para o historico registrar o que
    # a rodada teve.
    #
    # WORKERS nao entra: e paralelismo do processo que executa, e depende da
    # maquina dele. O executor usa o proprio padrao.
    # 1000s, ESCOLHIDO POR MEDICAO em 13/08/2026 — uA3 (67 cidades, 8.079 obras),
    # mesmos parametros, so o teto mudando:
    #
    #             500s                      1000s
    #   status    OTIMO                     VIAVEL(limite de tempo)
    #   VPL       141.685.312               170.430.575   (+20,3%)
    #   plano     815 obras / 47,764%       817 obras / 47,845%
    #   total     960s                      1.719s
    #
    # O PLANO E QUASE O MESMO E O VPL E 20% MAIOR, e a causa nao e ruido: com 500s
    # a FASE 3 (desempate por retorno) nao chegou a rodar. O motor reparte
    # `max_time_s*1.35` entre as fases e pula a terceira quando sobram menos de 5s
    # (`otimizador_capex_cpsat63.py`, `if _resta<5.0: return plano2`), devolvendo o
    # status da fase 2 — que provou o proprio otimo. Por isso o rotulo melhor sai
    # na rodada pior: `OTIMO` aqui significa "a ultima fase que rodou provou o
    # otimo DELA", e nao "melhor plano".
    #
    # 500 nao serve para a maior unidade: desliga em silencio a fase que otimiza
    # retorno. Nas pequenas o teto nao morde — a uA1 fecha em 14s, parando pelo
    # gap muito antes. Entao 1000 e o unico dos dois que nao erra em nenhuma ponta,
    # ao custo de 13 minutos na uA3.
    params["MAX_TIME_S"] = 1000

    _validar(params)
    return params


def mes_ano(data_inicio: str | None) -> tuple[int, int] | None:
    """`"2027-01"` (o que a tela manda) -> `(1, 2027)` (o que o motor entende).

    A CONVERSAO NAO E COSMETICA. O motor faz
    `_p = str(data_inicio).split("-"); _mi, _ai = int(_p[0]), int(_p[1])` — ou seja,
    ele le MES-ANO. A tela coleta ANO-MES (o placeholder dela e "2026-06"). Passar
    a string crua faria `_mi=2027, _ai=1`, e o deslocamento absurdo que sai dali e
    zerado pelo `max(0, ...)` do proprio motor: a data escolhida sumiria sem erro
    nenhum. Silencio e pior que nao repassar.

    Devolve TUPLA, e nao string remontada, porque o motor trata
    `isinstance(data_inicio, (list, tuple))` como caminho proprio — sem depender de
    ordem em texto, que foi a origem da confusao.

    Mora aqui, e nao no worker, pela razao do docstring do modulo: e a traducao
    entre a convencao da tela e a do job, e ela nao pode ter duas casas.
    """
    # Vazio E so-espacos sao "ninguem digitou": o motor deriva a data sozinho
    # (primeiro ano do CAPEX x dia da rodada). Falhar em `"  "` seria recusar uma
    # rodada por um espaco que a pessoa nao ve.
    if not data_inicio or not str(data_inicio).strip():
        return None
    partes = str(data_inicio).strip().replace("/", "-").split("-")
    if len(partes) != 2:
        raise ParametrosInvalidos(f"Data de início fora do formato AAAA-MM: {data_inicio!r}")
    try:
        ano, mes = int(partes[0]), int(partes[1])
    except ValueError:
        raise ParametrosInvalidos(
            f"Data de início fora do formato AAAA-MM: {data_inicio!r}"
        ) from None
    if not 1 <= mes <= 12:
        # Provavel MM-AAAA invertido chegando de algum lugar. Falhar aqui e melhor
        # que deslocar a janela para um mes que nao existe.
        raise ParametrosInvalidos(f"Data de início com mês fora de 1..12: {data_inicio!r}")
    return (mes, ano)


def _validar(params: dict[str, Any]) -> None:
    desconhecidas = sorted(set(params) - CHAVES_ACEITAS)
    if desconhecidas:
        # Nao chega ao usuario: e erro de programacao aqui dentro, e o job
        # levantaria o mesmo depois. Falhar antes de gravar poupa uma rodada morta.
        raise ParametrosInvalidos(
            f"params com chaves que o job recusaria: {desconhecidas}. "
            f"Aceitas: {sorted(CHAVES_ACEITAS)}"
        )

    foco = params.get("FOCO_COBERTURA")
    if foco is not None:
        # `float("abc")` levantava ValueError cru e virava 500. O usuario mexeu num
        # controle da tela; a resposta tem de dizer qual controle.
        try:
            foco = float(foco)
        except (TypeError, ValueError):
            raise ParametrosInvalidos(
                "O foco em cobertura precisa ser um número entre 0 e 1."
            ) from None
        if not 0 <= foco <= 1:
            raise ParametrosInvalidos("O foco em cobertura precisa estar entre 0 e 1.")

    orc = params.get("ORCAMENTO")
    if isinstance(orc, dict) and not any(v > 0 for v in orc.values()):
        raise ParametrosInvalidos(
            "Pelo menos um ano precisa de verba — uma rodada sem teto anual "
            "não tem o que otimizar."
        )
