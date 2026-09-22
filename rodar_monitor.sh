#!/usr/bin/env bash
# Chamado pelo cron. Carrega as credenciais, impede execucoes sobrepostas e
# registra tudo no log. Funciona em qualquer maquina: descobre a propria pasta.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARQUIVO_ENV="${SPKIDS_ENV:-$HOME/.config/spkids/env}"
TRAVA="$HOME/.cache/spkids-monitor.lock"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

if [ -r "$ARQUIVO_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ARQUIVO_ENV"
  set +a
else
  echo "aviso: $ARQUIVO_ENV nao encontrado; o alerta por e-mail nao vai sair" >&2
fi

echo "===== $(date '+%d/%m/%Y %H:%M:%S %Z') ====="

# -n: se a execucao anterior ainda roda, esta sai em vez de empilhar.
# -E 75: sai com 75 nesse caso, para nao se confundir com o 1 de erro do script.
flock -n -E 75 "$TRAVA" "$PY" "$REPO/monitor_spkids.py" "$@"
codigo=$?

case $codigo in
  0)  echo "-> sem novidade" ;;
  10) echo "-> NOVIDADE encontrada (alerta enviado)" ;;
  1)  echo "-> ERRO na verificacao ou no envio do aviso" ;;
  75) echo "-> outra execucao ainda em andamento; pulando esta" ;;
  *)  echo "-> saiu com codigo $codigo" ;;
esac
exit $codigo
