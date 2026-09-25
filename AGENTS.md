# AGENTS.md

Regras para qualquer agente (Claude Code, Codex, Cursor...) ou pessoa que mexa
neste repositório. O `CLAUDE.md` importa este arquivo; os agentes
especializados ficam em [`.agents/`](.agents/).

## O projeto

- `monitor_spkids.py` e `monitor_copag.py`: avisam por e-mail quando a coleção
  Pokémon 30 anos aparece/entra em estoque. A SP Kids roda no cron desta
  máquina (`rodar_monitor.sh spkids`, 5 min); a Copag roda na Render.
- `supabase/coleta_precos.sql`: coleta de preço das cartas dentro do Supabase
  (pg_cron às :17, chave da PokéWallet no Vault). Funções internas no schema
  `coleta`, fora da API; públicas só `painel_precos()` e `atualizar_precos()`.
- `painel_precos.html`: o painel publicado no GitHub Pages. É a casca: lê tudo
  do Supabase ao abrir.
- `tests/visual/`: regressão visual pixel a pixel do painel (Playwright).

Detalhes no [README](README.md).

## Git: trunk-based

- **Tudo direto na `main`.** Sem branch, sem pull request, sem etapa de
  revisão. Commit pequeno, coerente, e `git push origin main` logo em seguida.
- Mudança que depende de um passo externo antes de ir ao ar (rodar SQL no
  Supabase, criar segredo) segue a ordem: primeiro o passo externo, depois o
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
- **Escopos** usados aqui: `painel`, `coleta`, `supabase`, `monitor`,
  `copag`, `spkids`, `harness`, `ci`, `docs`. Minúsculos.
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
| `pre-push` | push para qualquer ref remota que não seja a `main`; push que mexe no painel com a regressão visual falhando |

Não contornar com `--no-verify`. Se um hook recusou, corrija a causa.

Depois do push, o `ci.yml` repete as checagens no GitHub (mensagens do push,
gitleaks no histórico, ruff, shellcheck, actionlint) e o agente `devops`
analisa o resultado com `scripts/analisar_push.sh`.

## Segredos

O repositório é **público**. Nunca vão para o git nem para a saída de comando:

- `~/.config/spkids/env`: SMTP e `POKEWALLET_KEY` (cópia local; a que vale
  está no Vault do Supabase, segredo `pokewallet`).
- `~/.config/spkids/supabase-db`: URL do Postgres com senha. Use sempre
  `psql "$(cat ~/.config/spkids/supabase-db)"`, sem imprimir o conteúdo.
- A chave `sb_publishable_...` que está no painel é pública de propósito.

## Antes de commitar

| Mexeu em | Rode |
|---|---|
| `painel_precos.html` | `npm run test:visual` e peça a revisão do agente `ux` |
| `supabase/*.sql` | o script inteiro no banco (`psql -1 -v ON_ERROR_STOP=1 -f`); ele precisa poder rodar de novo sem estrago. Depois confira `select * from public.coletas order by momento desc limit 3` |
| `monitor_*.py` | `.venv/bin/python -m py_compile monitor_*.py` e uma execução com `--sem-estado` |
| `*.sh`, `.githooks/*` | `bash -n` e `shellcheck` |
| `.github/workflows/*` | `actionlint` |
| `monitor_*.py` (lint) | `ruff check monitor_*.py` (config em `ruff.toml`) |

Mudança intencional no visual: atualize as referências com
`npm run test:visual:atualizar` **no mesmo commit** da mudança, e só depois de
olhar as imagens novas.

## Agentes

| Agente | Quando usar |
|---|---|
| [`dev`](.agents/dev.md) | implementar, corrigir ou refatorar qualquer parte do projeto |
| [`ux`](.agents/ux.md) | revisar pixel a pixel toda mudança visível no painel, com o Playwright |
| [`devops`](.agents/devops.md) | analisar todo push (CI, deploy, saúde do Supabase), investigar falhas e manter a esteira |

Fluxo: `dev` implementa → `ux` revisa (se mexeu no painel) → `dev` corrige o
que o `ux` apontar → commit e push na main → `devops` analisa o push e, se
algo quebrou, corrige a esteira ou devolve para o `dev`.

## Estilo

- Nomes e comentários em português. Nos `.py`, `.sql` e `.sh`, comentários sem
  acento (como o código existente); textos para o usuário (HTML, README,
  e-mail) com acento.
- Comentário explica o porquê, não repete o código.
- Python: só biblioteca padrão + o que está em `requirements.txt`.
- Painel: um arquivo HTML só, sem build. Cores só pelos tokens do `:root`
  (claro e escuro), layout sem rolagem horizontal a partir de 320 px.
