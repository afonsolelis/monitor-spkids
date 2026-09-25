-- Marcacoes de "tenho" do painel de precos. Rodar uma vez no SQL Editor do
-- Supabase. O painel usa a chave publishable (publica); quem protege os dados
-- e o RLS abaixo: cada usuario logado so ve, cria e apaga as proprias linhas.

create table if not exists public.cartas_tenho (
  user_id   uuid not null default auth.uid() references auth.users on delete cascade,
  carta     text not null check (char_length(carta) between 1 and 40),
  criado_em timestamptz not null default now(),
  primary key (user_id, carta)
);

alter table public.cartas_tenho enable row level security;

revoke all on public.cartas_tenho from anon, authenticated;
grant select, insert, update, delete on public.cartas_tenho to authenticated;

drop policy if exists "dono le" on public.cartas_tenho;
drop policy if exists "dono grava" on public.cartas_tenho;
drop policy if exists "dono atualiza" on public.cartas_tenho;
drop policy if exists "dono apaga" on public.cartas_tenho;

create policy "dono le" on public.cartas_tenho
  for select to authenticated using (user_id = (select auth.uid()));
create policy "dono grava" on public.cartas_tenho
  for insert to authenticated with check (user_id = (select auth.uid()));
-- O upsert do painel pede UPDATE mesmo quando so ignora duplicadas.
create policy "dono atualiza" on public.cartas_tenho
  for update to authenticated using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
create policy "dono apaga" on public.cartas_tenho
  for delete to authenticated using (user_id = (select auth.uid()));
