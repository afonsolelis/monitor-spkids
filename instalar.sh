#!/usr/bin/env bash
# Prepara o monitor nesta maquina: ambiente Python, credenciais e cron.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME/.local/state/spkids"
ENV_DIR="$HOME/.config/spkids"

# ---------------------------------------------------------------- ambiente
# Distribuicoes Debian/Ubuntu costumam vir sem o pacote python3-venv, entao
# tentamos em ordem: uv, venv normal, venv sem pip, e por fim o Python do
# sistema. O rodar_monitor.sh usa .venv/bin/python se existir, senao python3.
echo "==> ambiente Python"
instalou=""

if command -v uv >/dev/null 2>&1; then
  if uv venv "$REPO/.venv" >/dev/null 2>&1 &&
     VIRTUAL_ENV="$REPO/.venv" uv pip install --quiet -r "$REPO/requirements.txt"; then
    instalou="uv"
  fi
fi

if [ -z "$instalou" ] && python3 -m venv "$REPO/.venv" >/dev/null 2>&1; then
  if "$REPO/.venv/bin/pip" install --quiet -r "$REPO/requirements.txt"; then
    instalou="venv"
  fi
fi

if [ -z "$instalou" ]; then
  rm -rf "$REPO/.venv"
  if python3 -c "import requests" 2>/dev/null; then
    echo "    sem venv, mas o Python do sistema ja tem requests — seguindo assim"
    instalou="sistema"
  else
    echo "ERRO: nao consegui preparar o ambiente." >&2
    echo "  instale um destes:  sudo apt install python3-venv   |   pip install --user requests" >&2
    exit 1
  fi
fi
echo "    via $instalou"

# ------------------------------------------------------------------ pastas
echo "==> pastas"
mkdir -p "$LOG_DIR" "$ENV_DIR" "$HOME/.cache"

if [ -f "$ENV_DIR/env" ]; then
  echo "    $ENV_DIR/env ja existe, mantido"
else
  cp "$REPO/env.exemplo" "$ENV_DIR/env"
  chmod 600 "$ENV_DIR/env"
  echo "    criado $ENV_DIR/env — EDITE e coloque a Senha de App"
fi

# ------------------------------------------------------------------- teste
echo "==> teste"
"$REPO/rodar_monitor.sh" || true

# -------------------------------------------------------------------- cron
LINHA="0 * * * * $REPO/rodar_monitor.sh >> $LOG_DIR/monitor.log 2>&1"
if crontab -l 2>/dev/null | grep -Fq "$REPO/rodar_monitor.sh"; then
  echo "==> cron ja instalado, nada a fazer"
else
  echo "==> instalando cron (de hora em hora)"
  # "|| true": sem crontab ainda, o -l sai com erro e o set -e abortaria aqui.
  ( crontab -l 2>/dev/null || true
    echo "# Monitor SP Kids: colecao de 30 anos de Pokemon."
    echo "$LINHA"
  ) | crontab -
fi

echo
echo "pronto. falta so editar $ENV_DIR/env com a Senha de App do Gmail."
echo "log: $LOG_DIR/monitor.log"
