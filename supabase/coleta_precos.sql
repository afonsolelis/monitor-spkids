-- Coleta de preco das cartas dentro do Supabase. Substitui o precos_cartas.py
-- no GitHub Actions: a funcao coleta.coletar_precos() le a PokeWallet, grava
-- em public.precos e o pg_cron roda de hora em hora. O painel publicado le
-- tudo por rpc('painel_precos') e o botao chama rpc('atualizar_precos').
--
-- Rodar inteiro no SQL Editor do Supabase. Pode rodar de novo sem estragar
-- nada. Antes, uma vez so, guardar a chave da PokeWallet no Vault (a mesma do
-- POKEWALLET_KEY em ~/.config/spkids/env):
--
--   select vault.create_secret('<chave pk_live...>', 'pokewallet');
--
-- A chave nunca sai do banco: quem chama a PokeWallet e a propria funcao.

do $$
begin
  if not exists (select 1 from vault.secrets where name = 'pokewallet') then
    raise exception 'Falta a chave da PokeWallet no Vault. Rode antes: select vault.create_secret(''<chave>'', ''pokewallet'');';
  end if;
end $$;

-- O http e sincrono (a funcao le a resposta na hora); o pg_net nao.
create extension if not exists http with schema extensions;
create extension if not exists pg_cron with schema pg_catalog;

-- ------------------------------------------------------------- limpeza
-- Versao anterior: o botao disparava o workflow do GitHub com um token no
-- Vault. A assinatura e a mesma, entao a funcao nova (mais abaixo) a
-- substitui; a tabela de disparos e o token nao servem mais.
drop table if exists public.disparos;
do $$
begin
  delete from vault.secrets where name = 'github_disparo';
exception when others then
  raise notice 'apague o segredo github_disparo pelo painel do Vault (%)', sqlerrm;
end $$;
-- Tabela da versao com login das marcacoes; vazia desde a troca.
drop table if exists public.cartas_tenho;

-- -------------------------------------------------------------- tabelas
-- Catalogo: uma linha por carta, atualizada a cada coleta. O nome em
-- portugues veio da LigaPokemon (a PokeWallet so tem ingles) e e mantido.
create table if not exists public.cartas (
  colecao  text not null,
  numero   text not null,
  nome_en  text not null default '',
  nome_pt  text not null default '',
  imagem   text not null default '',
  url      text not null default '',
  visto_em timestamptz,
  primary key (colecao, numero)
);

-- Historico: uma linha por carta por coleta, em reais.
create table if not exists public.precos (
  coletado_em timestamptz not null,
  colecao     text not null,
  numero      text not null,
  preco_min   numeric(12, 2),
  preco_medio numeric(12, 2),
  preco_max   numeric(12, 2),
  primary key (colecao, numero, coletado_em)
);
create index if not exists precos_coletado_em on public.precos (coletado_em);

-- Registro de cada coleta. origem: cron, botao ou importado (o CSV antigo).
create table if not exists public.coletas (
  momento   timestamptz primary key,
  origem    text not null,
  cartas    integer not null,
  com_preco integer not null,
  dolar     numeric(10, 4)
);

-- Visitante so le. Quem grava e a coleta, que roda como dono das tabelas.
alter table public.cartas enable row level security;
alter table public.precos enable row level security;
alter table public.coletas enable row level security;
revoke all on public.cartas, public.precos, public.coletas from anon, authenticated;
grant select on public.cartas, public.precos, public.coletas to anon, authenticated;
drop policy if exists "todos leem" on public.cartas;
drop policy if exists "todos leem" on public.precos;
drop policy if exists "todos leem" on public.coletas;
create policy "todos leem" on public.cartas for select to anon, authenticated using (true);
create policy "todos leem" on public.precos for select to anon, authenticated using (true);
create policy "todos leem" on public.coletas for select to anon, authenticated using (true);

-- ---------------------------------------------------- funcoes internas
-- Num schema proprio, fora da API: ninguem de fora chama a PokeWallet nem
-- usa o banco para buscar URL arbitraria.
create schema if not exists coleta;
revoke all on schema coleta from public, anon, authenticated;

-- Preco positivo ou nulo: 0.00 na API quer dizer sem oferta.
create or replace function coleta.preco(valor jsonb)
returns numeric
language plpgsql
immutable
set search_path = ''
as $$
declare
  numero numeric;
begin
  if valor is null or jsonb_typeof(valor) not in ('number', 'string') then
    return null;
  end if;
  numero := (valor #>> '{}')::numeric;
  return case when numero > 0 then numero end;
exception when others then
  return null;
end;
$$;

-- '58/102' -> '058': o historico guarda o numero com tres digitos. A Classic
-- Collection reimprime cartas com o numero original e tres pares se repetem;
-- a Liga separava com letra, e o numero completo resolve.
create or replace function coleta.numero(sigla text, completo text)
returns text
language sql
immutable
set search_path = ''
as $$
  select case
    when sigla = '30C-C' and completo = '11/101'  then '011b'  -- Genesect-EX (Metagross e 11/113)
    when sigla = '30C-C' and completo = '106/160' then '106b'  -- M Gardevoir-EX (Palkia LV.X e 106/106)
    when sigla = '30C-C' and completo = '106/105' then '106c'  -- Shining Celebi
    when split_part(completo, '/', 1) ~ '^[0-9]{1,2}$' then lpad(split_part(completo, '/', 1), 3, '0')
    else split_part(completo, '/', 1)
  end
$$;

-- GET que tolera os 500/502 esporadicos da PokeWallet. A chave vai so no
-- cabecalho, nunca na URL, para nao aparecer em mensagem de erro.
create or replace function coleta.buscar_json(endereco text, chave text default null, tentativas integer default 4)
returns jsonb
language plpgsql
volatile
set search_path = ''
as $$
declare
  resposta extensions.http_response;
  cabecalhos extensions.http_header[] := array[extensions.http_header('Accept', 'application/json')];
  erro text;
begin
  if chave is not null then
    cabecalhos := cabecalhos || extensions.http_header('X-API-Key', chave);
  end if;
  perform extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS', '60000');
  for tentativa in 1..tentativas loop
    begin
      resposta := extensions.http(('GET', endereco, cabecalhos, null, null)::extensions.http_request);
      if resposta.status = 200 then
        return resposta.content::jsonb;
      end if;
      erro := 'HTTP ' || resposta.status;
    exception when others then
      erro := sqlerrm;
    end;
    if tentativa < tentativas then
      perform pg_catalog.pg_sleep(3 * tentativa);
    end if;
  end loop;
  raise exception 'falha ao buscar %: %', endereco, erro;
end;
$$;

-- Quanto vale um dolar em reais agora. A AwesomeAPI as vezes responde 429 a
-- IP de nuvem; a reserva atualiza uma vez por dia, o que basta.
create or replace function coleta.cotacao_dolar()
returns numeric
language plpgsql
volatile
set search_path = ''
as $$
declare
  valor numeric;
begin
  begin
    valor := coleta.preco(coleta.buscar_json('https://economia.awesomeapi.com.br/last/USD-BRL', null, 1) -> 'USDBRL' -> 'bid');
  exception when others then
    raise warning 'AwesomeAPI falhou (%); usando a cotacao reserva', sqlerrm;
  end;
  if valor is null then
    valor := coleta.preco(coleta.buscar_json('https://open.er-api.com/v6/latest/USD') -> 'rates' -> 'BRL');
  end if;
  if valor is null then
    raise exception 'cotacao do dolar veio vazia';
  end if;
  return valor;
end;
$$;

-- As cartas das duas edicoes no resultado da busca, com preco em reais.
-- Separada da coleta para poder ser testada com uma resposta gravada.
create or replace function coleta.cartas_da_busca(resultados jsonb, dolar numeric)
returns table (
  colecao text, numero text, nome_en text, imagem text, url text,
  preco_min numeric, preco_medio numeric, preco_max numeric
)
language sql
stable
set search_path = ''
as $$
  with edicoes (sigla, set_id) as (
    values ('30C', '24722'), ('30C-C', '24837')
  ),
  achadas as (
    select e.sigla,
           coleta.numero(e.sigla, r -> 'card_info' ->> 'card_number') as numero,
           r -> 'card_info' ->> 'name' as nome,
           coalesce(r -> 'tcgplayer' ->> 'url', '') as url,
           case when jsonb_typeof(r -> 'tcgplayer' -> 'prices') = 'array'
                then r -> 'tcgplayer' -> 'prices' else '[]'::jsonb end as precos,
           ordem
      from jsonb_array_elements(resultados) with ordinality as t (r, ordem)
      join edicoes e on e.set_id = r -> 'card_info' ->> 'set_id'
     where r -> 'card_info' ->> 'product_type' = 'card'
       -- sem numero e o "Code Card" do booster, nao e carta
       and coalesce(r -> 'card_info' ->> 'card_number', '') <> ''
  ),
  -- Carta repetida na busca: vale a ultima, como no script antigo.
  unicas as (
    select distinct on (sigla, numero) *
      from achadas
     order by sigla, numero, ordem desc
  ),
  com_variante as (
    -- Holografica custa varias vezes a normal: escolhe uma variante so.
    select u.*,
           coalesce((
             select p
               from jsonb_array_elements(u.precos) with ordinality as v (p, i)
              order by case when jsonb_typeof(p) <> 'object' then 5
                            when p ->> 'sub_type_name' = 'Holofoil' then 1
                            when p ->> 'sub_type_name' = 'Reverse Holofoil' then 2
                            when p ->> 'sub_type_name' = 'Normal' then 3
                            else 4 end, i
              limit 1
           ), '{}'::jsonb) as escolhida,
           regexp_replace(rtrim(u.url, '/'), '^.*/', '') as produto
      from unicas u
  )
  select sigla,
         numero,
         -- Algumas vem com o numero no nome ("Mew ex - 066/128").
         regexp_replace(coalesce(nome, ''), '\s+-\s+\S+/\S+$', ''),
         case when produto ~ '^[0-9]+$'
              then 'https://tcgplayer-cdn.tcgplayer.com/product/' || produto || '_400w.jpg'
              else '' end,
         url,
         round(coleta.preco(escolhida -> 'low_price') * dolar, 2),
         round(coalesce(coleta.preco(escolhida -> 'market_price'), coleta.preco(escolhida -> 'mid_price')) * dolar, 2),
         round(coleta.preco(escolhida -> 'high_price') * dolar, 2)
    from com_variante
$$;

-- Grava as cartas de uma busca ja feita. Se alguma edicao vier vazia, a busca
-- mudou de formato ou de nome: gravar so metade deixaria buracos no historico
-- sem ninguem notar, entao da erro e nao grava nada.
create or replace function coleta.gravar_coleta(resultados jsonb, dolar numeric, origem text, momento timestamptz)
returns json
language plpgsql
volatile
set search_path = ''
as $$
declare
  vazia text;
  total integer;
  com_preco integer;
begin
  drop table if exists pg_temp.novas;
  create temp table novas on commit drop as
    select * from coleta.cartas_da_busca(resultados, dolar);

  select string_agg(sigla, ', ') into vazia
    from (values ('30C'), ('30C-C')) as e (sigla)
   where not exists (select 1 from pg_temp.novas n where n.colecao = e.sigla);
  if vazia is not null then
    raise exception 'nenhuma carta de % na busca; nada gravado', vazia;
  end if;

  select count(*), count(preco_medio) into total, com_preco from pg_temp.novas;

  insert into public.cartas as c (colecao, numero, nome_en, imagem, url, visto_em)
  select n.colecao, n.numero, n.nome_en, n.imagem, n.url, momento from pg_temp.novas n
  on conflict (colecao, numero) do update
    set nome_en = excluded.nome_en, imagem = excluded.imagem,
        url = excluded.url, visto_em = excluded.visto_em;

  -- Sem preco nenhum nao ha o que guardar; uma coleta vazia so sujaria o
  -- historico.
  if com_preco > 0 then
    insert into public.precos (coletado_em, colecao, numero, preco_min, preco_medio, preco_max)
    select momento, n.colecao, n.numero, n.preco_min, n.preco_medio, n.preco_max from pg_temp.novas n;
    insert into public.coletas (momento, origem, cartas, com_preco, dolar)
    values (momento, origem, total, com_preco, dolar);
  end if;

  drop table pg_temp.novas;
  return json_build_object('ok', true, 'cartas', total, 'com_preco', com_preco, 'coletado_em', momento);
end;
$$;

-- A coleta: cotacao, todas as paginas da busca (100 cartas com preco por
-- pedido, ~5 pedidos; o plano gratis da PokeWallet da 100 por hora) e grava.
-- A listagem da edicao (/sets/:id) vem sem preco no plano gratis; a busca vem
-- com, e pega tambem a edicao japonesa e produtos lacrados, que o filtro por
-- set_id e product_type descarta.
create or replace function coleta.coletar_precos(origem text default 'cron')
returns json
language plpgsql
volatile
set search_path = ''
as $$
declare
  chave text;
  dolar numeric;
  dados jsonb;
  resultados jsonb := '[]';
  pagina integer := 1;
  paginas integer := 1;
begin
  -- Botao e cron nunca coletam ao mesmo tempo.
  if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtext('coleta.coletar_precos')) then
    raise exception 'ja tem uma coleta em andamento';
  end if;

  select decrypted_secret into chave from vault.decrypted_secrets where name = 'pokewallet';
  if chave is null then
    raise exception 'chave da PokeWallet nao esta no Vault (segredo pokewallet)';
  end if;

  dolar := coleta.cotacao_dolar();
  while pagina <= paginas and pagina <= 20 loop
    dados := coleta.buscar_json(
      'https://api.pokewallet.io/search?q=30th%20Celebration&limit=100&page=' || pagina, chave);
    if jsonb_typeof(dados -> 'results') = 'array' then
      resultados := resultados || (dados -> 'results');
    end if;
    paginas := coalesce(nullif(coleta.preco(dados -> 'pagination' -> 'total_pages'), 0), 1)::integer;
    pagina := pagina + 1;
  end loop;

  return coleta.gravar_coleta(resultados, dolar, origem, date_trunc('second', now()));
end;
$$;

-- Hora a hora sao ~190 linhas por coleta, ~150 MB por ano, e o plano gratis
-- tem 500 MB. Depois de 30 dias cada carta fica com um ponto por dia (a
-- media), que e o que o painel mostra de qualquer forma para o que passou de
-- 14 dias.
create or replace function coleta.compactar_precos()
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  corte timestamptz := (date_trunc('day', now() at time zone 'America/Sao_Paulo') - interval '30 days')
                         at time zone 'America/Sao_Paulo';
  removidas integer;
begin
  drop table if exists pg_temp.dias;
  create temp table dias on commit drop as
    select colecao, numero,
           (coletado_em at time zone 'America/Sao_Paulo')::date as dia,
           to_timestamp(round(avg(extract(epoch from coletado_em)))) as coletado_em,
           round(avg(preco_min), 2) as preco_min,
           round(avg(preco_medio), 2) as preco_medio,
           round(avg(preco_max), 2) as preco_max
      from public.precos
     where coletado_em < corte
     group by 1, 2, 3
    having count(*) > 1;

  delete from public.precos p
   using pg_temp.dias d
   where p.coletado_em < corte
     and p.colecao = d.colecao and p.numero = d.numero
     and (p.coletado_em at time zone 'America/Sao_Paulo')::date = d.dia;
  get diagnostics removidas = row_count;

  insert into public.precos (coletado_em, colecao, numero, preco_min, preco_medio, preco_max)
  select coletado_em, colecao, numero, preco_min, preco_medio, preco_max from pg_temp.dias;
  removidas := removidas - (select count(*) from pg_temp.dias);
  drop table pg_temp.dias;
  return removidas;
end;
$$;

-- Historico da epoca do GitHub Actions: dados/precos-cartas.csv (Liga ate
-- 23/09/2026, PokeWallet depois). Linhas que ja existem ficam como estao.
create or replace function coleta.importar_csv(endereco text)
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  texto text;
  inseridas integer;
begin
  select content into texto from extensions.http_get(endereco) where status = 200;
  if texto is null then
    raise exception 'nao consegui baixar %', endereco;
  end if;

  drop table if exists pg_temp.csv;
  create temp table csv on commit drop as
    select split_part(l, ',', 1)::timestamptz as coletado_em,
           split_part(l, ',', 2) as colecao,
           split_part(l, ',', 3) as numero,
           split_part(l, ',', 4) as nome_en,
           split_part(l, ',', 5) as nome_pt,
           nullif(split_part(l, ',', 6), '')::numeric as preco_min,
           nullif(split_part(l, ',', 7), '')::numeric as preco_medio,
           nullif(split_part(l, ',', 8), '')::numeric as preco_max
      from regexp_split_to_table(texto, '\r?\n') as l
     where l <> '' and l not like 'coletado_em,%';

  insert into public.precos (coletado_em, colecao, numero, preco_min, preco_medio, preco_max)
  select coletado_em, colecao, numero, preco_min, preco_medio, preco_max from pg_temp.csv
  on conflict do nothing;
  get diagnostics inseridas = row_count;

  -- Catalogo: nome em ingles da linha mais recente; o em portugues da mais
  -- recente que tiver (so a Liga tinha).
  insert into public.cartas as c (colecao, numero, nome_en, nome_pt, visto_em)
  select distinct on (colecao, numero) colecao, numero, nome_en,
         coalesce((select x.nome_pt from pg_temp.csv x
                    where x.colecao = t.colecao and x.numero = t.numero and x.nome_pt <> ''
                    order by x.coletado_em desc limit 1), ''),
         coletado_em
    from pg_temp.csv t
   order by colecao, numero, coletado_em desc
  on conflict (colecao, numero) do update
    set nome_pt = case when c.nome_pt = '' then excluded.nome_pt else c.nome_pt end,
        visto_em = greatest(c.visto_em, excluded.visto_em);

  insert into public.coletas (momento, origem, cartas, com_preco, dolar)
  select coletado_em, 'importado', count(*), count(preco_medio), null
    from pg_temp.csv group by coletado_em
  on conflict do nothing;

  drop table pg_temp.csv;
  return inseridas;
end;
$$;

revoke all on all functions in schema coleta from public, anon, authenticated;

-- ------------------------------------------------------ funcoes da API
-- Tudo que o painel precisa numa chamada so, no mesmo formato que o script
-- antigo embutia no HTML: hora a hora nos ultimos 14 dias e um ponto por dia
-- (a media) antes disso. s = [[segundos unix, preco medio, preco minimo]].
-- Mostra as cartas vistas na ultima coleta.
create or replace function public.painel_precos()
returns json
language sql
stable
security invoker
set search_path = ''
set statement_timeout = '20s'
as $$
  with corte as (
    select now() - interval '14 days' as t
  ),
  pontos as (
    select p.colecao, p.numero, extract(epoch from p.coletado_em)::bigint as x,
           p.preco_medio as m, p.preco_min as n
      from public.precos p, corte
     where p.coletado_em >= corte.t
    union all
    select p.colecao, p.numero, avg(extract(epoch from p.coletado_em))::bigint,
           round(avg(p.preco_medio), 2), round(avg(p.preco_min), 2)
      from public.precos p, corte
     where p.coletado_em < corte.t
     group by p.colecao, p.numero, (p.coletado_em at time zone 'America/Sao_Paulo')::date
  ),
  series as (
    select colecao, numero, json_agg(json_build_array(x, m, n) order by x) as s
      from pontos
     group by colecao, numero
  ),
  visiveis as (
    select c.*
      from public.cartas c
     where c.visto_em >= (select max(visto_em) from public.cartas) - interval '1 day'
  )
  select json_build_object(
    'gerado_em', (select max(momento) from public.coletas),
    'coletas', (select count(*) from public.coletas),
    'edicoes', json_build_object('30C', 'Celebração de 30 Anos', '30C-C', 'Cartas Clássicas'),
    'cartas', coalesce((
      select json_agg(json_build_object(
               'k', v.colecao || '/' || v.numero,
               'col', v.colecao, 'num', v.numero,
               'en', v.nome_en, 'pt', v.nome_pt,
               'img', v.imagem, 'url', v.url,
               's', coalesce(s.s, '[]'::json))
             order by v.colecao, v.numero)
        from visiveis v
        left join series s on s.colecao = v.colecao and s.numero = v.numero
    ), '[]'::json)
  )
$$;

revoke all on function public.painel_precos() from public;
grant execute on function public.painel_precos() to anon, authenticated;

-- Botao "Atualizar precos agora" do painel. Qualquer um com o painel pode
-- apertar; a espera minima entre coletas segura o gasto da cota da
-- PokeWallet. O statement_timeout da funcao vale para a chamada pela API (o
-- anon tem 3 s por padrao, e a coleta leva uns 5 a 15).
create or replace function public.atualizar_precos()
returns json
language plpgsql
security definer
set search_path = ''
set statement_timeout = '120s'
as $$
declare
  espera constant interval := interval '5 minutes';
  ultimo timestamptz;
begin
  if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtext('coleta.coletar_precos')) then
    return json_build_object('ok', false, 'erro', 'já tem uma coleta em andamento');
  end if;
  select max(momento) into ultimo from public.coletas where origem <> 'importado';
  if ultimo is not null and ultimo > now() - espera then
    return json_build_object('ok', false, 'liberado_em', ultimo + espera);
  end if;
  begin
    return coleta.coletar_precos('botao');
  exception when others then
    return json_build_object('ok', false, 'erro', sqlerrm);
  end;
end;
$$;

revoke all on function public.atualizar_precos() from public;
grant execute on function public.atualizar_precos() to anon, authenticated;

-- ------------------------------------------------------------ agendamento
-- cron.schedule com um nome que ja existe so atualiza o job.
select cron.schedule('coleta-precos', '17 * * * *', $$select coleta.coletar_precos('cron')$$);
select cron.schedule('compacta-precos', '41 6 * * *', $$select coleta.compactar_precos()$$);
-- O pg_cron guarda cada execucao; uma semana basta para investigar falha.
select cron.schedule('limpa-cron', '43 6 * * *',
  $$delete from cron.job_run_details where end_time < now() - interval '7 days'$$);

-- ------------------------------------------------------ primeira carga
select coleta.importar_csv('https://raw.githubusercontent.com/afonsolelis/monitor-spkids/main/dados/precos-cartas.csv') as linhas_importadas;
select coleta.coletar_precos('manual') as primeira_coleta;
