"""As formas dos payloads de RESULTADO — o contrato, escrito de um lado só.

## Por que estes modelos existem

Até 29/08/2026 toda rota deste serviço devolvia `dict[str, Any]`. Funciona, e
esconde uma classe inteira de defeito: o front declara `elementosPorAno` como
campo OBRIGATÓRIO em quatro interfaces, o backend não mandava em nenhuma, e as
quatro telas quebravam com `undefined.map`. Nada acusou — `test_contrato.py`
confere QUAIS rotas existem, não o que elas devolvem.

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
class SubBaciaPresa(BaseModel):
    subBaciaId: str
    cidadeId: str
    sistemaId: str | None
    vazaoPresa: float


class CategoriaDaExplicabilidade(BaseModel):
    categoria: str
    subbacias: int
    vazaoPresa: float
    itens: list[SubBaciaPresa]


class EloQueTrava(BaseModel):
    obraId: str
    componente: str
    cidadeId: str | None
    sistemaId: str | None
    subBaciaId: str | None
    bloqueia: int
    vazaoLiberada: float


class ExplicabilidadeGlobal(BaseModel):
    naoFaturando: int
    totalSubbacias: int
    categorias: list[CategoriaDaExplicabilidade]
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
    anoInicio: int | None
    prazoMeses: int | None


class ObrasPagina(BaseModel):
    #: Total do resultado FILTRADO, e não o da rodada.
    total: int
    itens: list[ObraLinha]


class ComponenteDoCronograma(BaseModel):
    componente: str
    obras: int
    capex: float


class AnoDeObras(BaseModel):
    ano: int
    obras: int
    capex: float
    obrasTerceiro: int
    porComponente: list[ComponenteDoCronograma]


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
