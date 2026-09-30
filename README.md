# Notícias + cartas Pokémon 30 anos

**https://afonsolelis.github.io/monitor-spkids/**

Um site com duas abas:

- **Notícias**: tecnologia, IA, games, Pokémon, Pokémon TCG e ciência, lidas
  dos feeds RSS de 24 fontes a cada meia hora.
- **Cartas 30 anos** (`precos.html`): o preço de todas as cartas das duas
  edições de 30 anos, com a evolução, a tendência e as marcações de "tenho" e
  "a chegar". Fica até a coleção estar completa.

Nada roda nesta máquina. As duas coletas rodam no `pg_cron` do Supabase
(projeto `lwamaovuxcevsjfvtqhf`) e o GitHub Pages serve só as páginas (que leem
do Supabase ao abrir).

> Até 30/09/2026 este repositório também monitorava a SP Kids e a Copag B2B
> esperando a coleção de 30 anos entrar em estoque, com compra automática. A
> compra foi feita e os monitores saíram; estão no histórico do git.

| Peça | Onde roda | Arquivo |
|---|---|---|
| Coleta de notícias | pg_cron no Supabase (`7,37 * * * *`) | `supabase/noticias.sql` |
| Coleta de preços | pg_cron no Supabase (`17 * * * *`) | `supabase/coleta_precos.sql` |
| Site | GitHub Pages | `index.html`, `painel_precos.html` |
| Checagem diária das coletas | GitHub Actions | `.github/workflows/coleta.yml` |

---

## Notícias

`coleta.coletar_noticias()` lê cada feed de `public.fontes` com a extensão
`http`, entende RSS 2.0, RSS 1.0 e Atom (por `local-name()`, sem depender do
prefixo de namespace de cada site) e grava título, link, resumo (texto puro,
até 280 caracteres), imagem e data em `public.noticias`. O link é a
identidade: a mesma notícia em dois feeds entra uma vez. Na primeira leitura
de um feed, o que passou de 14 dias fica de fora; o job `limpa-noticias` apaga
o que passou de 90.

Uma fonte que falha não derruba as outras: o erro fica anotado na própria
fonte (`fontes.erro`) e aparece no rodapé do site.

### Fontes

| Tema | Fontes |
|---|---|
| Tecnologia | Tecnoblog, Olhar Digital, Canaltech, The Verge, Ars Technica, Hacker News (300+ pontos) |
| IA | The Verge, TechCrunch, MIT Technology Review |
| Games | IGN Brasil, Nintendo Boy, Nintendo Life, Eurogamer |
| Pokémon | Google Notícias (busca em português), Nintendo Life, PokeJungle |
| Pokémon TCG | Google Notícias (português e inglês), Nintendo Life |
| Ciência | Pesquisa FAPESP, ScienceDaily, NASA, Quanta Magazine, Nature |

Pokémon em português quase não tem site com RSS próprio, por isso a busca do
Google Notícias (`when:3d` corta matéria velha). O site mostra o veículo real,
que o Google põe no fim do título. A PokeBeach responde 403 (Cloudflare) e
ficou de fora.

Para pôr ou tirar uma fonte, edite a lista no `supabase/noticias.sql` e rode o
script de novo (ele atualiza pelo `id`). Para só pausar uma:

```sql
update public.fontes set ativa = false where id = 'eurogamer';
```

### O site

- Filtro por tema (vai para o endereço: `#pokemon-tcg` abre já filtrado) e
  busca por palavra, sem acento.
- Agrupado por dia, 60 por vez, 7 dias para trás.
- Marca como "nova" o que chegou desde a sua última visita (guardado no navegador).
- A notícia abre no site de origem.

### Acompanhar

```sql
select id, ok_em, erro from public.fontes order by erro nulls last, id;  -- saúde das fontes
select tema, count(*) from public.noticias
 where publicado_em > now() - interval '1 day' group by 1;               -- volume do dia
select coleta.coletar_noticias();                                         -- coletar agora
```

## Preço das cartas

**https://afonsolelis.github.io/monitor-spkids/precos.html**

A coleta roda dentro do Supabase (projeto `lwamaovuxcevsjfvtqhf`), sem nada
nesta máquina nem no GitHub Actions. Tudo está em `supabase/coleta_precos.sql`:

- `coleta.coletar_precos()` busca a cotação do dólar e os preços na PokéWallet e
  grava em `public.precos`. O `pg_cron` roda de hora em hora (`17 * * * *`, job
  `coleta-precos`).
- O botão **Atualizar preços agora** chama `rpc('atualizar_precos')`: coleta na
  hora (uns 5 a 15 segundos) e o painel recarrega os dados. Entre duas coletas
  há uma espera de 5 minutos, porque o botão é aberto a quem tiver o link e a
  PokéWallet grátis dá 100 pedidos por hora.
- O GitHub Pages serve só a casca: ao abrir, o painel pede tudo de uma vez a
  `rpc('painel_precos')`.

Cobre as duas edições:

| Edição | Sigla | Set na PokéWallet | Cartas |
|---|---|---|---|
| Celebração de 30 Anos | `30C` | `24722` | 161 |
| Cartas Clássicas (Classic Collection) | `30C-C` | `24837` | 30 |

**Fonte: a API da PokéWallet.** As lojas brasileiras ficaram inviáveis: a MYP
Cards e a LigaPokemon respondem o desafio anti-robô do Cloudflare a qualquer
acesso automatizado (a Liga desde setembro de 2026), e contornar isso seria
burlar a proteção do site. A pokemontcg.io parou de publicar preços e sai do ar
em março de 2027. A PokéWallet tem plano grátis (100 pedidos por hora, 1.000
por dia) com os preços do TCGplayer; a busca devolve 100 cartas com preço por
pedido, então cada coleta gasta uns 5. A chave fica no Vault do Supabase
(segredo `pokewallet`) e nunca sai do banco.

**Os preços vêm em dólar e são convertidos.** Cada coleta pega a cotação do dia
(AwesomeAPI, com a open.er-api.com de reserva) e grava em real. É preço de
mercado americano convertido, não o que se paga numa loja daqui: serve para a
tendência, não para o bolso. As coletas até 23/09/2026 são da Liga, em real de
loja brasileira, por isso a série de cada carta dá um degrau nessa data.

### Tabelas

| Tabela | Conteúdo |
|---|---|
| `precos` | uma linha por carta por coleta: `coletado_em, colecao, numero, preco_min, preco_medio, preco_max`, em reais |
| `cartas` | catálogo: nome em inglês e em português (herdado da Liga), imagem, link do TCGplayer, quando foi vista por último |
| `coletas` | cada coleta: quando, de onde (`cron`, `botao`, `manual`, `importado`), quantas cartas, cotação |
| `cartas_marcadas` | as marcações de "tenho" (`supabase/cartas_marcadas.sql`) |

Visitante só lê `precos`, `cartas` e `coletas`; quem grava é a coleta. As
funções internas ficam no schema `coleta`, fora da API.

Para o banco não passar dos 500 MB do plano grátis, o job `compacta-precos`
reduz o que tem mais de 30 dias a um ponto por dia por carta (a média). O
painel já mostra assim o que passou de 14 dias: hora a hora nas duas últimas
semanas, um ponto por dia antes disso.

`dados/precos-cartas.csv` é o histórico da época em que a coleta rodava no
GitHub Actions (23 e 24/09/2026). Está no banco desde a instalação e fica no
repositório só como arquivo.

### Acompanhar

O workflow `Coletas no Supabase` (`.github/workflows/coleta.yml`) confere uma
vez por dia se a última coleta tem menos de 3 horas; se não tiver, falha e o
GitHub manda e-mail. O acesso diário também conta como uso, e o plano grátis
do Supabase pausa o projeto depois de 7 dias parado.

No SQL Editor do Supabase:

```sql
select * from public.coletas order by momento desc limit 10;       -- últimas coletas
select * from cron.job_run_details order by start_time desc limit 10;  -- erros do cron
select public.atualizar_precos();                                   -- coletar agora
```

O `site.yml` só publica no Pages quando `index.html` ou `painel_precos.html` muda.

### Instalar do zero

1. No SQL Editor do Supabase, guardar a chave da PokéWallet (grátis em
   pokewallet.io): `select vault.create_secret('<chave>', 'pokewallet');`
2. Colar e rodar `supabase/cartas_marcadas.sql` e depois
   `supabase/coleta_precos.sql`. O segundo instala as extensões `http` e
   `pg_cron`, cria as tabelas e os jobs, importa o CSV do repositório e faz a
   primeira coleta. Pode rodar de novo sem estragar nada.

### O painel

- Resumo no topo: quantas cartas você tem, quanto valem e quanto falta para completar.
- Tabela com preço médio, mínimo, uma mini-linha da evolução e a tendência em
  %/dia e R$/dia. Pode ordenar por qualquer coluna e filtrar por edição, por
  "só as que faltam"/"só as que tenho" e por nome.
- Clique numa carta para ver o gráfico completo: preço médio, preço mínimo e a reta
  da regressão, com a projeção para 7 dias e o R².
- **Tendência**: regressão linear simples do preço médio na janela escolhida
  (24 horas, 7 dias ou todo o período). Só aparece com pelo menos 3 coletas
  cobrindo 2 horas. R² baixo significa que o preço oscila mais do que segue
  uma direção.
- **Tenho**: fica no Supabase (tabela `cartas_marcadas`), uma lista só e sem
  login: quem abre o painel vê as mesmas marcações e pode mexer nelas. O
  navegador guarda uma cópia; quando o Supabase responde, vale o que está lá.
  A tabela só aceita códigos de carta (`30C/001`, `30C-C/12`...), para não
  virar depósito de outra coisa. **Exportar marcações** baixa uma cópia.

Aberto direto do disco (`file://`), o painel também funciona: ele lê do
Supabase do mesmo jeito.

---

## Desenvolvimento

```bash
git clone git@github.com:afonsolelis/monitor-spkids.git
cd monitor-spkids
./instalar.sh     # hooks do git e Playwright dos testes visuais
```

Regras do repositório (git trunk-based direto na `main`, Conventional Commits,
segredos, o que rodar antes de commitar) no [AGENTS.md](AGENTS.md). Os agentes
`dev`, `ux` e `devops` ficam em [`.agents/`](.agents/) e o Claude Code os
encontra por `.claude/agents`.

A cada push, o workflow `CI` confere as mensagens (Conventional Commits),
procura segredo no histórico (gitleaks) e roda shellcheck e actionlint.
Para ver o resultado de um push, o deploy do Pages e a saúde do Supabase:

```bash
scripts/analisar_push.sh          # o último commit da main
```

```bash
npm run test:visual             # regressão visual pixel a pixel das duas páginas
npm run test:visual:atualizar   # regravar as referências (mudança intencional)
npm run test:visual:relatorio   # abrir o relatório com as diferenças
```

Os testes abrem `index.html` e `painel_precos.html` do disco com dados fixos
(`tests/visual/fixtures/`) e toda a rede interceptada, em quatro perfis:
desktop e celular, tema claro e escuro.

## Arquivos fora do repositório

| Caminho | Conteúdo |
|---|---|
| `~/.config/spkids/supabase-db` | URL do Postgres com senha (para `psql` e `scripts/analisar_push.sh`) |
