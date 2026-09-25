-- Botao "Atualizar precos agora" do painel publicado. O painel chama
-- rpc('atualizar_precos'), e a funcao dispara o workflow painel.yml no GitHub.
-- O token do GitHub fica no Vault do Supabase (nunca no HTML nem no repo):
--
--   select vault.create_secret('<token>', 'github_disparo');
--
-- Token fine-grained, so deste repositorio, com Actions: Read and write.
-- Qualquer um com o painel pode apertar o botao; a espera minima entre dois
-- disparos segura o gasto (cada coleta sao ~5 pedidos a PokeWallet).

create extension if not exists pg_net with schema extensions;

create table if not exists public.disparos (
  momento timestamptz primary key default now()
);
alter table public.disparos enable row level security;
revoke all on public.disparos from anon, authenticated;

create or replace function public.atualizar_precos()
returns json
language plpgsql
security definer
set search_path = ''
as $$
declare
  espera constant interval := interval '5 minutes';
  ultimo timestamptz;
  token text;
begin
  select max(momento) into ultimo from public.disparos;
  if ultimo is not null and ultimo > now() - espera then
    return json_build_object('ok', false, 'liberado_em', ultimo + espera);
  end if;

  select decrypted_secret into token
    from vault.decrypted_secrets where name = 'github_disparo';
  if token is null then
    return json_build_object('ok', false, 'erro', 'token do GitHub nao configurado');
  end if;

  insert into public.disparos default values;
  perform net.http_post(
    url := 'https://api.github.com/repos/afonsolelis/monitor-spkids/actions/workflows/painel.yml/dispatches',
    body := '{"ref": "main"}'::jsonb,
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || token,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'monitor-spkids'
    )
  );
  return json_build_object('ok', true);
end;
$$;

revoke all on function public.atualizar_precos() from public;
grant execute on function public.atualizar_precos() to anon, authenticated;
