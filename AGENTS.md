# AGENTS.md

Regras para qualquer agente (Claude Code, Codex, Cursor...) ou pessoa que mexa
neste repositório. O `CLAUDE.md` importa este arquivo; os agentes
especializados ficam em [`.agents/`](.agents/).

## O projeto

Um site com duas abas, as duas só casca (leem da API ao abrir). Tudo roda na
VPS da Hostinger, pelo Coolify, como uma aplicação docker-compose construída
deste repositório a cada push na main (`hostinger/`): Postgres 17 com pg_cron
e http, PostgREST (`https://spkids-api.afonsolelis.tech`) e nginx com as
páginas (`https://spkids.afonsolelis.tech`). Nada roda em máquina local.

- `banco/noticias.sql`: coleta dos feeds RSS/Atom de `public.fontes` dentro
  do Postgres (pg_cron às :07 e :37). Pública só `painel_noticias()`.
- `index.html`: a aba Notícias, na raiz do site.
- `banco/coleta_precos.sql`: coleta de preço das cartas de 30 e 25 anos
  (pg_cron às :17, chave da PokéWallet em `coleta.segredos`). Públicas só
  `painel_precos()` e `atualizar_precos()`.
- `painel_precos.html`: a aba Cartas, publicada como `precos.html`.
  Fica até a coleção estar completa.
- Funções internas das duas coletas no schema `coleta`, fora da API.
- `hostinger/`: o compose, a imagem do Postgres e o nginx do site.
- `tests/visual/`: regressão visual pixel a pixel das duas páginas (Playwright).

Detalhes no [README](README.md).

## Git: trunk-based

- **Tudo direto na `main`.** Sem branch, sem pull request, sem etapa de
  revisão. Commit pequeno, coerente, e `git push origin main` logo em seguida.
- Mudança que depende de um passo externo antes de ir ao ar (rodar SQL no
  banco, criar segredo) segue a ordem: primeiro o passo externo, depois o
  commit na main. Nunca deixar trabalho parado numa branch esperando.
- Não reescrever histórico publicado (`push --force`, `rebase` do que já subiu).
  Errou? Commit novo corrigindo, ou `git revert`.
- Antes de commitar, `git pull --rebase origin main` se o remoto andou.

### Conventional Commits

Toda mensagem segue [Conventional Commits](https://www.conventionalcommits.org/pt-br/v1.0.0/):

```
<tipo>(<escopo>): <descrição>

<corpo opcional: o porquê, não o quê>
```

- **Tipos**: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
  `build`, `ci`, `chore`, `revert`. Mudança que quebra algo: `!` depois do
  tipo/escopo (`feat(coleta)!: ...`) e `BREAKING CHANGE:` no corpo.
- **Escopos** usados aqui: `noticias`, `resumo`, `site`, `painel`, `coleta`,
  `banco`, `hostinger`, `harness`, `ci`, `docs`. Minúsculos.
- **Descrição** em português, no presente, começando em minúscula, sem ponto
  final, primeira linha com até 72 caracteres.
  Ex.: `fix(coleta): tolera página vazia da PokéWallet`.
- Commit feito por agente termina com o trailer `Co-Authored-By:` do modelo.

### Hooks que garantem isso

O `instalar.sh` liga `git config core.hooksPath .githooks`:

| Hook | Barra |
|---|---|
| `pre-commit` | commit fora da `main`; segredo no diff (chave `pk_live_`, URL do Postgres com senha, chave secreta do Supabase) |
| `commit-msg` | mensagem fora do Conventional Commits ou com primeira linha > 72 caracteres |
| `pre-push` | push para qualquer ref remota que não seja a `main`; push que mexe nas páginas com a regressão visual falhando |

Não contornar com `--no-verify`. Se um hook recusou, corrija a causa.

Depois do push, o `ci.yml` repete as checagens no GitHub (mensagens do push,
gitleaks no histórico, shellcheck, actionlint) e o agente `devops`
analisa o resultado com `scripts/analisar_push.sh`.

## Segredos

O repositório é **público**. Nunca vão para o git nem para a saída de comando:

- A chave da PokéWallet: só na tabela `coleta.segredos` do banco (schema
  fechado para a API).
- As senhas do Postgres (`postgres` e `authenticator`): variáveis da
  aplicação no Coolify, com cópia em `~/repos/hostinger/.env` (`SPKIDS_PG_*`).
- O banco não tem porta pública. Consulta é por SSH na VPS e `docker exec`
  no container do Postgres; os dados de acesso ficam em `~/.config/spkids/vps`
  (ver README). Nunca imprima esses arquivos.
- A API é pública e sem chave: o visitante só lê e mexe nas marcações.

## Antes de commitar

| Mexeu em | Rode |
|---|---|
| `index.html`, `painel_precos.html` | `npm run test:visual` e peça a revisão do agente `ux` |
| `banco/*.sql` | o script inteiro no banco (`scripts/banco.sh -1 -v ON_ERROR_STOP=1 -f banco/x.sql`); ele precisa poder rodar de novo sem estrago. Depois confira `select * from public.coletas order by momento desc limit 3` |
| `hostinger/*` | `docker compose -f hostinger/docker-compose.yml config -q` (com `POSTGRES_PASSWORD` e `AUTHENTICATOR_PASSWORD` quaisquer no ambiente) e `shellcheck` nos `.sh`; o deploy de verdade é o push |
| `*.sh`, `.githooks/*` | `bash -n` e `shellcheck` |
| `.github/workflows/*` | `uvx --from actionlint-py==1.7.12.25 --with shellcheck-py==0.11.0.1 actionlint` (com o shellcheck junto, senão os blocos `run:` não são checados) |

Mudança intencional no visual: atualize as referências com
`npm run test:visual:atualizar` **no mesmo commit** da mudança, e só depois de
olhar as imagens novas.

## Agentes

| Agente | Quando usar |
|---|---|
| [`dev`](.agents/dev.md) | implementar, corrigir ou refatorar qualquer parte do projeto |
| [`ux`](.agents/ux.md) | revisar pixel a pixel toda mudança visível nas páginas, com o Playwright |
| [`devops`](.agents/devops.md) | analisar todo push (CI, deploy no Coolify, saúde do banco na VPS), investigar falhas e manter a esteira |

Fluxo: `dev` implementa → `ux` revisa (se mexeu numa página) → `dev` corrige o
que o `ux` apontar → commit e push na main → `devops` analisa o push e, se
algo quebrou, corrige a esteira ou devolve para o `dev`.

## Estilo

- Nomes e comentários em português. Nos `.sql` e `.sh`, comentários sem
  acento (como o código existente); textos para o usuário (HTML, README,
  mensagens) com acento.
- Comentário explica o porquê, não repete o código.
- Páginas: cada uma um arquivo HTML só, sem build. Cores só pelos tokens do `:root`
  (claro e escuro), layout sem rolagem horizontal a partir de 320 px.
