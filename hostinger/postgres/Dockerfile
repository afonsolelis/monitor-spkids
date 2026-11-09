# Postgres 17 com as duas extensoes que as coletas usam e a imagem oficial
# nao traz: pg_cron (agendamento) e http (os feeds e a PokeWallet, por
# chamada sincrona). Pacotes do PGDG, o mesmo repositorio da imagem.
FROM postgres:17.11-trixie
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-cron postgresql-17-http \
 && rm -rf /var/lib/apt/lists/*
# Roda uma vez, quando o volume de dados esta vazio.
COPY inicio/ /docker-entrypoint-initdb.d/
