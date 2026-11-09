#!/usr/bin/env bash
# psql no Postgres do site, que mora na VPS sem porta publica: entra por SSH e
# docker exec no container. Os argumentos vao para o psql.
#
#   scripts/banco.sh -c 'select * from public.coletas order by momento desc limit 3'
#   scripts/banco.sh -1 -v ON_ERROR_STOP=1 -f banco/noticias.sql
#   scripts/banco.sh < consulta.sql
#
# Os dados de acesso ficam em ~/.config/spkids/vps (SPKIDS_SSH_CHAVE,
# SPKIDS_SSH_PORTA, SPKIDS_SSH_DESTINO, SPKIDS_PG_CONTAINER). Um -f <arquivo>
# local vira a entrada padrao do psql remoto; o resto passa como esta.
set -euo pipefail

VPS="${SPKIDS_VPS_ARQUIVO:-$HOME/.config/spkids/vps}"
[ -r "$VPS" ] || { echo "sem $VPS: veja o README (Arquivos fora do repositorio)" >&2; exit 1; }
# shellcheck source=/dev/null
source "$VPS"

args=()
entrada=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-f" ]; then
    entrada="$2"; shift 2
  else
    args+=("$1"); shift
  fi
done

# O Coolify poe um sufixo no nome do container; acha pelo prefixo. Os
# argumentos vao citados para o shell remoto nao os quebrar.
remoto="docker exec -i \$(docker ps -qf name=^$SPKIDS_PG_CONTAINER) psql -U postgres -d spkids -X"
for a in "${args[@]}"; do remoto+=" $(printf '%q' "$a")"; done

if [ -n "$entrada" ]; then
  ssh -o BatchMode=yes -o ConnectTimeout=20 -i "$SPKIDS_SSH_CHAVE" -p "$SPKIDS_SSH_PORTA" "$SPKIDS_SSH_DESTINO" "$remoto" < "$entrada"
else
  ssh -o BatchMode=yes -o ConnectTimeout=20 -i "$SPKIDS_SSH_CHAVE" -p "$SPKIDS_SSH_PORTA" "$SPKIDS_SSH_DESTINO" "$remoto"
fi
