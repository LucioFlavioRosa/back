# Mudanças

O que mudou neste serviço e no front do cadastro, e por quê. Uma entrada por
leva de trabalho, da mais recente para a mais antiga.

O detalhe de cada mudança — o que foi medido, o que quebrou no caminho, o que se
considerou e descartou — está na mensagem do commit. Este arquivo é o índice.

---

## A rodada aceita um comentário

`PUT /runs/{id}/comentario` (e `DELETE`) gravam uma anotação sobre a rodada, e a
lista passa a devolvê-la junto de cada item. O front oferece o campo no modal de
detalhes, que é onde se está olhando o resultado.

É outra coisa que o `nome` da rodada: aquele é dado no disparo e descreve a
**intenção**; este é escrito depois de ver o que saiu e descreve a **conclusão**.

- `migracoes/010_run_comentario.sql` — tabela própria e **sem FK**, pela mesma
  razão de `run_favorita` (009): não há uma tabela única com todas as rodadas
  para apontar, e uma coluna em `run_request` deixaria de fora justamente a
  rodada publicada direto pelo pacote, que aparece na lista como as outras.
- **Compartilhado, não por usuário** — quem abre a rodada amanhã precisa ler a
  conclusão de quem a analisou. Por isso `autor` e `atualizado_em` são gravados:
  num texto que qualquer um reescreve, quem escreveu e quando é o mínimo para
  ele significar algo.
- **Escrever exige o mesmo alcance que ler** (`ve_rodada_de` + `acessa_unidade`,
  os mesmos métodos de `GET /runs`), e não o de `favorita`, que dispensa recorte
  porque só afeta a lista de quem pede. Fora do alcance responde `404`, e não
  `403`: dizer "existe, mas você não pode" já entrega que existe.
- **Texto vazio apaga a linha.** Assim "sem comentário" tem uma representação só,
  e a resposta traz `comentario: null` em vez de um bloco com texto vazio.
- É a **única parte mutável de uma rodada**, e a exceção é deliberada: o
  comentário não é registro da execução, é leitura humana sobre ela. Não entra em
  `params` nem em `otim_meta`.
- No front o salvamento é **pessimista**, ao contrário da estrela de favorita: a
  pessoa está digitando, e um update otimista revertido apagaria o que ela
  escreveu durante o voo — o mesmo defeito que fez criar/remover CTS voltarem a
  ser pessimistas no cadastro.

## Dá para desistir de uma rodada

`POST /runs/{id}/cancelar` respondia `501`: `CANCELADA` violava o CHECK de
`controle.run_status`, e responder `204` sem cancelar seria pior que responder
erro — a tela fecharia dizendo "cancelado" e o cluster seguiria processando.
Enquanto isso durou, o front não oferecia o botão.

`migracoes/008_lease_e_executores.sql` pôs o valor no CHECK, e endpoint e botão
voltaram juntos.

- `204` em `PENDENTE`/`RODANDO`, `409` em qualquer estado final. A condição está
  no `WHERE` do UPDATE, e não só no `if` que o precede: entre ler o status e
  escrever, o executor pode ter publicado.
- O executor confere o status nos pontos em que a rodada respira — antes do
  solver, depois dele e imediatamente antes de publicar — e larga o trabalho. Uma
  rodada `PENDENTE` cancelada nunca chega a executar; uma `RODANDO` nunca publica.
  O solver em voo é chamada nativa e não se interrompe no meio: a espera é
  limitada pelo `MAX_TIME_S` da própria rodada.

## Histórico da simulação mostra os metadados antes do resultado

Clicar numa rodada abre um modal com quem fez, quando, em que unidade e **as
variáveis com que ela foi pedida**; de lá se vai ao resultado.

`GET /runs` passou a devolver `pedido` — as chaves de `controle.run_request`,
como vieram. O campo `parametros`, que já existia, traz seis valores tipados; o
formulário de simulação aceita mais de vinte, e os demais não chegavam à tela.

Rodada em voo e rodada `INFEASIBLE` também abrem o modal: são as que mais geram a
pergunta "com que parâmetros isso foi pedido?". O botão de resultado fica
desabilitado nelas.

## A trilha de auditoria cobre a ficha inteira

`input.override` registrava só correções de dado do Databricks, e era montada pelo
front e enviada no corpo do `PUT`. Passou a ser calculada pelo servidor,
comparando o que está gravado com o que chega, e alcança `params`, obras, cidade,
metas, faixas e ETE.

- `migracoes/007_trilha_do_cadastro.sql`: coluna `origem`
  (`databricks` | `regional`) e `valor_novo` aceitando `NULL` para registrar
  remoção de um registro de coleção.
- `GET /unidades/{id}/alteracoes` expõe a trilha. Antes ela era gravada e não
  havia como lê-la pelo produto.
- No front, `HistoricoDaFicha` abre pela linha "última alteração" do cabeçalho.

## Cadastro para de inventar dado, e passa a registrar quem mexeu

Sete mudanças de regra, aplicadas juntas porque se sustentam:

| | |
|---|---|
| `capex` | é derivado de `quantidade × preco_unitario`; `migracoes/005_capex_derivado.sql` recusa divergência acima de R$ 0,01 |
| auditoria de ficha | `atualizado_em`/`atualizado_por` nas quatro tabelas de ficha (`migracoes/006`), no lugar do 409 por versão |
| obras | as listas literais de obra saíram do backend e do front; ficha sem componente é recusada com 422 |
| `/prontidao` | passou a dizer **qual** componente falta, e não só quantos campos |
| dedupe | alcança rodada concluída, desde que publicada e posterior à última alteração do cadastro |
| histórico de rodadas | `dev/limpar_rodadas_de_teste.py` remove o que nasceu de teste, em modo relatório por padrão |

Consequência assumida: duas pessoas na mesma ficha podem se sobrescrever sem
aviso no momento da gravação. O 409 comparava o hash da ficha inteira e cobrava o
preço de um conflito onde quase nunca havia um. Se for preciso barrar de novo, a
comparação tem de ser por campo.

## Nome da rodada chega ao histórico

`dev/worker.py` publicava `otim_meta.rotulo` a partir de `params["ROTULO"]`, chave
que não existe — o nome digitado era substituído por um texto genérico. Passou a
ler `run_request.rotulo` e `run_request.solicitado_por`.

Na mesma leva, o `README.md` ganhou o **contrato do executor**, que existia só
implícito no worker local.

---

## Migrações

| | o quê | obrigatória? |
|---|---|---|
| `001_override.sql` | trilha de auditoria do cadastro | sim |
| `002_progresso.sql` | `progresso` em `run_status` | sim |
| `003_usuario_acesso.sql` | perfis e escopo por unidade | sim |
| `004_run_request_rotulo.sql` | `rotulo` em `run_request` | sim |
| `005_capex_derivado.sql` | `capex` conferido contra `qtd × preço` | sim |
| `006_auditoria_cadastro.sql` | `atualizado_em`/`atualizado_por` | sim |
| `007_trilha_do_cadastro.sql` | `origem` e `valor_novo` nulável | sim |

Todas precisam ser aplicadas nos bancos existentes e refletidas no `ddl_input.sql`
do repositório do otimizador. O `/readyz` recusa o pod sem elas.
