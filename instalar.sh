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
# Os monitores de loja a cada 15 minutos. A linha e reconhecida pelo comando,
# entao rodar de novo nao duplica e uma instalacao antiga e atualizada. A coleta
# de precos das cartas nao entra aqui: roda no GitHub Actions (painel.yml).
instalar_cron() {  # $1 = linha desejada, $2 = trecho que identifica a linha, $3 = comentario
  local atual
  atual="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "$atual" | grep -Fxq "$1"; then
    echo "    ja instalado: $3"
  elif printf '%s\n' "$atual" | grep -v '^#' | grep -Fq "$2"; then
    echo "    atualizando: $3"
    # ENVIRON em vez de -v: o awk interpreta barras invertidas passadas por -v.
    printf '%s\n' "$atual" | TRECHO="$2" LINHA="$1" awk \
      '$0 !~ /^#/ && index($0, ENVIRON["TRECHO"]) { print ENVIRON["LINHA"]; next } { print }' | crontab -
  else
    echo "    instalando: $3"
    printf '%s\n%s\n%s\n' "$atual" "# $3" "$1" | sed '/./,$!d' | crontab -
  fi
}

echo "==> cron"
MONITOR="$REPO/rodar_monitor.sh"
instalar_cron "*/15 * * * * $MONITOR >> $LOG_DIR/monitor.log 2>&1" \
  "$MONITOR >>" "Monitor SP Kids + Copag: colecao de 30 anos de Pokemon (15 min)."

# ------------------------------------------------------ servidor do painel
# Serve o painel em http://127.0.0.1:8787 com o botao "Atualizar precos agora".
# Servico de usuario do systemd: sobe junto com a sessao, sem sudo.
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  echo "==> servidor do painel"
  PY_SERV="$REPO/.venv/bin/python"
  [ -x "$PY_SERV" ] || PY_SERV="$(command -v python3)"
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/painel-precos.service" <<UNIT
[Unit]
Description=Painel de precos das cartas Pokemon 30 anos (http://127.0.0.1:8787)

[Service]
ExecStart=$PY_SERV $REPO/servidor_painel.py
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now painel-precos.service >/dev/null 2>&1 &&
    systemctl --user restart painel-precos.service &&
    echo "    rodando em http://127.0.0.1:8787"
else
  echo "==> sem systemd de usuario; suba o painel na mao: python3 $REPO/servidor_painel.py"
fi

echo
echo "pronto. falta so editar $ENV_DIR/env com a Senha de App do Gmail."
echo "log: $LOG_DIR/monitor.log  |  precos: $LOG_DIR/precos.log"
echo "painel de precos: http://127.0.0.1:8787"
