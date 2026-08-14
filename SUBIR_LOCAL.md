# Subir a solução inteira na sua máquina

Do zero até o produto rodando no navegador, com dado real. **~20 minutos**, a maior parte
esperando build de imagem.

O que você vai ter no fim: o cadastro (5 unidades, 141 cidades, 4.850 sub-bacias), a tela de
simulação disparando rodadas de verdade, e o resultado com os gráficos.

---

## O que precisa estar instalado

| | Por quê |
|---|---|
| **Docker Desktop** | sobe o Postgres, o front, a API e os emuladores do Azure |
| **Python 3.12+** | o executor da rodada roda **fora** do Docker (ver passo 5) |
| **Git** | os três repositórios |

---

## 1 · Clonar os três repositórios

Eles precisam ser **irmãos na mesma pasta** — o `docker-compose.e2e.yml` monta a imagem do
front a partir de `../otimizador-cadastro-web`.

```bash
git clone https://github.com/LucioFlavioRosa/back.git        otimizador-backend
git clone https://github.com/LucioFlavioRosa/front.git       otimizador-cadastro-web
git clone https://github.com/LucioFlavioRosa/otimzador_capex.git   # o motor
```

```
suapasta/
├── otimizador-backend/        <- é daqui que você roda tudo
├── otimizador-cadastro-web/
└── otimzador_capex/
```

## 2 · Subir os containers

Com o Docker Desktop **aberto** (o ícone precisa dizer "Engine running"):

```bash
cd otimizador-backend
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
```

São 7 containers: `db` (Postgres 16), `api` (FastAPI), `web` (nginx + front), mais os
emuladores de Service Bus, Blob (Azurite), Redis e o SQL Edge que o emulador de fila exige.

O primeiro build demora — ele compila o front. Confira:

```bash
docker compose ps                          # 7 no ar
curl http://localhost:8000/readyz          # "banco": true
```

> **`readyz` vai acusar `migracoesFaltando` agora.** É esperado: o banco ainda está vazio.

## 3 · Carregar o banco

O dump traz o **cadastro completo** e as concessões de acesso, sem histórico de rodadas —
você gera as suas.

```bash
docker compose exec -T db pg_restore -U otim -d otimizador --no-owner < dev/cadastro_base.dump
```

**O que vem no dump — a estrutura inteira, o dado só do cadastro:**

| Esquema | Objetos | FKs | Dado |
|---|---|---|---|
| `input` | 17 tabelas | 10 | **completo** — 4.850 sub-bacias, 997 ETEs, 141 cidades |
| `controle` | 7 tabelas | 1 | as 3 concessões de acesso; **sem histórico de rodadas** |
| `public` | 14 tabelas + 3 views | 13 | **vazio** — você gera os seus resultados |

**Quem enxerga o quê** já vem configurado em `controle.usuario_acesso`, e é o que faz o
recorte por unidade funcionar:

| login | papel | escopo |
|---|---|---|
| `dev@local` | admin | tudo — é o usuário assumido quando o SSO está desligado |
| `ana@aegea` | analista | regional `rA` |
| `bruno@aegea` | analista | só a unidade `uB2` |

A trilha de correções do cadastro (`input.override`) vem com 18 linhas de exemplo — override
tem valor antigo, valor novo, autor e data, e é assim que a tela mostra o que foi corrigido à
mão sobre o dado do Databricks.

As migrações do cadastro e a do resultado já estão aplicadas. Índices, constraints e
`COMMENT ON COLUMN` vêm junto.

> **Um erro no fim do restore é esperado:** `schema "public" already exists` — ele já existe em
> qualquer banco Postgres novo. O `pg_restore` conta como "1 error ignored" e segue; as 17
> tabelas de resultado são criadas normalmente.

Confira:

```bash
docker compose exec -T db psql -U otim -d otimizador -c \
  "select (select count(*) from input.subbacia_operacional) subbacias,
          (select count(*) from public.otim_meta) resultados;"        # 4850 | 0

curl http://localhost:8000/readyz          # migracoesFaltando: []
```

> **Por que um dump, e não os DDLs.** O cadastro nasce de uma planilha que não está no
> repositório (2 MB, no OneDrive do time). O dump é o mesmo dado, com as migrações já
> aplicadas, em 771 KB. Quem for **regenerar da planilha** usa `dev/recarregar_tudo.py` — leva
> ~20 min porque também roda as 5 unidades.
>
> **Por que o `public` vem vazio, mas vem.** Sem as 17 tabelas de resultado, `GET /runs`
> responderia 500 e a publicação não teria onde gravar. Sem os dados, cada um gera as próprias
> rodadas — e 366 dos 385 MB do banco são exatamente isso.

## 4 · Preparar o pacote do motor

O executor não importa o motor do repositório: ele carrega um **pacote plano**, e o repositório
usa imports de pacote (`from otimizador.dominio import ...`) que não resolvem nesse formato.

```bash
# 1. copie o pacote-base (planilha, leitor, dashboard) que o time compartilha
#    para uma pasta sua, por exemplo ~/pacote-otimizador

# 2. por cima dele, os arquivos do motor, do repositório:
cp otimzador_capex/otimizador/dominio/otimizador_capex_v62.py      ~/pacote-otimizador/
cp otimzador_capex/otimizador/dominio/otimizador_capex_cpsat63.py  ~/pacote-otimizador/
cp otimzador_capex/otimizador/dominio/contrato_resultado.py        ~/pacote-otimizador/
cp otimzador_capex/otimizador/infraestrutura/persistencia.py       ~/pacote-otimizador/
cp otimzador_capex/otimizador/infraestrutura/publicacao.py         ~/pacote-otimizador/
```

Depois **ajuste três imports** nos arquivos copiados — é a única diferença entre os dois
formatos:

| Arquivo | Trocar | Por |
|---|---|---|
| `otimizador_capex_cpsat63.py` | `from otimizador.dominio import otimizador_capex_v62 as M` | `import otimizador_capex_v62 as M` |
| `publicacao.py` | `from otimizador.dominio.contrato_resultado import (` | `from contrato_resultado import (` |
| `publicacao.py` | `from otimizador.infraestrutura import persistencia as _P` | `import persistencia as _P` |

Confirme que carrega:

```bash
cd ~/pacote-otimizador && python -c "import otimizador_capex_cpsat63, publicacao; print('ok')"
```

## 5 · Subir o executor

**Ele roda fora do Docker, e sem ele nada acontece:** `POST /runs` grava a rodada, publica na
fila e responde `PENDENTE` — e para por aí. Em produção quem consome a fila é o job do
Databricks; aqui é este processo.

```bash
cd otimizador-backend
pip install -r requirements.txt ortools openpyxl psycopg2-binary

OTIMIZADOR_PACOTE="$HOME/pacote-otimizador" python -u dev/worker.py --tempo 1000
```

Deixe o terminal aberto. Ele imprime a batida e cada rodada que pega:

```
worker SEUPC/12345/abcdef
  fila `otimizacoes`, 1 em paralelo, 1000s de solver, codigo <hash>
```

> **`-u` importa.** Sem ele o Python bufferiza e o terminal fica mudo.
>
> **`OTIMIZADOR_PACOTE` importa mais ainda.** Sem a variável, o executor procura o pacote no
> caminho padrão do Windows do autor e, se achar um antigo, roda com um motor velho **em
> silêncio**.

## 6 · Usar

**http://localhost:8080**

Um roteiro de 5 minutos:

1. escolha uma unidade — comece por **uA1** (5 cidades, 142 sub-bacias, ~15 s por rodada);
2. **Simular**, orçamento por ano, **Disparar**;
3. acompanhe no terminal do executor e no modal da tela;
4. o resultado abre com os gráficos — CAPEX por elemento traz, na mesma linha, valor, número de
   obras e quanto foi construído em cada unidade física.

`uA3` (67 cidades) e `uB2` (27 cidades, 186 CTS) levam de 15 a 30 minutos. Comece pela `uA1`.

---

## Quando algo não funciona

| Sintoma | Causa | O que fazer |
|---|---|---|
| `error during connect ... dockerDesktopLinuxEngine` | Docker Desktop fechado | abrir e esperar "Engine running" |
| rodada fica **PENDENTE** para sempre | executor não está rodando | passo 5 |
| rodada vai a **ERRO** com `ModuleNotFoundError: otimizador` | imports do pacote plano | passo 4 |
| `readyz` com `migracoesFaltando` | banco vazio ou desatualizado | passo 3 |
| a tela abre mas não lista unidades | o dump não entrou | repita o passo 3 e confira as 4.850 sub-bacias |
| reiniciou a máquina e nada sobe | containers parados | `docker compose -f docker-compose.yml -f docker-compose.e2e.yml start` — **`start`, não `up`** |

> **Sobre `start` e não `up`:** o serviço `db` **não tem volume nomeado**, então o dado mora na
> camada gravável do container. `start` religa o que existe; um `up` que decida recriar o `db`
> apaga o banco. Se isso acontecer, é o passo 3 de novo — que é justamente por que o dump existe.

---

## Como as peças se encaixam

```
navegador :8080
      |
   web (nginx)  -- serve o front e faz proxy de /api
      |
   api (FastAPI)  -- grava a rodada, publica na fila, lê o resultado
      |                              |
   db (Postgres)                servicebus (emulador)
      |                              |
      +---------- executor (dev/worker.py, FORA do Docker) 
                        |
                    o motor (pacote plano) -> publica em public.otim_*
```

O executor lê a **planilha** do pacote, não o Postgres — é a diferença mais fácil de esquecer
quando alguém muda o cadastro no banco e não vê efeito na rodada.
