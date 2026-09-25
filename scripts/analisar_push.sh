#!/usr/bin/env bash
# Analisa um push na main: espera os workflows do commit, mostra o log do que
# falhou, confere se o Pages serve o painel desse commit e checa a saude do
# Supabase (coleta, cron, espaco, RLS, funcoes expostas).
#
#   scripts/analisar_push.sh            o ultimo commit da origin/main
#   scripts/analisar_push.sh <sha>      um commit especifico
#
# Saida 0 quando esta tudo certo, 1 quando algo precisa de atencao. Usado pelo
# agente devops (.agents/devops.md).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
git fetch -q origin main
sha="$(git rev-parse "${1:-origin/main}")"
PAGINA="https://afonsolelis.github.io/monitor-spkids/"
BANCO="${SPKIDS_DB_ARQUIVO:-$HOME/.config/spkids/supabase-db}"
problemas=0
falha() { echo "  FALHA: $*"; problemas=$((problemas + 1)); }
aviso() { echo "  aviso: $*"; }

echo "== commit $(git log -1 --format='%h %s (%an, %ar)' "$sha")"

# ------------------------------------------------------------- workflows
echo "== workflows"
# O GitHub leva alguns segundos para registrar os runs de um push novo; o CI
# roda em todo push, entao nenhum run depois de 2 minutos ja e problema.
inicio=$SECONDS
while :; do
  runs="$(gh run list --commit "$sha" --json databaseId,name,status,conclusion 2>/dev/null || echo '[]')"
  total="$(jq length <<<"$runs")"
  pendentes="$(jq '[.[] | select(.status != "completed")] | length' <<<"$runs")"
  if [ "$total" -gt 0 ] && [ "$pendentes" -eq 0 ]; then break; fi
  if [ "$total" -eq 0 ] && [ $((SECONDS - inicio)) -ge 120 ]; then
    falha "nenhum workflow rodou para este commit (o CI deveria rodar em todo push)"
    break
  fi
  if [ $((SECONDS - inicio)) -ge 900 ]; then
    falha "$pendentes workflow(s) ainda rodando depois de 15 minutos"
    break
  fi
  sleep 15
done
jq -r '.[] | "  \(.conclusion // .status)\t\(.name)\t(run \(.databaseId))"' <<<"$runs"
for id in $(jq -r '.[] | select(.status == "completed" and .conclusion != "success" and .conclusion != "skipped") | .databaseId' <<<"$runs"); do
  falha "run $id: $(jq -r --argjson id "$id" '.[] | select(.databaseId == $id) | .name' <<<"$runs")"
  gh run view "$id" --log-failed 2>/dev/null | tail -40 | sed 's/^/    /'
done

# ----------------------------------------------------------------- Pages
echo "== GitHub Pages"
if [ "$sha" = "$(git rev-parse origin/main)" ]; then
  esperado="$(git show "$sha:painel_precos.html" | sha256sum | cut -d' ' -f1)"
  # O CDN do Pages pode levar alguns minutos para trocar a versao.
  for tentativa in 1 2 3 4 5 6; do
    servido="$(curl -sSfL "$PAGINA?v=$(date +%s)" 2>/dev/null | sha256sum | cut -d' ' -f1)"
    [ "$servido" = "$esperado" ] && break
    [ "$tentativa" -lt 6 ] && sleep 30
  done
  if [ "$servido" = "$esperado" ]; then
    echo "  ok: o Pages serve o painel_precos.html deste commit"
  else
    falha "o Pages nao serve o painel_precos.html deste commit ($PAGINA)"
  fi
else
  echo "  pulado: $sha nao e o topo da main"
fi

# -------------------------------------------------------------- Supabase
echo "== Supabase"
if [ ! -r "$BANCO" ]; then
  aviso "sem $BANCO; checagem do banco pulada"
else
  sql() { psql "$(cat "$BANCO")" -X -At -v ON_ERROR_STOP=1 -c "$1" 2>&1; }
  idade="$(sql "select coalesce(round(extract(epoch from now() - max(momento)) / 60), -1) from public.coletas where origem <> 'importado'")"
  if [[ ! "$idade" =~ ^-?[0-9]+$ ]]; then
    falha "nao consegui consultar o banco: $idade"
  else
    if [ "$idade" -lt 0 ] || [ "$idade" -gt 90 ]; then falha "ultima coleta ha $idade min (esperado: menos de 90)"
    else echo "  ok: ultima coleta ha $idade min"; fi

    falhas_cron="$(sql "select count(*) from cron.job_run_details where status = 'failed' and start_time > now() - interval '24 hours'")"
    if [ "$falhas_cron" -gt 0 ]; then
      falha "$falhas_cron execucao(oes) do cron falharam nas ultimas 24 h"
      sql "select to_char(start_time, 'DD/MM HH24:MI') || '  ' || left(return_message, 160) from cron.job_run_details where status = 'failed' order by start_time desc limit 3" | sed 's/^/    /'
    else echo "  ok: nenhuma falha do cron em 24 h"; fi

    mb="$(sql "select pg_database_size(current_database()) / 1024 / 1024")"
    if [ "$mb" -gt 400 ]; then falha "banco com $mb MB (plano gratis: 500 MB)"
    else echo "  ok: banco com $mb MB de 500"; fi

    sem_rls="$(sql "select string_agg(tablename, ', ') from pg_tables where schemaname = 'public' and tablename in ('precos', 'cartas', 'coletas', 'cartas_marcadas') and not rowsecurity")"
    if [ -n "$sem_rls" ]; then falha "tabela(s) sem RLS: $sem_rls"; else echo "  ok: RLS ligado nas tabelas do painel"; fi

    # O projeto do Supabase abriga outros apps; so interessa o que e deste:
    # o schema coleta fechado e, no public, so as duas funcoes do painel.
    abertas="$(sql "select concat_ws(', ', case when has_schema_privilege('anon', 'coleta', 'usage') then 'schema coleta' end, (select string_agg(p.proname, ', ') from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'coleta' and has_function_privilege('anon', p.oid, 'execute')))")"
    if [ -n "$abertas" ]; then falha "o visitante (anon) alcanca a coleta interna: $abertas"
    else echo "  ok: schema coleta fechado para o visitante"; fi
  fi
fi

echo
if [ "$problemas" -eq 0 ]; then echo "tudo certo"; else echo "$problemas problema(s)"; fi
[ "$problemas" -eq 0 ]
