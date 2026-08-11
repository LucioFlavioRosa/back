// Config de runtime do front na pilha COM AUTENTICAÇÃO (`docker-compose.sso.yml`).
//
// Igual ao `config-e2e.js`, mais o bloco `ssoDeMentira`: o front pede um token ao
// IdP falso e o manda em toda chamada. O login não é real — escolhe-se um usuário
// numa lista, na faixa do rodapé — mas o TOKEN é, e o backend o valida de verdade.
//
// `localhost:8099` e não `idp:8080`: quem faz este pedido é o NAVEGADOR, que está
// fora da rede do Docker. É também o endereço de onde sai o `iss` do token, e o
// que a API espera em `ENTRA_ISSUER`.
//
// Os três `usuarios` são os `client_id` que o mock mapeia (ver o `JSON_CONFIG` em
// `docker-compose.sso.yml`), e os escopos deles são diferentes de propósito:
// `dev` vê tudo, `ana` só a regional rA, `bruno` só a unidade uB2.
window.__CADASTRO_CONFIG__ = {
  apiUrl: '',
  sso: { authority: '', clientId: '', escopos: [] },
  ssoDeMentira: {
    tokenUrl: 'http://localhost:8099/otimizador/token',
    usuarios: ['dev', 'ana', 'bruno'],
    escopo: 'otimizador-api',
  },
};
