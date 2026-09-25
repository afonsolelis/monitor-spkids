# Monitor SP Kids + Copag — coleção Pokémon 30 anos

Avisa por e-mail quando a coleção de 30 anos de Pokémon aparecer em duas lojas:

- **SP Kids Distribuidora**: quando a coleção for listada.
- **Copag B2B** (`b2b.copagloja.com.br/pokemon`): a coleção já está cadastrada,
  mas sem estoque. O aviso sai quando algum item **entra em estoque**.

Roda a cada 15 minutos pelo cron, sem precisar de login em nenhum dos dois sites.

À parte, o Supabase grava de hora em hora o preço de todas as cartas das duas
edições (191 cartas), e um painel no GitHub Pages mostra a evolução, a tendência
de cada carta e uma caixa para marcar as que você já tem. Veja
[Preço das cartas](#preço-das-cartas).

---

## Como rodar

### 1. Clonar e instalar

```bash
git clone git@github.com:afonsolelis/monitor-spkids.git
cd monitor-spkids
./instalar.sh
```

O `instalar.sh` prepara tudo: cria o ambiente Python (tenta `uv`, depois
`venv`, e no pior caso usa o Python do sistema), cria as pastas, roda um teste
e instala o cron a cada 15 minutos. Rodar de novo é seguro — ele não duplica o
cron nem sobrescreve suas credenciais.

### 2. Colocar a Senha de App do Gmail

Sem isso o monitor roda e grava no log, mas **o e-mail não sai**.

1. Ative a verificação em 2 etapas na conta Google.
2. Gere uma Senha de App em https://myaccount.google.com/apppasswords
   (16 caracteres; a senha normal da conta não funciona em SMTP desde 2022).
3. Edite `~/.config/spkids/env` e preencha:

```bash
SPKIDS_ALERTA_PARA=voce@gmail.com
SPKIDS_SMTP_USUARIO=voce@gmail.com
SPKIDS_SMTP_SENHA=asenhadeapp16chars
```

### 3. Conferir que funciona

```bash
# roda uma vez e manda o e-mail mesmo sem novidade — serve de teste do SMTP
.venv/bin/python monitor_spkids.py --sempre-notificar --sem-estado
```

Se o e-mail chegar, está pronto. Se aparecer
`SMTP recusou as credenciais (535)`, a Senha de App está errada ou ausente.

### 4. Acompanhar

```bash
crontab -l                                  # confirmar o agendamento
tail -f ~/.local/state/spkids/monitor.log   # ver as execuções
./rodar_monitor.sh                          # rodar na mão agora (as duas lojas)
./rodar_monitor.sh copag                    # só a Copag
./rodar_monitor.sh spkids                   # só a SP Kids
./rodar_monitor.sh precos                   # coletar o preço das cartas agora
```

A partir daí é só esperar: o e-mail chega sozinho quando algo mudar.

---

## O que ele faz

### Por que não precisa de login

O site esconde os preços atrás de `Faça login para ver o preço`, mas isso é só
no HTML. A **Store API do WooCommerce** (`/wp-json/wc/store/v1`) é pública e
devolve os preços sem autenticação — conferidos contra o site logado, batem
exatamente. Nenhuma senha do site é usada, guardada ou necessária.

### Duas fontes, para não perder o lançamento

| Fonte | O que pega |
|---|---|
| Store API | os produtos visíveis no catálogo (hoje 111) |
| `wp-sitemap-posts-product-1.xml` | toda página de produto publicada (hoje 168) |

A diferença importa: a loja cria a página do produto **antes** de liberá-la no
catálogo. Um produto de 30 anos aparece no sitemap assim que a página existe,
o que dá vantagem enquanto a distribuidora ainda está organizando o estoque.

Além dos termos de busca, cada execução compara o catálogo com o da execução
anterior e avisa sobre **qualquer** produto ou categoria nova — rede de
segurança para o caso de a coleção entrar com um nome inesperado.

Os padrões foram validados contra os 40 nomes reais da coleção como listados
no mercado brasileiro, normalizados para o estilo do catálogo da SP Kids
(maiúscula, sem acento): 40 de 40 detectados, sem casar com falsos positivos
como `PASTA 3X3 C/ 30 FOLHAS`.

---

## Copag B2B

A loja roda em VTEX. A API de catálogo clássica recusa as consultas (os canais
de venda do B2B são restritos), mas o **Intelligent Search**
(`/api/io/_v/api/intelligent-search`) é público e devolve nome, categoria,
preço e estoque sem login. O catálogo inteiro tem cerca de 170 produtos, então
cada execução baixa tudo em 4 requisições.

Na primeira verificação (23/09/2026) os 8 itens da categoria
`Pokémon › 30 Anos` já estavam no catálogo, **todos com estoque zero**. Por
isso o monitor da Copag avisa sobre mudança de estoque, não sobre produto novo:

| Evento | Alerta |
|---|---|
| item de 30 anos passa de 0 para disponível | **urgente**: `DISPONIVEL na Copag B2B!` |
| item de 30 anos novo no catálogo | **urgente** |
| item de 30 anos esgota | informativo |
| outro produto Pokémon novo | informativo |
| página Pokémon nova no sitemap, ainda fora da busca | informativo |

Todo e-mail traz a situação atual dos itens da coleção (preço e estoque). O
preço mostrado é o público da loja; o preço B2B de quem está logado pode ser
diferente.

O estado fica em `dados/copag-estado.json`. Usa as mesmas credenciais de
e-mail e o mesmo webhook da SP Kids (`~/.config/spkids/env`).

```bash
.venv/bin/python monitor_copag.py --help
.venv/bin/python monitor_copag.py --sem-estado        # só ver a situação atual
```

---

## Preço das cartas

**https://afonsolelis.github.io/monitor-spkids/**

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

O workflow `Coleta no Supabase` (`.github/workflows/coleta.yml`) confere uma
vez por dia se a última coleta tem menos de 3 horas; se não tiver, falha e o
GitHub manda e-mail. O acesso diário também conta como uso, e o plano grátis
do Supabase pausa o projeto depois de 7 dias parado.

No SQL Editor do Supabase:

```sql
select * from public.coletas order by momento desc limit 10;       -- últimas coletas
select * from cron.job_run_details order by start_time desc limit 10;  -- erros do cron
select public.atualizar_precos();                                   -- coletar agora
```

O `painel.yml` só publica o HTML no Pages quando `painel_precos.html` muda.

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

## Opções

```bash
.venv/bin/python monitor_spkids.py --help

# só a categoria Coleções, sem gravar estado
.venv/bin/python monitor_spkids.py --categoria colecoes --sem-estado

# termo extra de busca (regex)
.venv/bin/python monitor_spkids.py --termo "escuridao absoluta"

# notificação no celular via ntfy, em vez de e-mail
.venv/bin/python monitor_spkids.py --webhook https://ntfy.sh/seu-topico

# conferir se as credenciais do site autenticam (opcional, não é necessário)
SPKIDS_EMAIL=... SPKIDS_SENHA=... .venv/bin/python monitor_spkids.py --testar-login
```

Por padrão só notifica quando há novidade. `--sempre-notificar` manda sempre.

### Códigos de saída

| Código | Significado |
|---|---|
| `0` | sem novidade |
| `10` | novidade encontrada (alerta enviado) |
| `1` | erro na verificação, ou nenhum canal conseguiu entregar o alerta |
| `75` | outra execução ainda rodando (só pelo `rodar_monitor.sh`) |

Se o alerta de uma novidade não for entregue, o estado **não** é gravado: a
execução seguinte vê a mesma novidade e tenta de novo.

Depois que a coleção lançar, o aviso "LANÇOU" não se repete a cada hora: o
estado lembra o que já foi avisado e só volta a avisar quando aparece produto
novo da coleção ou quando um deles entra em estoque (itens novos vêm marcados
com `[novo]`).

### Segurança do e-mail

SMTP com STARTTLS. Se o servidor não oferecer TLS e houver senha configurada,
o envio é **recusado** em vez de mandar a credencial em texto claro. As
credenciais ficam só em `~/.config/spkids/env` (permissão `600`), nunca no
código nem na linha de comando — `ps` mostra argumentos para qualquer usuário
da máquina.

---

## Arquivos fora do repositório

| Caminho | Conteúdo |
|---|---|
| `~/.config/spkids/env` | credenciais SMTP, permissão `600` |
| `~/.local/state/spkids/monitor.log` | log de cada execução |
| `dados/spkids-estado.json` | catálogo da SP Kids na execução anterior |
| `dados/copag-estado.json` | catálogo e estoque da Copag na execução anterior |

## Desenvolvimento

Regras do repositório (git trunk-based direto na `main`, Conventional Commits,
segredos, o que rodar antes de commitar) no [AGENTS.md](AGENTS.md). Os agentes
`dev`, `ux` e `devops` ficam em [`.agents/`](.agents/) e o Claude Code os
encontra por `.claude/agents`.

A cada push, o workflow `CI` confere as mensagens (Conventional Commits),
procura segredo no histórico (gitleaks) e roda ruff, shellcheck e actionlint.
Para ver o resultado de um push, o deploy do Pages e a saúde do Supabase:

```bash
scripts/analisar_push.sh          # o último commit da main
```

```bash
npm run test:visual             # regressão visual pixel a pixel do painel
npm run test:visual:atualizar   # regravar as referências (mudança intencional)
npm run test:visual:relatorio   # abrir o relatório com as diferenças
```

Os testes abrem `painel_precos.html` do disco com dados fixos
(`tests/visual/fixtures/painel.json`) e toda a rede interceptada, em quatro
perfis: desktop e celular, tema claro e escuro.

## Limitação conhecida

O cron não recupera execução perdida: se a máquina estiver suspensa às 14h00,
não roda 14h00 — roda na próxima janela de 15 minutos em que estiver acordada. Para garantia em máquina que dorme, troque por um
timer do systemd com `Persistent=true`.
