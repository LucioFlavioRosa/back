-- `rotulo` em controle.run_request
--
-- O nome que o usuário dá à rodada só existia depois da PUBLICAÇÃO, em
-- `otim_meta.rotulo`. Entre o `POST /runs` e o fim da execução ele não estava em
-- lugar nenhum: o backend recebia `nome` no corpo e o descartava.
--
-- Isso não incomodou enquanto o histórico só mostrava rodada publicada. Passou a
-- incomodar quando ele passou a mostrar as EM VOO: a lista exibia linhas sem nome
-- durante toda a execução — justamente quando o usuário precisa distinguir uma
-- rodada da outra, porque é quando há várias ao mesmo tempo.
--
-- Não entra em `params` de propósito: `params` é o que vai para o MOTOR, e uma
-- chave a mais lá quebrava a execução (`ROTULO` inesperado). Coluna própria.
--
-- `reprocessa_de` fica para depois: só faz sentido junto com o `/reexecutar`
-- guardando linhagem, que ainda não é caso de uso.

ALTER TABLE controle.run_request
  ADD COLUMN IF NOT EXISTS rotulo text;

COMMENT ON COLUMN controle.run_request.rotulo IS
  'Nome dado pelo usuário no disparo. Existe desde o POST, ao contrário de otim_meta.rotulo, que só existe após publicar.';
