"""As formas dos payloads de RESULTADO — o contrato, escrito de um lado só.

## Por que estes modelos existem

Um `dict[str, Any]` de retorno funciona e esconde uma classe inteira de defeito:
um campo que o front declara OBRIGATÓRIO e o backend não manda só aparece como
`undefined.map` na tela do usuário. Nada acusa antes — `test_contrato.py` confere
QUAIS rotas existem, não o que elas devolvem.

Declarar a forma faz o próprio FastAPI validar a resposta contra o modelo: o
campo que faltar quebra o teste da rota, e não o navegador.

Com um modelo por payload, o mesmo erro vira erro de servidor no primeiro pedido
em vez de tela branca semanas depois, o `/api/openapi.json` passa a descrever o
formato de verdade, e o Pydantic serializa do lado Rust.

## Como estão ligados

Por `response_model=` no decorador, e NÃO como anotação de retorno das funções.
Os repositórios devolvem o dicionário cru do asyncpg; forçá-los a construir
modelos espalharia a forma por duas camadas. `response_model` valida, filtra e
documenta exatamente o mesmo, deixando a rota devolver o que já devolvia.

## O que NÃO está modelado, e por quê

`RunResumo.pedido` é o corpo do pedido gravado em `controle.run_request`: o dict
de parâmetros do MOTOR, cujas chaves mudam com a versão dele (`ORCAMENTO` ora é
número, ora é um mapa ano→teto; `PESO_CIDADE` é um mapa aberto). Modelar isso
seria copiar para cá um contrato que não é nosso e que envelhece sozinho —
`dict[str, Any]` diz a verdade: é opaco de propósito, e o front o exibe como
lista de chave e valor.

## `| None` é parte do contrato, não descuido

A regra do serviço é que `null` significa "não existe", nunca 0
(`infra/repositorios/cascata.py`).
Um campo opcional aqui é afirmação: ocupação de ETE com capacidade zero é `null`
porque a conta não existe, e um `0.0` afirmaria ETE vazia.
"""

from typing import Any

from pydantic import BaseModel


# ===========================================================================
#  Peças reaproveitadas
# ===========================================================================
class ParcelaDaCascata(BaseModel):
    rotulo: str
    tipo: str
    valor: float


class ComponenteDoAno(BaseModel):
    componente: str
    #: `null` quando o componente aparece no ano com mais de uma unidade física:
    #: metro não soma com unidade. `capex` sobrevive porque reais somam.
    quantidade: float | None
    unidade: str | None
    precoUnitario: float | None
    capex: float


class ElementoDoAno(BaseModel):
    ano: int
    porComponente: list[ComponenteDoAno]


class ObraDoAno(BaseModel):
    componente: str
    #: CONTAGEM de obras — não confundir com a quantidade física de
    #: `ComponenteDoAno`. São perguntas diferentes sobre a mesma linha.
    quantidade: int


class ObrasDoAno(BaseModel):
    ano: int
    porComponente: list[ObraDoAno]


class Componente(BaseModel):
    """Uma obra vista de dentro de um nó do fluxo, ou de uma sub-bacia."""

    obraId: str
    nome: str
    situacao: str
    quantidade: float | None
    unidade: str | None
    precoUnitario: float | None
    capex: float
    anoInicio: int | None
    prazoMeses: int | None


# ===========================================================================
#  §3.1 — histórico
# ===========================================================================
class ComentarioDaRodada(BaseModel):
    texto: str
    autor: str
    atualizadoEm: str


class ParametrosRodada(BaseModel):
    orcamento: float
    janelaCapex: int
    focoCobertura: float
    usarCts: bool
    baseReceita: str
    coberturaSoResidencial: bool | None = None
    #: A regua da cobertura: 'ligacoes' | 'economias' | 'populacao'. Era coluna de
    #: cadastro por cidade ate a migracao 019 — hoje e parametro da rodada, e a
    #: tela precisa dele para nao comparar dois planos medidos em moedas
    #: diferentes como se fossem o mesmo numero.
    unidadeCobertura: str | None = None


class MetricasCapa(BaseModel):
    vpl: float
    capex: float
    coberturaFimPct: float
    metasAtingidas: int
    metasTotal: int
    obrasConstruidas: int
    obrasTotal: int
    usoOrcamentoPct: float
    ebitdaTotal: float | int


class RunResumo(BaseModel):
    runId: str
    nome: str | None
    unidadeId: str
    unidadeNome: str
    dataHora: str
    autor: str
    duracaoS: float | int | None
    status: str
    favorita: bool
    publicada: bool
    comentario: ComentarioDaRodada | None = None
    progresso: int | None = None
    erro: str | None = None
    solver: str | None = None
    parametros: ParametrosRodada | None = None
    metricas: MetricasCapa | None = None
    #: Opaco de propósito — ver o comentário do topo.
    pedido: dict[str, Any] | None = None


# ===========================================================================
#  §3.3 — cabeçalho da rodada
# ===========================================================================
class KpisDaRodada(BaseModel):
    vpl: float
    capexTotal: float
    opexTotal: float
    receitaTotal: float
    coberturaFimPct: float
    metasAtingidas: int
    metasTotal: int
    obrasConstruidas: int
    obrasTotal: int
    obrigatoriasConstruidas: int
    obrigatoriasTotal: int
    subbaciasFaturando: int
    subbaciasTotal: int


class VariacaoDe(BaseModel):
    """De qual rodada esta aqui e uma variacao de orcamento."""

    runId: str
    #: O rotulo da BASE. O rotulo desta rodada diz "+10%" e nao diz de que.
    nome: str | None
    degrau: int
    estimativa: bool


class RunMeta(BaseModel):
    runId: str
    nome: str | None
    #: AUSENTE quando a rodada nao e variacao de ninguem — a maioria. Presente,
    #: ela e ponto da curva de sensibilidade de outra: a tela nao oferece analisar
    #: a sensibilidade DELA, e diz de onde ela veio.
    variacaoDe: VariacaoDe | None = None
    unidadeId: str
    unidadeNome: str
    dataHora: str | None
    autor: str | None
    status: str
    statusTexto: str | None
    kpis: KpisDaRodada
    parametros: ParametrosRodada


# ===========================================================================
#  §3.4 — painel global
# ===========================================================================
class AnoDoPainel(BaseModel):
    ano: int
    capex: float
    opex: float
    receita: float
    #: `null` no ano fora da janela de CAPEX — não há teto a comparar.
    tetoCapex: float | None


class PontoDaCurvaS(BaseModel):
    mes: str
    capexMes: float
    capexAcumulado: float


class CapexPorComponente(BaseModel):
    componente: str
    capex: float
    pctDoTotal: float | None
    obras: int
    #: Ausentes quando não há o que contar (ETE) ou quando o componente aparece
    #: com mais de uma unidade física.
    unidadesConstruidas: float | None
    unidade: str | None


class PainelGlobal(BaseModel):
    anos: list[AnoDoPainel]
    curvaS: list[PontoDaCurvaS]
    cascata: list[ParcelaDaCascata]
    capexPorComponente: list[CapexPorComponente]
    obrasPorAno: list[ObrasDoAno]
    elementosPorAno: list[ElementoDoAno]
    fimCapex: int | None


# ===========================================================================
#  §3.5 — EBITDA
# ===========================================================================
class AnoDeEbitda(BaseModel):
    ano: int
    ebitda: float
    margemPct: float | None


class PainelEbitda(BaseModel):
    anos: list[AnoDeEbitda]
    total: float
    #: `null` quando nunca vira positivo.
    anoViraPositivo: int | None
    fimCapex: int | None


# ===========================================================================
#  §3.6 e §3.7 — cidades
# ===========================================================================
class PontoDeCobertura(BaseModel):
    ano: int
    coberturaPct: float


class MetaDeCobertura(BaseModel):
    ano: int
    alvoPct: float | None
    realizadoPct: float | None
    #: TRÊS estados: `null` é meta fora da janela, que ninguém avaliou — dizer
    #: "não atingida" ali reportaria falha inexistente.
    atingida: bool | None
    dentroDaJanela: bool


class CidadeLinha(BaseModel):
    id: str
    nome: str
    vpl: float
    capex: float
    coberturaFimPct: float | None
    metasAtingidas: int
    metasTotal: int
    sistemas: int


class FaixaDeParidade(BaseModel):
    coberturaPct: float
    paridade: float
    ehBase: bool
    ehFinal: bool


class ParidadeDaCidade(BaseModel):
    #: PENDENTE: as faixas vivem em `input.fator_esgoto`, que é cadastro, e não
    #: nas tabelas de resultado. Sai vazia até o job publicá-las.
    faixas: list[FaixaDeParidade]
    paridadeInicial: float | None
    paridadeFinal: float | None
    houveDegrau: bool
    vpEfeitoBase: float | int
    pctDoVplDaCidade: float | None


class SistemaDaCidade(BaseModel):
    id: str
    nome: str
    subbacias: int
    faturando: int
    capex: float | None
    ocupacaoPct: float | None


class CidadeDetalhe(BaseModel):
    id: str
    nome: str
    fimConcessao: int | None
    fimCapex: int | None
    capexTotal: float
    vpl: float
    ligacoesNovas: float | None
    coberturaBasePct: float | None
    coberturaFinalPct: float | None
    cobertura: list[PontoDeCobertura]
    metas: list[MetaDeCobertura]
    cascata: list[ParcelaDaCascata]
    paridade: ParidadeDaCidade
    sistemas: list[SistemaDaCidade]
    elementosPorAno: list[ElementoDoAno]


# ===========================================================================
#  Explicabilidade — "por que o plano não fatura 100%"
# ===========================================================================
class ObraForaDoPlano(BaseModel):
    """Uma obra que não entrou — as maiores de cada tópico, por CAPEX."""

    obraId: str
    componente: str
    cidadeId: str | None
    sistemaId: str | None
    #: O nó que a obra atende. Vazio nas de transporte, que não têm nó próprio.
    subBaciaId: str | None
    capex: float
    #: Ligações que a obra traria. ZERO em obra de transporte — é a regra do
    #: domínio: só ligação e CTS faturam, o resto é CAPEX e OPEX que existe para
    #: o esgoto chegar à ETE.
    ligacoes: float


class ComponenteDoTopico(BaseModel):
    """Quantas obras de cada TIPO o tópico tem — a leitura de dentro dele."""

    componente: str
    obras: int
    capex: float


class TopicoDaExplicabilidade(BaseModel):
    """Um dos três motivos de uma obra não entrar, agrupado pelo que se faz nele.

    `topico` é o CÓDIGO (`orcamento` | `nao_se_paga` | `depende` | `outros`), e
    não o rótulo: a tela escreve a frase em português, e mudar texto de tela não
    pode mudar contrato. `outros` é válvula — categoria nova do motor aparece
    nele em vez de sumir do agregado.
    """

    topico: str
    obras: int
    capex: float
    ligacoes: float
    porComponente: list[ComponenteDoTopico]
    #: As dez de maior CAPEX. NÃO é a lista completa, e a tela diz isso: mandar
    #: as 6.765 trocaria uma tela pesada por uma ilegível.
    maiores: list[ObraForaDoPlano]


class EloQueTrava(BaseModel):
    obraId: str
    componente: str
    cidadeId: str | None
    sistemaId: str | None
    subBaciaId: str | None
    bloqueia: int
    vazaoLiberada: float


class AnoDoCenario(BaseModel):
    """Um ano da janela: o que o plano faz nele, e o que faltaria investir."""

    ano: int
    orcado: float
    noPlano: float
    obrasNoPlano: int
    #: O que falta, rateado pelo PESO deste ano no orcamento atual — mesma forma,
    #: escala maior. Nao e otimizacao, e a tela diz isso.
    faltaQueSePaga: float
    faltaTodas: float


class EscopoDoCenario(BaseModel):
    """Quanto falta, em tres reguas da mesma coisa.

    `fator` responde "de quantas vezes teria de ser o orcamento"; `anos` responde
    "quantos anos ao ritmo de hoje". Sao o mesmo numero — e ter os dois e o que
    faz a ideia atravessar para quem nao lida com orcamento todo dia.
    """

    obras: int
    capex: float
    fator: float
    anosAoRitmoDeHoje: float


class PodemComecarCedo(BaseModel):
    """Quantas das obras que ficaram fora poderiam comecar JA no primeiro ano.

    E o que sobrou da pergunta "sem teto, o que entra em cada ano?": a resposta
    nao dava grafico (quase tudo no primeiro ano, e tres anos vazios), mas da
    frase — e a frase e o achado. Tirado o dinheiro, nao ha nada segurando obra
    nenhuma: o cronograma do plano e artefato de orcamento, nao de engenharia.
    """

    obras: int
    de: int


class CenarioAnual(BaseModel):
    """DE QUANTO TERIA DE SER O ORCAMENTO ANUAL para fazer tudo na MESMA janela.

    Substitui duas perguntas que os dados recusaram: "sem teto, o que entra em
    cada ano" (6.645 das 7.325 obras podem comecar no primeiro — vira uma torre)
    e "quantos anos ao ritmo de hoje" (64 — setenta barras nao sao um grafico).
    Fixada a janela, a resposta cabe em seis barras.
    """

    anos: list[AnoDoCenario]
    podemComecarCedo: PodemComecarCedo
    anosDaJanela: int
    orcamentoAnualDeHoje: float
    obrasNoPlano: int
    capexNoPlano: float
    queSePaga: EscopoDoCenario
    todas: EscopoDoCenario


class ExplicabilidadeGlobal(BaseModel):
    """As OBRAS que ficaram fora do plano, em três tópicos.

    Era por SUB-BACIA, e a troca não é de rótulo: obra de transporte não tem
    sub-bacia própria, então 85% do CAPEX que ficou de fora não cabia na lista
    antiga — 4.531 obras e R$ 4,4 bi invisíveis no maior run publicado.

    `obrasCandidatas` é o denominador (o que o motor considerou), `obrasNoPlano`
    o que entrou, e `deTerceiros` fica FORA dos tópicos: é obra que acontece e
    outro paga — não é decisão de investimento do plano.
    """

    obrasFora: int
    obrasCandidatas: int
    obrasNoPlano: int
    capexFora: float
    ligacoesFora: float
    deTerceiros: int
    topicos: list[TopicoDaExplicabilidade]
    elos: list[EloQueTrava]


# ===========================================================================
#  Sensibilidade — a curva e o teto
# ===========================================================================
class DegrauDoTeto(BaseModel):
    degrau: int
    folga: float
    subbaciasNoMaximo: int
    vazaoNoMaximo: float


class TetoDeSensibilidade(BaseModel):
    #: A SOMA DOS ANOS, e nao o valor anual — ver `dominio/teto.py`.
    orcamentoTotal: float
    #: Quantos anos tem orcamento maior que zero. A tela usa para dizer sobre
    #: quantos anos o acrescimo em reais esta somado.
    anosDoPlano: int
    subbaciasFora: int
    subbaciasSemCapexProprio: int
    capexParaTodas: float
    vazaoTotalPresa: float
    degraus: list[DegrauDoTeto]


class ObrasDoComponente(BaseModel):
    componente: str
    nome: str
    construidas: int


class PontoDaCurva(BaseModel):
    degrau: int
    runId: str
    status: str
    #: `True` para a estimativa rapida (solver de 60s). A tela DEVE dizer isto:
    #: e a diferenca entre um numero para orientar e um numero para decidir.
    estimativa: bool
    vpl: float | None = None
    coberturaFimPct: float | None = None
    metasAtingidas: int | None = None
    metasTotal: int | None = None
    capexTotal: float | None = None
    tempoS: float | None = None
    #: O motivo da falha, quando `status` e ERRO. A tela mostra a frase inteira:
    #: ela costuma dizer o que fazer ("tente com MAX_TIME_S maior").
    erro: str | None = None
    #: Vazia enquanto a rodada nao publicou — nao ha plano ainda, e uma lista de
    #: zeros seria lida como "nao construiu nada".
    obras: list[ObrasDoComponente] = []


class Sensibilidade(BaseModel):
    teto: TetoDeSensibilidade | None
    pontos: list[PontoDaCurva]


# ===========================================================================
#  Plano de obras
# ===========================================================================
class ObraLinha(BaseModel):
    obraId: str
    componente: str
    situacao: str
    cidadeId: str | None
    sistemaId: str | None
    #: `null` para ETE e módulo de ETE — não têm sub-bacia própria.
    subBaciaId: str | None
    capex: float | None
    quantidade: float | None
    unidade: str | None
    #: POR QUE a obra está no plano: `terceiro` | `obrigatoria` | `escolhida`.
    #: A mesma partição do cronograma, e disjunta pela mesma razão.
    recorte: str
    anoInicio: int | None
    #: Ano de CONCLUSÃO, 'AAAA-MM'. Para obra de terceiro é a única data que o
    #: motor calcula — e é por ela que a lista de um ano a inclui.
    dataPronta: str | None
    prazoMeses: int | None


class ObrasPagina(BaseModel):
    #: Total do resultado FILTRADO, e não o da rodada.
    total: int
    itens: list[ObraLinha]


class ComponenteDoCronograma(BaseModel):
    componente: str
    obras: int
    capex: float


class RecorteDoAno(BaseModel):
    """Um dos três recortes de um ano — as parcelas que somadas dão "todas"."""

    obras: int
    capex: float
    porComponente: list[ComponenteDoCronograma]


class AnoDeObras(BaseModel):
    """Um ano do cronograma, particionado por POR QUE a obra está no plano.

    Os três recortes são disjuntos e exaustivos por construção (ver
    `RECORTE_SQL` em `nivel_global`), então "todas as obras" é a soma deles — e
    o cliente a calcula, em vez de recebê-la pronta e poder divergir das
    parcelas sem nada acusar.

    O ANO NÃO SIGNIFICA O MESMO PARA TODO RECORTE: obra da Aegea entra pelo ano
    em que COMEÇA, obra de terceiro pelo ano em que fica PRONTA — que é a única
    data que o motor calcula para ela. Ver `ANO_SQL` em `nivel_global`.
    """

    ano: int
    terceiro: RecorteDoAno
    obrigatoria: RecorteDoAno
    escolhida: RecorteDoAno


class CronogramaDeObras(BaseModel):
    anos: list[AnoDeObras]


# ===========================================================================
#  §3.8 — fluxo de escoamento do sistema
# ===========================================================================
class NoDoFluxo(BaseModel):
    id: str
    tipo: str
    vazao: float | None
    fatura: bool | None
    pareadaCom: str | None
    #: `null` é "liga direto na ETE" — mandamos como veio, sem inventar um id.
    jusante: str | None
    componentes: list[Componente]


class EteDoFluxo(BaseModel):
    id: str
    nome: str
    capacidade: float | None
    vazaoConectada: float | None
    vazaoNaoAtendida: float | None
    ocupacaoPct: float | None
    modulos: list[Componente]


class Fluxo(BaseModel):
    sistemaId: str
    sistemaNome: str
    cidadeId: str
    cidadeNome: str
    subbacias: int
    faturando: int
    capexConstruido: float | None
    nos: list[NoDoFluxo]
    ete: EteDoFluxo
    elementosPorAno: list[ElementoDoAno]


# ===========================================================================
#  §3.9 — sub-bacia
# ===========================================================================
class PontoDeReceita(BaseModel):
    ano: int
    direta: float | None
    indireta: float | None


class SeFosseLigada(BaseModel):
    receita: float | None
    capexSozinha: float | None
    opex: float | None
    saldoSozinha: float | None
    saldoComRateio: float | None


class ExplicacaoDaSubBacia(BaseModel):
    categoria: str | None
    #: Só quando o elo é obra DESTA sub-bacia: um id de outro nó levaria a uma
    #: ficha plausível e errada.
    elo: str | None
    narrativa: str | None
    #: `null` quando a sub-bacia fatura — não há contrafactual a montar.
    seFosseLigada: SeFosseLigada | None


class ElementoDaSubBacia(BaseModel):
    obraId: str
    componente: str
    situacao: str
    quantidade: float | None
    unidade: str | None
    precoUnitario: float | None
    capex: float
    anoInicio: int | None
    prazoMeses: int | None


class SubBaciaDetalhe(BaseModel):
    id: str
    tipo: str
    pareadaCom: str | None
    cidadeId: str
    cidadeNome: str
    sistemaId: str | None
    sistemaNome: str | None
    fatura: bool | None
    vazao: float | None
    vpl: float | None
    cascata: list[ParcelaDaCascata]
    receita: list[PontoDeReceita]
    explicacao: ExplicacaoDaSubBacia
    caminho: list[str]
    elementos: list[ElementoDaSubBacia]
    elementosPorAno: list[ElementoDoAno]


# ===========================================================================
#  §3.10 — elemento de obra
# ===========================================================================
class DependenciaDaObra(BaseModel):
    """Uma sub-bacia que depende desta obra, e quanto do CAPEX cabe a ela."""

    subbaciaId: str
    vazao: float | None
    fracaoRateio: float | None
    capexRateado: float | None
    fatura: bool | None


class ObraDetalhe(BaseModel):
    obraId: str
    componente: str
    rotulo: str
    situacao: str
    cidadeId: str
    cidadeNome: str
    sistemaId: str | None
    sistemaNome: str | None
    subbaciaId: str | None
    responsavel: str | None
    obrigatoria: bool | None
    quantidade: float | None
    unidade: str | None
    precoUnitario: float | None
    capex: float | None
    opexAno: float | None
    prazoMeses: int | None
    mesMaisCedo: int | None
    #: Em PONTOS PERCENTUAIS (9.45), não em fração.
    wacc: float | None
    waccOrigem: str | None
    ligacoesNovas: float | None
    ticketMedio: float | None
    precoPorLigacao: float | None
    capexConstruido: float | None
    capexQueFalta: float | None
    dataInicio: str | None
    dataPronta: str | None
    categoria: str | None
    narrativa: str | None
    elo: str | None
    dependencias: list[DependenciaDaObra]
