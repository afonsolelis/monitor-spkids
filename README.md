# Notícias + cartas Pokémon

**https://spkids.afonsolelis.tech/**

Um site com duas abas:

- **Notícias**: tecnologia, IA, games, Nintendo, Pokémon, Pokémon TCG e ciência,
  lidas dos feeds RSS de 28 fontes a cada meia hora.
- **Cartas** (`precos.html`): o preço de todas as cartas das edições de 30 anos
  e das de 25 anos (Celebrations), com a evolução, a tendência e as marcações de "tenho" e
  "a chegar". Fica até a coleção estar completa.

Nada roda nesta máquina. Tudo mora na VPS da Hostinger, como uma aplicação
docker-compose do Coolify construída deste repositório a cada push na main
(`hostinger/`): um Postgres 17 com `pg_cron` e `http`, onde rodam as duas
coletas; o PostgREST, que é a API que as páginas leem
(`https://spkids-api.afonsolelis.tech`); e um nginx com as duas páginas, que
são só a casca (leem da API ao abrir). O endereço antigo no GitHub Pages
redireciona para cá.

> Até 30/09/2026 este repositório também monitorava a SP Kids e a Copag B2B
> esperando a coleção de 30 anos entrar em estoque, com compra automática. A
> compra foi feita e os monitores saíram; estão no histórico do git.

| Peça | Onde roda | Arquivo |
|---|---|---|
| Coleta de notícias | pg_cron no Postgres da VPS (`7,37 * * * *`) | `banco/noticias.sql` |
| Coleta de preços | pg_cron no Postgres da VPS (`17 * * * *`) | `banco/coleta_precos.sql` |
| API | PostgREST na VPS | `hostinger/docker-compose.yml` |
| Site | nginx na VPS | `index.html`, `painel_precos.html`, `hostinger/site/` |
| Deploy | Coolify, por webhook do GitHub a cada push | `hostinger/` |
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
| Games | IGN Brasil, Eurogamer |
| Nintendo | Nintendo Boy, Nintendo Life, Nintendo Everything, Google Notícias (Zelda, Super Mario, Mario Kart, Mario Party e Switch 2) |
| Pokémon | Google Notícias (busca em português), Google News (Pokémon Winds and Waves, rumores e vazamentos), Nintendo Life, PokeJungle |
| Pokémon TCG | Google Notícias (português e inglês), Nintendo Life |
| Carros | Autoesporte, Quatro Rodas, g1 Carros, iG Carros, AutoPapo, FlatOut, Google Notícias (carros elétricos) |
| Eng. reversa | Google Notícias e Google News (ArtCraft, REA, engenharia reversa, decompilações e ports nativos de jogos retro), Hackaday |
| Ciência | Pesquisa FAPESP, ScienceDaily, NASA, Quanta Magazine, Nature |

Pokémon em português quase não tem site com RSS próprio, por isso a busca do
Google Notícias (`when:3d` corta matéria velha). O site mostra o veículo real,
que o Google põe no fim do título. A PokeBeach responde 403 (Cloudflare) e
ficou de fora. Em carros, Motor1 e InsideEVs também bloqueiam, e o UOL Carros
vem sem data e com acento quebrado.

### Mega-Sena

No topo da lista, antes do primeiro dia e com qualquer filtro, fica fixo o
último resultado da Mega-Sena: concurso, dezenas e se acumulou (com o prêmio
estimado do próximo) ou quantas apostas acertaram a sena. A página lê direto
`https://resultadomega.com.br/api/ultimo.json` (API pública, sem chave, com
CORS); a API do site não participa. Se ela falhar, o cartão só não aparece.

A mesma notícia em dois feeds entra uma vez, com o tema do primeiro que a
trouxe. Por isso a coleta lê Pokémon TCG, Pokémon e Nintendo antes dos temas
gerais: a matéria de Pokémon da Nintendo Life cai em Pokémon, não em Nintendo.

Para pôr ou tirar uma fonte, edite a lista no `banco/noticias.sql` e rode o
script de novo (`scripts/banco.sh -1 -v ON_ERROR_STOP=1 -f banco/noticias.sql`;
ele atualiza pelo `id`). Para só pausar uma:

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

**https://spkids.afonsolelis.tech/precos.html**

A coleta roda dentro do Postgres da VPS, sem nada nesta máquina nem no GitHub
Actions. Tudo está em `banco/coleta_precos.sql`:

- `coleta.coletar_precos()` busca a cotação do dólar e os preços na PokéWallet e
  grava em `public.precos`. O `pg_cron` roda de hora em hora (`17 * * * *`, job
  `coleta-precos`).
- O botão **Atualizar preços agora** chama `rpc('atualizar_precos')`: coleta na
  hora (uns 5 a 15 segundos) e o painel recarrega os dados. Entre duas coletas
  há uma espera de 5 minutos, porque o botão é aberto a quem tiver o link e a
  PokéWallet grátis dá 100 pedidos por hora.
- O nginx serve só a casca: ao abrir, o painel pede tudo de uma vez a
  `rpc('painel_precos')`.

Cobre as edições de 30 anos e as de 25 anos (Celebrations, 2021). As edições,
os sets e as buscas que as cobrem ficam nas views `coleta.edicoes` e
`coleta.buscas`:

| Edição | Sigla | Set na PokéWallet | Cartas |
|---|---|---|---|
| Celebração de 30 Anos | `30C` | `24722` | 161 |
| Cartas Clássicas (Classic Collection) | `30C-C` | `24837` | 30 |
| Promos de 30 Anos (com o selo) | `30C-P` | `24451` | 18 |
| Celebração de 25 Anos (Celebrations) | `25C` | `2867` | 25 |
| Clássicas de 25 Anos (Classic Collection) | `25C-C` | `2931` | 25 |
| Promos de 25 Anos (lista da PokéBeach) e as duas de metal | `25C-P` | `2545`, `2374` | 18 |

Os promos de 25 anos não têm "Celebrations" no nome na PokéWallet, por isso a
busca vai pelo prefixo do número (`SWSH13`, `SWSH14`, `SWSH062`) e só entram os
da lista (`coleta.promos_25`). Os quatro "15" da Classic Collection de 25 anos
ganham sufixo (`015`, `015b`, `015c`, `015d`), como os repetidos da de 30.

**Fonte: a API da PokéWallet.** As lojas brasileiras ficaram inviáveis: a MYP
Cards e a LigaPokemon respondem o desafio anti-robô do Cloudflare a qualquer
acesso automatizado (a Liga desde setembro de 2026), e contornar isso seria
burlar a proteção do site. A pokemontcg.io parou de publicar preços e sai do ar
em março de 2027. A PokéWallet tem plano grátis (100 pedidos por hora, 1.000
por dia) com os preços do TCGplayer; a busca devolve 100 cartas com preço por
pedido, então cada coleta gasta uns 8. A chave fica na tabela
`coleta.segredos` (schema fechado para a API) e nunca sai do banco.

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
| `cartas_marcadas` | as marcações de "tenho" (`banco/cartas_marcadas.sql`) |

Visitante só lê `precos`, `cartas` e `coletas`; quem grava é a coleta. As
funções internas ficam no schema `coleta`, fora da API.

Para o banco não crescer à toa (o disco da VPS é dividido com os outros
projetos), o job `compacta-precos`
reduz o que tem mais de 30 dias a um ponto por dia por carta (a média). O
painel já mostra assim o que passou de 14 dias: hora a hora nas duas últimas
semanas, um ponto por dia antes disso.

`dados/precos-cartas.csv` é o histórico da época em que a coleta rodava no
GitHub Actions (23 e 24/09/2026). Está no banco desde a instalação e fica no
repositório só como arquivo.

### Acompanhar

O workflow `Coletas na VPS` (`.github/workflows/coleta.yml`) confere uma vez
por dia, pela API pública, se a última coleta tem menos de 3 horas; se não
tiver, falha e o GitHub manda e-mail.

No banco (`scripts/banco.sh` abre um `psql` lá dentro por SSH):

```sql
select * from public.coletas order by momento desc limit 10;       -- últimas coletas
select * from cron.job_run_details order by start_time desc limit 10;  -- erros do cron
select public.atualizar_precos();                                   -- coletar agora
```

### Instalar do zero

Na VPS, a aplicação `monitor-spkids` do Coolify sobe o Postgres (imagem de
`hostinger/postgres/`, que cria os papéis e as extensões na primeira subida),
o PostgREST e o nginx. Depois, no banco:

1. Guardar a chave da PokéWallet (grátis em pokewallet.io):
   `insert into coleta.segredos values ('pokewallet', '<chave>');`
   (a tabela é criada por `banco/coleta_precos.sql`; se ainda não existir,
   `create schema coleta; create table coleta.segredos (nome text primary key, valor text not null);`).
2. Rodar `banco/noticias.sql`, `banco/cartas_marcadas.sql` e
   `banco/coleta_precos.sql`, cada um inteiro
   (`scripts/banco.sh -1 -v ON_ERROR_STOP=1 -f banco/x.sql`). O último
   instala `http` e `pg_cron`, cria as tabelas e os jobs, importa o CSV do
   repositório e faz a primeira coleta. Todos podem rodar de novo sem
   estragar nada.

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
- **Tenho**: fica no banco (tabela `cartas_marcadas`), uma lista só e sem
  login: quem abre o painel vê as mesmas marcações e pode mexer nelas. O
  navegador guarda uma cópia; quando o servidor responde, vale o que está lá.
  A tabela só aceita códigos de carta (`30C/001`, `25C-C/015b`...), para não
  virar depósito de outra coisa. **Exportar marcações** baixa uma cópia.

Aberto direto do disco (`file://`), o painel também funciona: ele lê da API
do mesmo jeito.

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
Para ver o resultado de um push, o deploy no Coolify e a saúde do banco:

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
| `~/.config/spkids/vps` | acesso ao banco na VPS, para `scripts/banco.sh` e `scripts/analisar_push.sh`: `SPKIDS_SSH_CHAVE` (caminho da chave), `SPKIDS_SSH_PORTA`, `SPKIDS_SSH_DESTINO` (`usuario@host`) e `SPKIDS_PG_CONTAINER` (nome do container do Postgres no Coolify) |
| `~/repos/hostinger/.env` | acesso à VPS e ao Coolify (`HOSTINGER_*`, `COOLIFY_*`) e as senhas do Postgres deste projeto (`SPKIDS_PG_*`) |
