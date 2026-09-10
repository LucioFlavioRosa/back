# Otimizador CAPEX

O serviço que guarda o cadastro de saneamento de uma unidade, dispara simulações
de investimento e serve o resultado publicado. O motor de otimização não vive
aqui: ele roda como job e publica em `public.otim_*`; este serviço lê e escreve
as bordas.

## Language

### Organização

**Unidade**:
O recorte de operação que se cadastra e se simula inteiro. É a fronteira de
autorização: quem tem acesso, tem acesso a uma unidade.
_Avoid_: filial, operação, cliente

**Regional**:
O agrupamento de unidades acima delas na hierarquia. Vem do Databricks e não se
edita aqui.
_Avoid_: região, distrito

**Superintendência**:
O nível entre regional e cidade. Existe no modelo por ser elo da hierarquia, e
não porque alguma tela o preencha.

**Cidade**:
Onde o contrato de concessão vive: fim de concessão, régua de cobertura e metas
são dela.
_Avoid_: município, localidade

**Sistema**:
O conjunto de componentes que escoam para uma mesma ETE. É a unidade de
topologia: um componente está em um sistema só.
_Avoid_: rede, bacia

### Componentes

**Sub-bacia**:
A área de coleta que fatura. É o nó que o motor liga ou deixa de ligar, e a
resposta de "quanto do plano fatura" é contada nelas.
_Avoid_: bacia, setor, área

**CTS**:
Coletor de Tempo Seco — a irmã da sub-bacia: mesmos dados operacionais, quatro
obras próprias, e um nó do sistema como ela. Diferente de todo o resto, é a
Regional quem escolhe em que sistema cada uma entra.
_Avoid_: coletor, interceptor

**Macrorregião de CTS**:
O regime em que um coletor atende à região inteira, e por isso cada sistema da
unidade comporta UMA CTS. É decisão da unidade, não de cada sistema, e é dado de
cadastro: o motor não conta CTS por sistema. Marcada, a tela de montar o sistema
deixa de oferecer coletores e passa a oferecer macrorregiões — e é a macrorregião
que ganha ficha e as quatro obras.
_Avoid_: sistema de CTS (colide com **Sistema**, que é outra coisa), usa CTS

**Membro**:
Cada coletor que forma uma macrorregião — as linhas de `cts_operacional` com o
mesmo `sistema_cts` (e a mesma empresa). O membro não é colocado em sistema
nenhum, não tem obra cobrada e não aparece na tela quando a unidade está marcada:
quem faz as três coisas é a macrorregião a que ele pertence.
_Avoid_: CTS filha, CTS individual

**Linha da macrorregião**:
A linha de `cts_operacional` que REPRESENTA a macrorregião, marcada com
`e_macrorregiao`. Nasce no instante em que a macrorregião é colocada num sistema,
com as doze medidas do Databricks já somadas, os `params` vazios e as quatro
obras em branco. É ela que a Regional preenche, que a prontidão cobra e que o
motor lê como nó — os membros continuam soltos, e é assim que devem ficar.
_Avoid_: CTS agregada, CTS virtual

**Somar e preencher**:
As duas metades da ficha de uma macrorregião, e a fronteira entre elas.
SOMA-SE o que o Databricks mede sobre uma área — receitas, ligações, economias —,
porque a área da macrorregião é a união das áreas dos membros. PREENCHE-SE o
resto: preço por ligação, prazos, vazão e as quatro obras são informação de quem
cadastra sobre o conjunto, e a Regional as informa uma vez. Somar preço daria
R$ 3.049 por ligação; mediá-lo daria um número plausível que ninguém digitou.
_Avoid_: agregação (sozinho, não diz qual das duas)

**Microrregião de CTS**:
O outro regime: cada sistema comporta quantas CTS forem colocadas nele. É o
estado da unidade com a macrorregião desmarcada — um regime, e não a ausência de
um.
_Avoid_: sem macrorregião, desmarcado

**Usar CTS**:
Parâmetro da RODADA que decide se a CTS existe na simulação. Desligado, o coletor
não vira nó e quem atende a área sobreposta é a sub-bacia, pelas colunas
`*_com_cts`. Nada a ver com **Macrorregião de CTS**, que é cadastro e limita
quantas cabem por sistema.
_Avoid_: usa CTS, considerar CTS

**ETE**:
A Estação de Tratamento que fecha o caminho de um sistema. Um sistema tem uma
ETE só.

**Módulo de ETE**:
O que de fato se constrói numa ETE, e o que tem CAPEX e prazo. A linha da ETE em
si é ficha, não obra.
_Avoid_: unidade de tratamento, expansão

**Componente**:
Qualquer nó do fluxo — sub-bacia, CTS ou ETE. O tipo se descobre pela aba em que
ele tem ficha, e não por uma coluna.

**Obra**:
A intervenção física com quantidade, preço unitário, prazo e CAPEX. Uma
sub-bacia tem cinco; uma CTS, quatro; a ETE, uma por módulo.
_Avoid_: intervenção, projeto, item

### Fluxo

**Fluxo de escoamento**:
O grafo que diz para onde cada componente escoa até chegar à ETE do sistema.
_Avoid_: topologia (era o nome antigo; a URL `/topologia` ficou por ser contrato)

**Jusante**:
Para onde um componente escoa. Vazio significa "liga direto na ETE".
_Avoid_: destino, próximo, downstream

**Pareamento sub-bacia · CTS**:
A sobreposição de ÁREA entre uma CTS e uma sub-bacia. Nunca significou
pertencimento — ver ADR-0006.
_Avoid_: vínculo, associação

### Cadastro

**Cadastro**:
O conjunto de fichas de uma unidade — o que a simulação lê como entrada. Vive em
`input.*`.

**Ficha**:
As linhas de uma tabela de cadastro para um componente. É a granularidade da
gravação: grava-se uma ficha por vez, não o cadastro inteiro.
_Avoid_: registro, formulário

**Trilha**:
O histórico de override: quem mudou qual campo, de quê para quê, quando. Mora em
`input.override`.
_Avoid_: auditoria, log, histórico

**Pendência**:
Campo obrigatório de uma ficha que ainda está vazio. A contagem por grupo é o que
autoriza ou barra o disparo de uma rodada.
_Avoid_: erro, validação

**Prontidão**:
O estado de "esta unidade pode simular": pendências zeradas e cadastro coerente.

### Contrato e metas

**Régua de cobertura**:
A unidade em que a cobertura da cidade é medida — ligações, economias ou
população.
_Avoid_: base de cobertura, métrica

**Meta de cobertura**:
O percentual que o contrato exige numa cidade num ano. Meta fora da janela de
CAPEX não é cobrada da rodada, e a resposta para ela é "não avaliada", não "não
atingida".

**Fator de esgoto**:
A faixa que converte cobertura em paridade. Vive no cadastro, e não nas tabelas
de resultado.

**Rateio do CAPEX**:
Quanto do preço de uma obra COMPARTILHADA cabe a cada sub-bacia que escoa por
ela, na proporção da vazão. É atribuição de CUSTO — não é a ponderação do WACC,
que desde 10/09/2026 olha só as obras da própria sub-bacia (ADR-0009).
_Avoid_: partilha, divisão, share

**WACC da receita**:
A taxa que desconta a receita de uma sub-bacia no VPL: a média dos WACCs das
obras DELA, ponderada pelo CAPEX de cada uma. Transporte a jusante e ETE ficam
de fora.
_Avoid_: taxa de desconto, custo de capital do sistema

**Paridade**:
A razão entre esgoto e água numa cidade. O degrau entre a inicial e a final é o
efeito do plano sobre a base que já existia.

### Rodada

**Rodada**:
Uma execução do motor com um conjunto de parâmetros, do pedido ao resultado
publicado. Identificada por `run_id`.
_Avoid_: simulação (é o ato), execução, job

**Pedido**:
O corpo de parâmetros gravado em `controle.run_request` — a fonte de verdade do
que a rodada rodou. A mensagem da fila não os carrega, de propósito.

**Publicada**:
A rodada que tem linha em `otim_meta`. Só a publicada tem resultado a ler; a que
está em voo não.

**Janela de CAPEX**:
Os anos em que o plano pode investir. Fora dela não há teto a comparar nem meta a
cobrar.

### Resultado

**Explicabilidade**:
A resposta a "por que o plano não conecta 100%": para cada sub-bacia que não
fatura, o motivo, agregado por categoria.

**Categoria**:
O motivo pelo qual uma sub-bacia não fatura — "perdeu a disputa pelo orçamento",
"não se paga", "travada por obra da cadeia". Ver ADR-0004 sobre de qual obra ela
vem.
_Avoid_: motivo (é a frase em português; a categoria é o código)

**Elo**:
A obra que, não construída, tira OUTRAS sub-bacias do plano. O gargalo.
_Avoid_: dependência, bloqueio

**Vazão presa**:
A vazão das sub-bacias que não faturam. É a grandeza que diz o tamanho do que
está parado — a contagem sozinha não diz.

**Plano de execução**:
As obras que entraram na rodada, com o ano em que começam. Obra fora do plano não
tem ano.
_Avoid_: cronograma (é a visão por ano do mesmo plano)
