#!/bin/bash
# Primeira subida do banco (volume vazio): os papeis do PostgREST e as
# extensoes. Depois disso, o resto vem dos scripts em banco/*.sql.
#   anon           o visitante do site (PostgREST sem token)
#   authenticated  so para os scripts, que foram escritos para o Supabase
#   authenticator  o usuario com que o PostgREST entra e vira anon
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v senha="$AUTHENTICATOR_PASSWORD" <<'SQL'
create role anon nologin;
create role authenticated nologin;
create role authenticator noinherit login password :'senha';
grant anon, authenticated to authenticator;
-- Os scripts qualificam a extensao http como extensions.http, como no Supabase.
create schema extensions;
create extension http with schema extensions;
create extension pg_cron;
SQL
