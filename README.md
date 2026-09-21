# Monitor SP Kids — coleção Pokémon 30 anos

Avisa por e-mail quando a **SP Kids Distribuidora** listar a coleção de
30 anos de Pokémon. Roda de hora em hora pelo cron, sem precisar de login
no site.

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
e instala o cron de hora em hora. Rodar de novo é seguro — ele não duplica o
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
./rodar_monitor.sh                          # rodar na mão agora
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
| `dados/spkids-estado.json` | catálogo da execução anterior |

## Limitação conhecida

O cron não recupera execução perdida: se a máquina estiver suspensa às 14h,
não roda 14h — roda 15h. Para garantia em máquina que dorme, troque por um
timer do systemd com `Persistent=true`.
