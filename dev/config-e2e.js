// Config de runtime do front no e2e. `apiUrl` vazio = `/api` na mesma origem,
// que e o modo recomendado em producao (sem CORS, sem dominio do backend no
// bundle) e o que o proxy do nginx-e2e atende.
window.__CADASTRO_CONFIG__ = {
  apiUrl: '',
  sso: { authority: '', clientId: '', escopos: [] },
};
