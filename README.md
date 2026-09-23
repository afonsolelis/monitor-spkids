# Monitor SP Kids + Copag — coleção Pokémon 30 anos

Avisa por e-mail quando a coleção de 30 anos de Pokémon aparecer em duas lojas:

- **SP Kids Distribuidora**: quando a coleção for listada.
- **Copag B2B** (`b2b.copagloja.com.br/pokemon`): a coleção já está cadastrada,
  mas sem estoque. O aviso sai quando algum item **entra em estoque**.

Roda a cada 15 minutos pelo cron, sem precisar de login em nenhum dos dois sites.

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
| `1` | erro na verificação |

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

## Limitação conhecida

O cron não recupera execução perdida: se a máquina estiver suspensa às 14h00,
não roda 14h00 — roda na próxima janela de 15 minutos em que estiver acordada. Para garantia em máquina que dorme, troque por um
timer do systemd com `Persistent=true`.
