#!/usr/bin/env bash
# Prepara esta maquina para mexer no repositorio: hooks do git e Playwright
# dos testes visuais. Nada roda aqui de forma agendada: as coletas rodam no
# pg_cron do Supabase.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- git/harness
# Hooks do repositorio (trunk-based e Conventional Commits, ver AGENTS.md) e,
# se houver Node, o Playwright dos testes visuais do site.
echo "==> hooks do git"
git -C "$REPO" config core.hooksPath .githooks
echo "    core.hooksPath = .githooks"
if command -v npm >/dev/null 2>&1; then
  echo "==> testes visuais (Playwright)"
  if (cd "$REPO" && npm install --no-fund --no-audit --silent && npx playwright install chromium >/dev/null); then
    echo "    pronto: npm run test:visual"
  else
    echo "    falhou; o resto funciona sem isso"
  fi
fi

# ------------------------------------------------- monitores de loja (antigos)
# Os monitores da SP Kids e da Copag sairam do projeto. Tira do crontab as
# linhas de quem ainda tinha instalado.
if crontab -l 2>/dev/null | grep -Fq "$REPO/rodar_monitor.sh"; then
  echo "==> removendo os monitores de loja do crontab"
  crontab -l | grep -vF "$REPO/rodar_monitor.sh" | grep -viE '^# *(monitor sp kids|preco das cartas)' | crontab -
fi

echo
echo "pronto."
echo "site: https://afonsolelis.github.io/monitor-spkids/"
