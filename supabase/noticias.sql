-- Coleta de noticias dentro do Supabase, no mesmo molde da coleta de precos:
-- a funcao coleta.coletar_noticias() le os feeds RSS/Atom de public.fontes,
-- grava em public.noticias e o pg_cron roda a cada meia hora. O site le tudo
-- por rpc('painel_noticias').
--
-- Rodar inteiro no SQL Editor do Supabase (ou psql -1 -f). Pode rodar de novo
-- sem estragar nada: as fontes sao atualizadas pelo id.

create extension if not exists http with schema extensions;
create extension if not exists pg_cron with schema pg_catalog;

-- -------------------------------------------------------------- tabelas
create table if not exists public.fontes (
  id        text primary key,
  nome      text not null,
  tema      text not null check (tema in ('tecnologia', 'ia', 'games', 'pokemon', 'pokemon-tcg', 'ciencia')),
  url       text not null,
  ativa     boolean not null default true,
  ok_em     timestamptz,
  erro      text,
  erro_em   timestamptz
);

-- O link e a identidade da noticia: o mesmo link em dois feeds entra uma vez.
create table if not exists public.noticias (
  link         text primary key,
  fonte        text not null references public.fontes (id) on delete cascade,
  tema         text not null,
  titulo       text not null,
  resumo       text not null default '',
  imagem       text not null default '',
  publicado_em timestamptz not null,
  coletado_em  timestamptz not null default now()
);
create index if not exists noticias_publicado_em on public.noticias (publicado_em desc);

alter table public.fontes enable row level security;
alter table public.noticias enable row level security;
revoke all on public.fontes, public.noticias from anon, authenticated;
grant select on public.fontes, public.noticias to anon, authenticated;
drop policy if exists "todos leem" on public.fontes;
drop policy if exists "todos leem" on public.noticias;
create policy "todos leem" on public.fontes for select to anon, authenticated using (true);
create policy "todos leem" on public.noticias for select to anon, authenticated using (true);

-- --------------------------------------------------------------- fontes
-- O Google Noticias por busca cobre Pokemon em portugues, que quase nao tem
-- site com RSS proprio; o "when:3d" corta materia velha que a busca devolve.
-- A PokeBeach responde 403 (Cloudflare) e ficou de fora.
insert into public.fontes as f (id, nome, tema, url) values
  ('tecnoblog',        'Tecnoblog',             'tecnologia',  'https://tecnoblog.net/feed/'),
  ('olhar-digital',    'Olhar Digital',         'tecnologia',  'https://olhardigital.com.br/feed/'),
  ('canaltech',        'Canaltech',             'tecnologia',  'https://canaltech.com.br/rss/'),
  ('the-verge',        'The Verge',             'tecnologia',  'https://www.theverge.com/rss/index.xml'),
  ('ars-technica',     'Ars Technica',          'tecnologia',  'https://feeds.arstechnica.com/arstechnica/index'),
  ('hacker-news',      'Hacker News',           'tecnologia',  'https://hnrss.org/frontpage?points=300'),
  ('verge-ia',         'The Verge',             'ia',          'https://www.theverge.com/rss/ai-artificial-intelligence/index.xml'),
  ('techcrunch-ia',    'TechCrunch',            'ia',          'https://techcrunch.com/category/artificial-intelligence/feed/'),
  ('mit-ia',           'MIT Technology Review', 'ia',          'https://www.technologyreview.com/topic/artificial-intelligence/feed'),
  ('ign-brasil',       'IGN Brasil',            'games',       'https://br.ign.com/feed.xml'),
  ('nintendoboy',      'Nintendo Boy',          'games',       'https://nintendoboy.com.br/feed/'),
  ('nintendo-life',    'Nintendo Life',         'games',       'https://www.nintendolife.com/feeds/latest'),
  ('eurogamer',        'Eurogamer',             'games',       'https://www.eurogamer.net/feed'),
  ('gn-pokemon',       'Google Notícias',       'pokemon',     'https://news.google.com/rss/search?q=Pok%C3%A9mon+-TCG+-cartas+when:3d&hl=pt-BR&gl=BR&ceid=BR:pt-419'),
  ('nl-pokemon',       'Nintendo Life',         'pokemon',     'https://www.nintendolife.com/feeds/news/tags/pokemon'),
  ('pokejungle',       'PokeJungle',            'pokemon',     'https://pokejungle.net/feed/'),
  ('gn-pokemon-tcg',   'Google Notícias',       'pokemon-tcg', 'https://news.google.com/rss/search?q=Pok%C3%A9mon+TCG+when:3d&hl=pt-BR&gl=BR&ceid=BR:pt-419'),
  ('gn-pokemon-tcg-en','Google News',           'pokemon-tcg', 'https://news.google.com/rss/search?q=%22Pok%C3%A9mon+TCG%22+when:3d&hl=en-US&gl=US&ceid=US:en'),
  ('nl-pokemon-tcg',   'Nintendo Life',         'pokemon-tcg', 'https://www.nintendolife.com/feeds/news/tags/pokemon-trading-card-game'),
  ('pesquisa-fapesp',  'Pesquisa FAPESP',       'ciencia',     'https://revistapesquisa.fapesp.br/feed/'),
  ('sciencedaily',     'ScienceDaily',          'ciencia',     'https://www.sciencedaily.com/rss/top/science.xml'),
  ('nasa',             'NASA',                  'ciencia',     'https://www.nasa.gov/feed/'),
  ('quanta',           'Quanta Magazine',       'ciencia',     'https://www.quantamagazine.org/feed/'),
  ('nature',           'Nature',                'ciencia',     'https://www.nature.com/nature.rss')
on conflict (id) do update
  set nome = excluded.nome, tema = excluded.tema, url = excluded.url;

-- ---------------------------------------------------- funcoes internas
create schema if not exists coleta;
revoke all on schema coleta from public, anon, authenticated;

-- HTML de resumo de feed -> texto corrido de ate `limite` caracteres. O XML
-- ja desfez uma camada de escape; o que sobra sao tags e entidades do HTML.
create or replace function coleta.texto(html text, limite integer default 280)
returns text
language plpgsql
immutable
set search_path = ''
as $$
declare
  t text := coalesce(html, '');
  m text[];
begin
  t := regexp_replace(t, '<(script|style)[^>]*>.*?</\1>', ' ', 'gis');
  t := regexp_replace(t, '<[^>]*>', ' ', 'g');
  t := replace(replace(replace(replace(t, '&lt;', '<'), '&gt;', '>'), '&quot;', '"'), '&nbsp;', ' ');
  t := replace(replace(replace(t, '&apos;', ''''), '&#039;', ''''), '&hellip;', '…');
  t := replace(replace(replace(replace(t, '&ndash;', '–'), '&mdash;', '—'), '&ordm;', 'º'), '&ordf;', 'ª');
  t := replace(replace(replace(replace(t, '&lsquo;', '‘'), '&rsquo;', '’'), '&ldquo;', '“'), '&rdquo;', '”');
  t := replace(replace(t, '&laquo;', '«'), '&raquo;', '»');
  -- Letra acentuada (&aacute; &ccedil; &otilde;...): a letra mais o acento
  -- combinante, normalizados num caractere so. O Canaltech usa muito.
  for m in select regexp_matches(t, '&([a-zA-Z])(acute|grave|circ|tilde|uml|cedil|ring);', 'g') loop
    t := replace(t, '&' || m[1] || m[2] || ';', normalize(m[1] || pg_catalog.chr(case m[2]
           when 'grave' then 768 when 'acute' then 769 when 'circ' then 770 when 'tilde' then 771
           when 'uml' then 776 when 'ring' then 778 else 807 end), nfc));
  end loop;
  -- Entidades numericas (&#8217; &#x2019;), uma de cada vez.
  for m in select regexp_matches(t, '&#([xX]?)([0-9a-fA-F]+);', 'g') loop
    begin
      t := replace(t, '&#' || m[1] || m[2] || ';',
                   pg_catalog.chr(case when m[1] = '' then m[2]::integer
                                       else ('x' || lpad(m[2], 8, '0'))::bit(32)::integer end));
    exception when others then
      null;
    end;
  end loop;
  t := replace(t, '&amp;', '&');
  t := btrim(regexp_replace(t, '\s+', ' ', 'g'));
  if length(t) > limite then
    t := rtrim(left(t, limite - 1)) || '…';
  end if;
  return t;
end;
$$;

-- Primeiro valor nao vazio de uma lista de expressoes XPath sobre o item. O
-- xpath() do Postgres devolve o resultado de string() escapado como XML;
-- desfaz esse escape (o &amp; por ultimo) para links e HTML sairem crus.
create or replace function coleta.xp(item xml, caminhos text[])
returns text
language plpgsql
immutable
set search_path = ''
as $$
declare
  c text;
  v text;
begin
  foreach c in array caminhos loop
    v := btrim((pg_catalog.xpath(c, item))[1]::text);
    if v <> '' then
      return replace(replace(replace(replace(replace(v,
        '&lt;', '<'), '&gt;', '>'), '&quot;', '"'), '&apos;', ''''), '&amp;', '&');
    end if;
  end loop;
  return null;
end;
$$;

-- Os itens de um feed RSS 2.0, RSS 1.0 (RDF) ou Atom. Por local-name() para
-- nao depender do prefixo de namespace que cada site escolhe. Separada da
-- coleta para poder testar com um feed gravado.
create or replace function coleta.itens_do_feed(conteudo text)
returns table (titulo text, link text, resumo text, imagem text, publicado_em timestamptz)
language plpgsql
stable
set search_path = ''
as $$
declare
  doc xml;
  item xml;
  data_texto text;
  corpo text;
begin
  -- A declaracao <?xml encoding=...?> atrapalha o xmlparse de um text que ja
  -- esta em UTF-8.
  doc := xmlparse(document regexp_replace(conteudo, '^\s*<\?xml[^>]*\?>', ''));
  foreach item in array pg_catalog.xpath('//*[local-name()="item" or local-name()="entry"]', doc) loop
    titulo := coleta.texto(coleta.xp(item, array['string(/*/*[local-name()="title"])']), 300);
    link := coleta.xp(item, array[
      'string(/*/*[local-name()="link"][not(@href)])',
      'string(/*/*[local-name()="link"][@rel="alternate"]/@href)',
      'string(/*/*[local-name()="link"][not(@rel)]/@href)',
      'string(/*/*[local-name()="guid"][not(@isPermaLink="false")])']);
    corpo := coleta.xp(item, array[
      'string(/*/*[local-name()="description"])',
      'string(/*/*[local-name()="summary"])',
      'string(/*/*[local-name()="encoded"])',
      'string(/*/*[local-name()="content"])']);
    resumo := coleta.texto(corpo);
    -- Cabecalhos que nao sao resumo: o do Hacker News (so links) e o da
    -- Nature (revista, data e DOI).
    resumo := regexp_replace(resumo, '^Article URL: .*$', '');
    resumo := regexp_replace(resumo, '^[^;]*Published online: [^;]*; doi:\S+\s*', '');
    -- O Google Noticias repete o titulo ("Titulo - Site") como resumo.
    if resumo <> '' and starts_with(resumo, regexp_replace(titulo, '\s+-\s+[^-]+$', '')) then
      resumo := '';
    end if;
    imagem := coalesce(coleta.xp(item, array[
      'string(/*/*[local-name()="content" and (@medium="image" or starts-with(@type, "image"))]/@url)',
      'string(/*/*[local-name()="thumbnail"]/@url)',
      'string(/*/*[local-name()="group"]/*[local-name()="thumbnail"]/@url)',
      'string(/*/*[local-name()="group"]/*[local-name()="content"]/@url)',
      'string(/*/*[local-name()="enclosure" and starts-with(@type, "image")]/@url)',
      'string(/*/*[local-name()="content" and not(@type) and @url]/@url)']),
      substring(concat(corpo, ' ', coleta.xp(item, array['string(/*/*[local-name()="encoded"])']))
                from '<img[^>]+src="(https://[^"]+)"'),
      '');
    -- So imagem por https: o site e servido por https.
    if imagem !~ '^https://' then
      imagem := '';
    end if;
    data_texto := coleta.xp(item, array[
      'string(/*/*[local-name()="pubDate"])',
      'string(/*/*[local-name()="published"])',
      'string(/*/*[local-name()="updated"])',
      'string(/*/*[local-name()="date"])']);
    begin
      publicado_em := data_texto::timestamptz;
    exception when others then
      publicado_em := null;
    end;
    if titulo <> '' and link ~ '^https?://' then
      link := btrim(link);
      return next;
    end if;
  end loop;
end;
$$;

-- Busca e grava um feed. Devolve quantas noticias novas entraram.
create or replace function coleta.coletar_fonte(fonte public.fontes)
returns integer
language plpgsql
volatile
set search_path = ''
as $$
declare
  resposta extensions.http_response;
  inseridas integer;
begin
  -- O http conecta com 1 s de limite por padrao, pouco para o hnrss.org.
  perform extensions.http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS', '10000');
  perform extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS', '20000');
  resposta := extensions.http(('GET', fonte.url,
    array[extensions.http_header('User-Agent', 'Mozilla/5.0 (compatible; noticias-afonsolelis; +https://afonsolelis.github.io/monitor-spkids/)'),
          extensions.http_header('Accept', 'application/rss+xml, application/atom+xml, application/xml, text/xml')],
    null, null)::extensions.http_request);
  if resposta.status <> 200 then
    raise exception 'HTTP %', resposta.status;
  end if;

  insert into public.noticias (link, fonte, tema, titulo, resumo, imagem, publicado_em)
  select distinct on (i.link) i.link, fonte.id, fonte.tema, i.titulo, i.resumo, i.imagem,
         -- Sem data, ou com data no futuro: vale a hora em que chegou.
         case when i.publicado_em is null or i.publicado_em > now() + interval '1 hour'
              then now() else i.publicado_em end
    from (select * from coleta.itens_do_feed(resposta.content) limit 40) i
   -- Na primeira leitura de um feed vem o arquivo inteiro; o que passou de
   -- 14 dias nao e noticia.
   where i.publicado_em is null or i.publicado_em > now() - interval '14 days'
  on conflict (link) do nothing;
  get diagnostics inseridas = row_count;
  return inseridas;
end;
$$;

-- Todas as fontes ativas. Uma que falha nao derruba as outras: o erro fica
-- anotado na propria fonte (o site mostra) e a coleta segue.
create or replace function coleta.coletar_noticias()
returns json
language plpgsql
volatile
set search_path = ''
as $$
declare
  f public.fontes;
  novas integer := 0;
  falhas integer := 0;
begin
  if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtext('coleta.coletar_noticias')) then
    raise exception 'ja tem uma coleta de noticias em andamento';
  end if;
  for f in select * from public.fontes where ativa order by id loop
    begin
      novas := novas + coleta.coletar_fonte(f);
      update public.fontes set ok_em = now(), erro = null, erro_em = null where id = f.id;
    exception when others then
      falhas := falhas + 1;
      update public.fontes set erro = left(sqlerrm, 300), erro_em = now() where id = f.id;
    end;
  end loop;
  return json_build_object('ok', true, 'novas', novas, 'fontes_com_erro', falhas);
end;
$$;

revoke all on all functions in schema coleta from public, anon, authenticated;

-- ------------------------------------------------------ funcoes da API
-- Tudo o que o site precisa numa chamada: as fontes (com o ultimo erro) e as
-- noticias dos ultimos `dias`, no maximo 800, mais nova primeiro.
create or replace function public.painel_noticias(dias integer default 7)
returns json
language sql
stable
security invoker
set search_path = ''
set statement_timeout = '15s'
as $$
  select json_build_object(
    'gerado_em', (select max(ok_em) from public.fontes),
    'fontes', coalesce((
      select json_agg(json_build_object(
               'id', f.id, 'nome', f.nome, 'tema', f.tema,
               'ok_em', f.ok_em, 'erro', f.erro) order by f.tema, f.nome)
        from public.fontes f where f.ativa), '[]'::json),
    'noticias', coalesce((
      select json_agg(json_build_object(
               't', n.titulo, 'l', n.link, 'r', n.resumo, 'i', n.imagem,
               'f', n.fonte, 'tm', n.tema, 'p', n.publicado_em)
             order by n.publicado_em desc)
        from (select * from public.noticias
               where publicado_em > now() - make_interval(days => least(greatest(dias, 1), 30))
               order by publicado_em desc
               limit 800) n), '[]'::json)
  )
$$;

revoke all on function public.painel_noticias(integer) from public;
grant execute on function public.painel_noticias(integer) to anon, authenticated;

-- ------------------------------------------------------------ agendamento
select cron.schedule('coleta-noticias', '7,37 * * * *', $$select coleta.coletar_noticias()$$);
-- Noticia de tres meses atras nao volta a ser lida; o banco gratis tem 500 MB.
select cron.schedule('limpa-noticias', '45 6 * * *',
  $$delete from public.noticias where publicado_em < now() - interval '90 days'$$);

-- ------------------------------------------------------ primeira carga
select coleta.coletar_noticias() as primeira_coleta;
