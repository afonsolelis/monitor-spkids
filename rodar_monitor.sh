#!/usr/bin/env bash
# Chamado pelo cron. Carrega as credenciais, impede execucoes sobrepostas e
# registra tudo no log. Funciona em qualquer maquina: descobre a propria pasta.
#
#   rodar_monitor.sh [opcoes]           roda os dois monitores (SP Kids e Copag)
#   rodar_monitor.sh spkids [opcoes]    so a SP Kids
#   rodar_monitor.sh copag [opcoes]     so a Copag B2B
#   rodar_monitor.sh precos [opcoes]    coleta o preco das cartas (cron de hora em hora)
#
# Sem nome de loja, as opcoes vao para os dois — use so as comuns
# (--sempre-notificar, --sem-estado, --termo, --webhook, --email, -v).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARQUIVO_ENV="${SPKIDS_ENV:-$HOME/.config/spkids/env}"

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

case "${1:-}" in
  spkids|copag|precos) lojas=("$1"); shift ;;
  *)            lojas=(spkids copag) ;;
esac

echo "===== $(date '+%d/%m/%Y %H:%M:%S %Z') ====="

final=0
for loja in "${lojas[@]}"; do
  echo "--- $loja"
  # -n: se a execucao anterior ainda roda, esta sai em vez de empilhar.
  # A trava da SP Kids mantem o nome antigo para nao conflitar na atualizacao.
  trava="$HOME/.cache/spkids-monitor.lock"
  [ "$loja" = spkids ] || trava="$HOME/.cache/$loja-monitor.lock"
  script="$REPO/monitor_$loja.py"
  [ "$loja" = precos ] && script="$REPO/precos_cartas.py"
  flock -n "$trava" "$PY" "$script" "$@"
  codigo=$?

  case $codigo in
    0)  [ "$loja" = precos ] && echo "-> precos gravados" || echo "-> sem novidade" ;;
    10) echo "-> NOVIDADE encontrada (alerta enviado)" ;;
    1)  echo "-> ERRO na verificacao" ;;
    *)  echo "-> saiu com codigo $codigo (1 = outra execucao em andamento)" ;;
  esac

  # Erro vence novidade, que vence "sem novidade".
  if [ "$codigo" -ne 0 ] && [ "$codigo" -ne 10 ]; then
    final=$codigo
  elif [ "$codigo" -eq 10 ] && [ "$final" -eq 0 ]; then
    final=10
  fi
done
exit $final
