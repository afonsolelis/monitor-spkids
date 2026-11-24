-- Marcacoes de "tenho" e "encomendei" do painel de precos. Rodar inteiro no
-- banco (psql -1 -v ON_ERROR_STOP=1 -f), depois de banco/coleta_precos.sql
-- (schema coleta e tabela coleta.segredos). Uma lista so, sem login: qualquer
-- um le; para mexer, o painel manda um PIN, conferido pelas funcoes do fim do
-- arquivo. O check limita o que entra a codigos de carta, para ninguem usar a
-- tabela de deposito.

create table if not exists public.cartas_marcadas (
  carta     text primary key check (carta ~ '^(30C|25C)(-C|-P)?/[0-9A-Za-z]{1,6}$'),
  criado_em timestamptz not null default now()
);

-- O check fica fora do create table para valer tambem na tabela que ja
-- existe: a edicao de promos (30C-P) e as de 25 anos entraram depois.
alter table public.cartas_marcadas drop constraint if exists cartas_marcadas_carta_check;
alter table public.cartas_marcadas add constraint cartas_marcadas_carta_check
  check (carta ~ '^(30C|25C)(-C|-P)?/[0-9A-Za-z]{1,6}$');

alter table public.cartas_marcadas enable row level security;

-- O visitante so le. Quem grava sao as funcoes com PIN, que rodam como dono
-- da tabela (o dono passa por cima do RLS).
revoke all on public.cartas_marcadas from anon, authenticated;
grant select on public.cartas_marcadas to anon, authenticated;

drop policy if exists "todos leem" on public.cartas_marcadas;
drop policy if exists "todos marcam" on public.cartas_marcadas;
drop policy if exists "todos desmarcam" on public.cartas_marcadas;

create policy "todos leem" on public.cartas_marcadas
  for select to anon, authenticated using (true);

-- Cartas encomendadas, ainda a chegar. Mesma regra da lista de "tenho": uma
-- lista so, sem login, gravada so com PIN. Quando a carta chega e vira
-- "tenho", o painel tira a encomenda.
create table if not exists public.cartas_encomendadas (
  carta     text primary key check (carta ~ '^(30C|25C)(-C|-P)?/[0-9A-Za-z]{1,6}$'),
  criado_em timestamptz not null default now()
);

alter table public.cartas_encomendadas drop constraint if exists cartas_encomendadas_carta_check;
alter table public.cartas_encomendadas add constraint cartas_encomendadas_carta_check
  check (carta ~ '^(30C|25C)(-C|-P)?/[0-9A-Za-z]{1,6}$');

alter table public.cartas_encomendadas enable row level security;

revoke all on public.cartas_encomendadas from anon, authenticated;
grant select on public.cartas_encomendadas to anon, authenticated;

drop policy if exists "todos leem" on public.cartas_encomendadas;
drop policy if exists "todos marcam" on public.cartas_encomendadas;
drop policy if exists "todos desmarcam" on public.cartas_encomendadas;

create policy "todos leem" on public.cartas_encomendadas
  for select to anon, authenticated using (true);

-- Observacao da encomenda (de quem comprei, de onde vem). Texto curto,
-- editado no detalhe da carta.
alter table public.cartas_encomendadas
  add column if not exists obs text check (char_length(obs) <= 500);

drop policy if exists "todos anotam" on public.cartas_encomendadas;

-- ---------------------------------------------------------------- PIN
-- O PIN fica so no banco, como hash bcrypt em coleta.segredos. Nem o hash
-- vai para o git: o repositorio e publico, e PIN de 6 digitos sai do hash em
-- segundos. Gravar (ou trocar) uma vez, no servidor:
--   insert into coleta.segredos values
--     ('pin_cartas', extensions.crypt('<pin>', extensions.gen_salt('bf', 10)))
--   on conflict (nome) do update set valor = excluded.valor;
create extension if not exists pgcrypto with schema extensions;

-- Cada PIN errado deixa uma linha. Dez na ultima hora travam a escrita por
-- uma hora: um milhao de PINs a dez por hora nao se esgota. O preco e que
-- quem errar de proposito trava tambem o dono; nada grave numa lista de cartas.
create table if not exists coleta.pin_falhas (momento timestamptz not null default now());

create or replace function coleta.conferir_pin(pin text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  hash text;
begin
  if (select count(*) from coleta.pin_falhas where momento > now() - interval '1 hour') >= 10 then
    return 'travado';
  end if;
  select valor into hash from coleta.segredos where nome = 'pin_cartas';
  if hash is null then
    raise exception 'Falta o PIN das cartas no banco (coleta.segredos, nome pin_cartas)';
  end if;
  if pin is not null and extensions.crypt(pin, hash) = hash then
    return 'ok';
  end if;
  insert into coleta.pin_falhas default values;
  delete from coleta.pin_falhas where momento < now() - interval '1 day';
  return 'errado';
end;
$$;

-- Recusa sem excecao: excecao desfaria o insert em pin_falhas e a trava nunca
-- contaria. O PostgREST devolve o status de response.status e o painel le o
-- campo message, como num erro comum.
create or replace function coleta.recusar(motivo text)
returns json
language plpgsql
set search_path = ''
as $$
begin
  perform pg_catalog.set_config('response.status', case motivo when 'travado' then '429' else '403' end, true);
  return json_build_object('ok', false, 'message', case motivo
    when 'travado' then 'Muitos PINs errados; a escrita volta em até uma hora'
    else 'PIN errado' end);
end;
$$;

revoke all on function coleta.conferir_pin(text), coleta.recusar(text) from public;

-- Marca ou desmarca varias cartas de uma vez numa das duas listas.
create or replace function public.mudar_cartas(pin text, lista text, cartas text[], tem boolean)
returns json
language plpgsql
security definer
set search_path = ''
as $$
declare
  conferido text := coleta.conferir_pin(pin);
  n int;
begin
  if conferido <> 'ok' then
    return coleta.recusar(conferido);
  end if;
  if lista = 'tenho' then
    if tem then
      insert into public.cartas_marcadas (carta) select unnest(cartas) on conflict do nothing;
    else
      delete from public.cartas_marcadas where carta = any(cartas);
    end if;
  elsif lista = 'encomendei' then
    if tem then
      insert into public.cartas_encomendadas (carta) select unnest(cartas) on conflict do nothing;
    else
      delete from public.cartas_encomendadas where carta = any(cartas);
    end if;
  else
    raise exception 'Lista desconhecida: %', lista;
  end if;
  get diagnostics n = row_count;
  return json_build_object('ok', true, 'linhas', n);
end;
$$;

create or replace function public.anotar_encomenda(pin text, carta text, obs text)
returns json
language plpgsql
security definer
set search_path = ''
as $$
declare
  conferido text := coleta.conferir_pin(pin);
begin
  if conferido <> 'ok' then
    return coleta.recusar(conferido);
  end if;
  update public.cartas_encomendadas e set obs = nullif(btrim(anotar_encomenda.obs), '')
   where e.carta = anotar_encomenda.carta;
  return json_build_object('ok', true);
end;
$$;

revoke all on function public.mudar_cartas(text, text, text[], boolean), public.anotar_encomenda(text, text, text) from public;
grant execute on function public.mudar_cartas(text, text, text[], boolean), public.anotar_encomenda(text, text, text) to anon, authenticated;
