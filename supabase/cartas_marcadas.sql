-- Marcacoes de "tenho" e "encomendei" do painel de precos. Rodar no SQL Editor do
-- Supabase. Uma lista so, sem login: quem abre o painel le e mexe nela. O
-- check limita o que entra a codigos de carta, para ninguem usar a tabela de
-- deposito.

create table if not exists public.cartas_marcadas (
  carta     text primary key check (carta ~ '^30C(-C)?/[0-9A-Za-z]{1,6}$'),
  criado_em timestamptz not null default now()
);

alter table public.cartas_marcadas enable row level security;

revoke all on public.cartas_marcadas from anon, authenticated;
grant select, insert, delete on public.cartas_marcadas to anon, authenticated;

drop policy if exists "todos leem" on public.cartas_marcadas;
drop policy if exists "todos marcam" on public.cartas_marcadas;
drop policy if exists "todos desmarcam" on public.cartas_marcadas;

create policy "todos leem" on public.cartas_marcadas
  for select to anon, authenticated using (true);
create policy "todos marcam" on public.cartas_marcadas
  for insert to anon, authenticated with check (true);
create policy "todos desmarcam" on public.cartas_marcadas
  for delete to anon, authenticated using (true);

-- Cartas encomendadas, ainda a chegar. Mesma regra da lista de "tenho": uma
-- lista so, sem login. Quando a carta chega e vira "tenho", o painel tira a
-- encomenda.
create table if not exists public.cartas_encomendadas (
  carta     text primary key check (carta ~ '^30C(-C)?/[0-9A-Za-z]{1,6}$'),
  criado_em timestamptz not null default now()
);

alter table public.cartas_encomendadas enable row level security;

revoke all on public.cartas_encomendadas from anon, authenticated;
grant select, insert, delete on public.cartas_encomendadas to anon, authenticated;

drop policy if exists "todos leem" on public.cartas_encomendadas;
drop policy if exists "todos marcam" on public.cartas_encomendadas;
drop policy if exists "todos desmarcam" on public.cartas_encomendadas;

create policy "todos leem" on public.cartas_encomendadas
  for select to anon, authenticated using (true);
create policy "todos marcam" on public.cartas_encomendadas
  for insert to anon, authenticated with check (true);
create policy "todos desmarcam" on public.cartas_encomendadas
  for delete to anon, authenticated using (true);
