#!/usr/bin/env bash
# Analisa um push na main: espera os workflows do commit, mostra o log do que
# falhou, confere se o site na VPS serve as paginas desse commit (o Coolify
# reconstroi a cada push) e checa a saude do banco (coleta, cron, espaco,
# RLS, funcoes expostas).
#
#   scripts/analisar_push.sh            o ultimo commit da origin/main
#   scripts/analisar_push.sh <sha>      um commit especifico
#
# O banco nao tem porta publica: as consultas entram por SSH na VPS e
# docker exec no container do Postgres. Os dados de acesso ficam em
# ~/.config/spkids/vps (ver README, "Arquivos fora do repositorio").
#
# Saida 0 quando esta tudo certo, 1 quando algo precisa de atencao. Usado pelo
# agente devops (.agents/devops.md).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
git fetch -q origin main
sha="$(git rev-parse "${1:-origin/main}")"
PAGINA="https://spkids.afonsolelis.tech/"
API="https://spkids-api.afonsolelis.tech"
VPS="${SPKIDS_VPS_ARQUIVO:-$HOME/.config/spkids/vps}"
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

# ------------------------------------------------------------------ site
echo "== site (VPS)"
if [ "$sha" = "$(git rev-parse origin/main)" ]; then
  # Raiz = index.html (noticias); precos.html = painel_precos.html. O Coolify
  # leva uns minutos para reconstruir as paginas depois do push.
  for par in "index.html:" "painel_precos.html:precos.html"; do
    arquivo="${par%%:*}"
    esperado="$(git show "$sha:$arquivo" | sha256sum | cut -d' ' -f1)"
    for tentativa in $(seq 1 12); do
      servido="$(curl -sSfL "$PAGINA${par#*:}?v=$(date +%s)" 2>/dev/null | sha256sum | cut -d' ' -f1)"
      [ "$servido" = "$esperado" ] && break
      [ "$tentativa" -lt 12 ] && sleep 30
    done
    if [ "$servido" = "$esperado" ]; then
      echo "  ok: o site serve o $arquivo deste commit"
    else
      falha "o site nao serve o $arquivo deste commit ($PAGINA${par#*:}); veja o deploy no Coolify"
    fi
  done
else
  echo "  pulado: $sha nao e o topo da main"
fi

# A API responde? Uma chamada que o painel faz.
if curl -sSf -m 30 -o /dev/null "$API/coletas?select=momento&limit=1"; then
  echo "  ok: a API responde"
else
  falha "a API nao responde ($API)"
fi

# ----------------------------------------------------------------- banco
echo "== banco (VPS)"
if [ ! -r "$VPS" ]; then
  aviso "sem $VPS; checagem do banco pulada"
else
  # shellcheck source=/dev/null
  source "$VPS"
  sql() {
    ssh -o BatchMode=yes -o ConnectTimeout=20 -i "$SPKIDS_SSH_CHAVE" -p "$SPKIDS_SSH_PORTA" "$SPKIDS_SSH_DESTINO" \
      "docker exec -i $SPKIDS_PG_CONTAINER psql -U postgres -d spkids -X -At -v ON_ERROR_STOP=1" <<<"$1" 2>&1
  }
  idade="$(sql "select coalesce(round(extract(epoch from now() - max(momento)) / 60), -1) from public.coletas where origem <> 'importado'")"
  if [[ ! "$idade" =~ ^-?[0-9]+$ ]]; then
    falha "nao consegui consultar o banco: $idade"
  else
    if [ "$idade" -lt 0 ] || [ "$idade" -gt 90 ]; then falha "ultima coleta de precos ha $idade min (esperado: menos de 90)"
    else echo "  ok: ultima coleta de precos ha $idade min"; fi

    # Noticias: a cada meia hora; fonte com erro e aviso, nao falha (site de
    # fora sai do ar e volta sozinho).
    idade_n="$(sql "select coalesce(round(extract(epoch from now() - max(ok_em)) / 60), -1) from public.fontes")"
    if [ "$idade_n" -lt 0 ] || [ "$idade_n" -gt 70 ]; then falha "ultima coleta de noticias ha $idade_n min (esperado: menos de 70)"
    else echo "  ok: ultima coleta de noticias ha $idade_n min"; fi
    com_erro="$(sql "select string_agg(id || ' (' || left(erro, 60) || ')', ', ') from public.fontes where ativa and erro is not null")"
    [ -z "$com_erro" ] || aviso "fonte(s) com erro na ultima leitura: $com_erro"

    falhas_cron="$(sql "select count(*) from cron.job_run_details where status = 'failed' and start_time > now() - interval '24 hours'")"
    if [ "$falhas_cron" -gt 0 ]; then
      falha "$falhas_cron execucao(oes) do cron falharam nas ultimas 24 h"
      sql "select to_char(start_time, 'DD/MM HH24:MI') || '  ' || left(return_message, 160) from cron.job_run_details where status = 'failed' order by start_time desc limit 3" | sed 's/^/    /'
    else echo "  ok: nenhuma falha do cron em 24 h"; fi

    # O disco da VPS e dividido com os outros projetos; o compacta-precos
    # deveria segurar o banco em poucas centenas de MB.
    mb="$(sql "select pg_database_size(current_database()) / 1024 / 1024")"
    if [ "$mb" -gt 1500 ]; then falha "banco com $mb MB (esperado: bem menos de 1500)"
    else echo "  ok: banco com $mb MB"; fi

    sem_rls="$(sql "select string_agg(tablename, ', ') from pg_tables where schemaname = 'public' and tablename in ('precos', 'cartas', 'coletas', 'cartas_marcadas', 'cartas_encomendadas', 'fontes', 'noticias') and not rowsecurity")"
    if [ -n "$sem_rls" ]; then falha "tabela(s) sem RLS: $sem_rls"; else echo "  ok: RLS ligado nas tabelas do site"; fi

    # O schema coleta (PokeWallet, http) tem que ficar fechado para o visitante.
    abertas="$(sql "select concat_ws(', ', case when has_schema_privilege('anon', 'coleta', 'usage') then 'schema coleta' end, (select string_agg(p.proname, ', ') from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'coleta' and has_function_privilege('anon', p.oid, 'execute')))")"
    if [ -n "$abertas" ]; then falha "o visitante (anon) alcanca a coleta interna: $abertas"
    else echo "  ok: schema coleta fechado para o visitante"; fi
  fi
fi

echo
if [ "$problemas" -eq 0 ]; then echo "tudo certo"; else echo "$problemas problema(s)"; fi
[ "$problemas" -eq 0 ]
