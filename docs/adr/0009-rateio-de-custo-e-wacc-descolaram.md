# O rateio de CAPEX é de CUSTO; o WACC da receita é só das obras da sub-bacia

Até 10/09/2026 as duas contas usavam a mesma fração. `_wacc_receita`, no motor,
mediava o WACC da CADEIA INTEIRA até a ETE — a ligação e a rede da sub-bacia, o
transporte de todos os nós a jusante e a ETE do sistema — ponderando pelo CAPEX
rateado por vazão. O efeito era que o custo de capital de uma sub-bacia dependia
de QUEM ESTAVA A JUSANTE dela: duas sub-bacias idênticas, em sistemas
diferentes, descontavam a receita a taxas diferentes por causa de troncos e de
uma ETE que nenhuma das duas paga sozinha.

Hoje o WACC olha só as obras da própria sub-bacia (`r.no == o.no`), com o CAPEX
cheio de cada uma. O rateio por vazão continua existindo, intacto, para o que
sempre foi a outra pergunta: quanto do preço de uma obra compartilhada cabe a
cada quem escoa por ela — e é ele que faz a soma dos VPLs por sub-bacia
reproduzir o VPL do plano.

## Consequences

**A tela mostra as duas coisas juntas e elas não se seguem mais.** O detalhe da
obra lista o WACC dela e, embaixo, as sub-bacias que pagam uma fatia do CAPEX; a
inferência natural — "então o WACC dela pesa no desconto dessas sub-bacias" —
deixou de valer. Por isso o cartão diz "custo, não desconto", o verbete
`RATEIO_DO_CAPEX` explica a diferença, e `DependenciaDaObra` a repete no modelo.

**Há viés de atribuição, e ele é aceito.** Sem rateio no WACC, a sub-bacia que
ANCORA um tronco caro carrega 100% do peso dele, mesmo quando parte da demanda
vem de montante — o que favorece sistematicamente quem está acima do tronco. Num
sistema U→D→ETE com obras locais iguais a 10% e um tronco de 20% ancorado em D,
as duas ficavam em 16,25% e agora U fica em 10% e D em 17,69%. A decisão foi
aceitar: o WACC é o custo do financiamento das obras DAQUELA sub-bacia, e é assim
que ele passa a ser lido. Reintroduzir a ponderação é uma linha, se a leitura
mudar.

**Isto muda o plano escolhido, e não só o relatório.** `_wacc_receita` alimenta o
desconto da receita, que entra no VPL e no objetivo do CP-SAT. Rodadas
publicadas antes de 10/09/2026 têm números da régua antiga: comparar uma nova
com uma anterior é comparar duas réguas.
