#!/usr/bin/env python3
"""Monitor da colecao de 30 anos de Pokemon na SP Kids Distribuidora.

Le o catalogo pela Store API do WooCommerce (`/wp-json/wc/store/v1`), que e
publica e — ao contrario do HTML, onde o tema troca o preco por "Faca login
para ver o preco" — devolve os precos sem autenticacao. Sao 111 produtos no
total, entao a execucao baixa o catalogo inteiro e procura localmente, em vez
de depender da busca do site (que nao acha nem "anivers").

Cada execucao compara o catalogo com o estado da execucao anterior, de forma
que a colecao e detectada tanto pelos termos de busca quanto por ser um
produto ou categoria que simplesmente nao existia ontem.

Saida: relatorio no stdout e, com --webhook, uma notificacao no celular.
Codigo de saida 10 quando ha novidade (e o aviso foi entregue), 0 quando nao
ha, 1 em caso de erro — inclusive quando nenhum canal conseguiu avisar.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import smtplib
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("spkids")

SITE = "https://spkidsdistribuidora.com.br"
API = f"{SITE}/wp-json/wc/store/v1"
SITEMAP = f"{SITE}/wp-sitemap-posts-product-1.xml"
ESTADO_PADRAO = Path(__file__).resolve().parent / "dados" / "spkids-estado.json"

UA_NAVEGADOR = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# O que caracteriza a colecao de 30 anos. "30 anos" exige a palavra logo apos o
# numero, senao a "PASTA 3X3 C/ 30 FOLHAS" entraria como falso positivo.
PADROES = (
    r"30\s*anos",
    r"30\s*a[nñ]os",
    r"\b30\s*th\b",
    r"30\s*[ºo°]?\s*anivers",
    r"anivers[áa]rio",
    r"anniversary",
    # A colecao saiu como "Celebracao de 30 Anos" / "30th Celebration": se a SP
    # Kids cadastrar sem o numero, "celebra" ainda pega. Conferido contra os 111
    # produtos atuais, nao gera falso positivo.
    r"celebra",
)


def _texto(valor: str) -> str:
    """Nomes vem com entidades HTML (`&#8211;`) mesmo pela API."""
    return html.unescape(valor or "").strip()


def _sem_acento(valor: str) -> str:
    """"Coleções" e "colecoes" precisam casar: o usuario digita o slug."""
    decomposto = unicodedata.normalize("NFKD", valor.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


# --------------------------------------------------------------------- modelo
@dataclass
class Produto:
    id: int
    nome: str
    url: str
    sku: str
    preco: int | None
    decimais: int
    em_estoque: bool
    categorias: list[str] = field(default_factory=list)
    slugs: list[str] = field(default_factory=list)
    descricao: str = ""

    @classmethod
    def da_api(cls, bruto: dict[str, Any]) -> "Produto":
        precos = bruto.get("prices") or {}
        valor = precos.get("price")
        return cls(
            id=bruto["id"],
            nome=_texto(bruto.get("name", "")),
            url=bruto.get("permalink", ""),
            sku=bruto.get("sku", ""),
            preco=int(valor) if valor not in (None, "") else None,
            decimais=precos.get("currency_minor_unit", 2),
            em_estoque=bool(bruto.get("is_in_stock")),
            categorias=[_texto(c.get("name", "")) for c in bruto.get("categories", [])],
            slugs=[c.get("slug", "") for c in bruto.get("categories", [])],
            descricao=_texto(re.sub(r"<[^>]+>", " ", bruto.get("short_description", ""))),
        )

    @property
    def preco_formatado(self) -> str:
        if self.preco is None:
            return "sob consulta"
        return f"R$ {self.preco / (10 ** self.decimais):,.2f}".replace(",", "@").replace(
            ".", ","
        ).replace("@", ".")

    def na_categoria(self, chave: str) -> bool:
        """Aceita tanto o slug (`colecoes`) quanto o nome (`Coleções`)."""
        alvo = _sem_acento(chave)
        return any(alvo == s or alvo == _sem_acento(n) for s, n in zip(self.slugs, self.categorias))

    def campos_de_busca(self, profundo: bool) -> str:
        partes = [self.nome, self.sku, *self.categorias]
        if profundo:
            partes.append(self.descricao)
        return " | ".join(partes)


# ------------------------------------------------------------------- coleta
def criar_sessao(timeout_retries: int = 3) -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update(
        {
            "User-Agent": UA_NAVEGADOR,
            "Accept": "application/json",
            "Accept-Language": "pt-BR,pt;q=0.9",
        }
    )
    politica = Retry(
        total=timeout_retries,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        respect_retry_after_header=True,
    )
    adaptador = HTTPAdapter(max_retries=politica)
    sessao.mount("https://", adaptador)
    return sessao


def _paginar(sessao: requests.Session, rota: str, timeout: float) -> Iterator[dict[str, Any]]:
    pagina = 1
    while True:
        resposta = sessao.get(
            f"{API}/{rota}",
            params={"per_page": 100, "page": pagina},
            timeout=timeout,
        )
        resposta.raise_for_status()
        itens = resposta.json()
        if not itens:
            return
        yield from itens
        total_paginas = int(resposta.headers.get("X-WP-TotalPages", 1))
        if pagina >= total_paginas:
            return
        pagina += 1


def baixar_catalogo(
    sessao: requests.Session, timeout: float = 30.0
) -> tuple[list[Produto], list[str]]:
    produtos = [Produto.da_api(p) for p in _paginar(sessao, "products", timeout)]
    categorias = sorted(
        {_texto(c.get("name", "")) for c in _paginar(sessao, "products/categories", timeout)}
    )
    log.info("catalogo: %d produtos, %d categorias", len(produtos), len(categorias))
    return produtos, categorias


def baixar_sitemap(sessao: requests.Session, timeout: float = 30.0) -> list[str] | None:
    """Slugs de produto no sitemap — o aviso antecipado.

    A loja cria a pagina do produto antes de libera-la no catalogo: hoje o
    sitemap lista 168 produtos contra 111 visiveis na Store API. Um produto de
    30 anos aparece aqui assim que a pagina existe, mesmo que ainda nao de para
    ve-lo navegando pelo site.

    Devolve None se o sitemap falhar — diferente de lista vazia, para a
    execucao nao gravar "sitemap vazio" e a seguinte apontar as 168 paginas
    como novas.
    """
    try:
        resposta = sessao.get(SITEMAP, timeout=timeout, headers={"Accept": "application/xml"})
        resposta.raise_for_status()
    except requests.RequestException as erro:
        log.warning("sitemap indisponivel (%s); seguindo so com a API", erro)
        return None
    return [
        url.rstrip("/").rsplit("/", 1)[-1]
        for url in re.findall(r"<loc>([^<]+)</loc>", resposta.text)
    ]


# ------------------------------------------------------------------ deteccao
def compilar(padroes: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in padroes), re.IGNORECASE)


def procurar(
    produtos: list[Produto], categorias: list[str], regex: re.Pattern[str], profundo: bool
) -> tuple[list[Produto], list[str]]:
    achados = [p for p in produtos if regex.search(p.campos_de_busca(profundo))]
    cats = [c for c in categorias if regex.search(c)]
    return achados, cats


# -------------------------------------------------------------------- estado
# Na Render o cron job nao tem disco entre execucoes: com SPKIDS_REDIS_URL o
# estado vai para o Key Value, na chave com o nome do arquivo. Sem ela, arquivo.
def _redis() -> Any:
    url = os.environ.get("SPKIDS_REDIS_URL", "")
    if not url:
        return None
    import redis  # so e necessario na Render

    return redis.Redis.from_url(url, socket_timeout=15)


def carregar_estado(caminho: Path) -> dict[str, Any]:
    try:
        cliente = _redis()
        if cliente is not None:
            bruto = cliente.get(f"spkids:{caminho.name}")
            return json.loads(bruto) if bruto else {}
        if not caminho.exists():
            return {}
        return json.loads(caminho.read_text(encoding="utf-8"))
    except ValueError as erro:
        log.warning("estado ilegivel em %s (%s); tratando como primeira execucao", caminho, erro)
        return {}


def gravar_estado(caminho: Path, dados: dict[str, Any]) -> None:
    texto = json.dumps(
        {"verificado_em": datetime.now().astimezone().isoformat(timespec="seconds"), **dados},
        ensure_ascii=False,
        indent=2,
    )
    cliente = _redis()
    if cliente is not None:
        cliente.set(f"spkids:{caminho.name}", texto)
        return
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8")


def salvar_estado(
    caminho: Path,
    produtos: list[Produto],
    categorias: list[str],
    sitemap: list[str],
    alertados: set[str],
) -> None:
    gravar_estado(
        caminho,
        {
            "produtos": {str(p.id): p.nome for p in produtos},
            "categorias": categorias,
            "sitemap": sorted(sitemap),
            "alertados": sorted(alertados),
        },
    )


def novidades(
    estado: dict[str, Any],
    produtos: list[Produto],
    categorias: list[str],
    sitemap: list[str] | None,
) -> tuple[list[Produto], list[str], list[str]]:
    """O que nao existia na execucao anterior."""
    if not estado:
        return [], [], []  # primeira execucao: tudo e "novo", nao vale alarmar
    conhecidos = set(estado.get("produtos", {}))
    vistas = set(estado.get("categorias", []))
    # Sem sitemap agora, ou sem sitemap no estado (gravado antes de ele existir,
    # ou numa execucao em que ele falhou): nao ha base para comparar.
    antes = set(estado.get("sitemap") or [])
    slugs = [s for s in sitemap if s not in antes] if sitemap and antes else []
    return (
        [p for p in produtos if str(p.id) not in conhecidos],
        [c for c in categorias if c not in vistas],
        slugs,
    )


# ----------------------------------------------------------------- relatorio
@dataclass
class Achados:
    """Tudo que uma execucao encontrou, para o relatorio decidir o tom."""

    por_termo: list[Produto] = field(default_factory=list)
    categorias_termo: list[str] = field(default_factory=list)
    ocultos_suspeitos: list[str] = field(default_factory=list)
    produtos_novos: list[Produto] = field(default_factory=list)
    categorias_novas: list[str] = field(default_factory=list)
    slugs_novos: list[str] = field(default_factory=list)
    total: int = 0
    # Chaves ja avisadas em execucoes anteriores: sem isso, depois do lancamento
    # o monitor mandaria o mesmo "LANCOU" de hora em hora.
    ja_avisados: set[str] = field(default_factory=set)

    def chaves_lancamento(self) -> set[str]:
        return (
            {chave_produto(p) for p in self.por_termo}
            | {f"c:{c}" for c in self.categorias_termo}
            | {f"s:{s}" for s in self.ocultos_suspeitos}
        )

    @property
    def e_lancamento(self) -> bool:
        return bool(self.por_termo or self.categorias_termo or self.ocultos_suspeitos)

    @property
    def lancamento_novo(self) -> bool:
        return bool(self.chaves_lancamento() - self.ja_avisados)

    @property
    def houve_novidade(self) -> bool:
        return self.lancamento_novo or bool(
            self.produtos_novos or self.categorias_novas or self.slugs_novos
        )


def chave_produto(p: Produto) -> str:
    """Inclui o estoque: sair da pre-venda e entrar em estoque avisa de novo."""
    return f"p:{p.id}:{'estoque' if p.em_estoque else 'fora'}"


def montar_relatorio(a: Achados) -> tuple[str, str]:
    """Devolve (titulo, corpo) — o titulo serve de assunto do e-mail."""
    linhas: list[str] = []

    def marca(chave: str) -> str:
        return "[novo] " if chave not in a.ja_avisados else ""

    if a.e_lancamento:
        if a.categorias_termo:
            linhas.append(
                "Categorias com o termo: "
                + ", ".join(marca(f"c:{c}") + c for c in a.categorias_termo)
            )
        for p in a.por_termo:
            estoque = "em estoque" if p.em_estoque else "indisponivel/pre-venda"
            linhas.append(
                f"- {marca(chave_produto(p))}{p.nome}\n"
                f"  {p.preco_formatado} ({estoque})\n  {p.url}"
            )
        if a.ocultos_suspeitos:
            linhas.append(
                "\nPaginas ja criadas, ainda invisiveis no catalogo "
                "(achadas no sitemap — o site pode estar montando a colecao agora):"
            )
            for slug in a.ocultos_suspeitos:
                linhas.append(f"- {marca(f's:{slug}')}{slug}\n  {SITE}/produto/{slug}/")

    # Novidades genericas que ja nao apareceram acima.
    ids_termo = {p.id for p in a.por_termo}
    prods = [p for p in a.produtos_novos if p.id not in ids_termo]
    cats = [c for c in a.categorias_novas if c not in a.categorias_termo]
    slugs_prods = {p.url.rstrip("/").rsplit("/", 1)[-1] for p in a.produtos_novos}
    somente_sitemap = [
        s for s in a.slugs_novos if s not in slugs_prods and s not in a.ocultos_suspeitos
    ]
    genericas = bool(prods or cats or somente_sitemap)
    if genericas:
        if linhas:
            linhas.append("\nOutras novidades no catalogo:")
        if cats:
            linhas.append("Categorias novas: " + ", ".join(cats))
        for p in prods:
            linhas.append(f"- {p.nome}\n  {p.preco_formatado}\n  {p.url}")
        if somente_sitemap:
            linhas.append("\nPaginas novas no sitemap, ainda fora do catalogo:")
            linhas.extend(f"- {SITE}/produto/{s}/" for s in somente_sitemap)

    if a.lancamento_novo:
        titulo = "Pokemon 30 anos: LANCOU na SP Kids!"
    elif genericas:
        titulo = "SP Kids: produtos novos (sem sinal de 30 anos)"
    elif a.e_lancamento:
        titulo = "Pokemon 30 anos: nada mudou desde o ultimo aviso"
    else:
        titulo = "SP Kids: colecao de 30 anos ainda nao lancou"
        linhas.append(f"Nenhuma novidade. {a.total} produtos no catalogo.")

    return titulo, "\n".join(linhas)


def notificar(sessao: requests.Session, webhook: str, titulo: str, corpo: str) -> bool:
    """POST de texto puro — funciona direto com ntfy.sh, entre outros."""
    try:
        resposta = sessao.post(
            webhook,
            data=f"{titulo}\n\n{corpo}".encode("utf-8"),
            headers={"Title": titulo, "Content-Type": "text/plain; charset=utf-8"},
            timeout=20,
        )
        resposta.raise_for_status()
        log.info("notificacao enviada para %s", webhook)
        return True
    except requests.RequestException as erro:
        log.error("falha ao notificar: %s", erro)
        return False


def _corpo_html(titulo: str, corpo: str, assinatura: str, site: str) -> str:
    """Mesmo texto do e-mail, com os links dos produtos clicaveis."""
    escapado = html.escape(corpo)
    com_links = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', escapado)
    return (
        "<html><body>"
        f"<h2 style=\"font:600 18px system-ui,sans-serif\">{html.escape(titulo)}</h2>"
        "<pre style=\"font:14px/1.6 system-ui,sans-serif;white-space:pre-wrap\">"
        f"{com_links}</pre>"
        f'<p style="font:12px system-ui,sans-serif;color:#666">{assinatura} &middot; '
        f'<a href="{site}">{site}</a></p>'
        "</body></html>"
    )


def enviar_email(
    destinos: list[str],
    titulo: str,
    corpo: str,
    assinatura: str = "monitor_spkids",
    site: str = SITE,
) -> bool:
    """Envia o relatorio por SMTP.

    Servidor e credenciais vem do ambiente (SPKIDS_SMTP_*), nunca do codigo nem
    da linha de comando — `ps` mostra argumentos, entao senha ali vazaria para
    qualquer usuario da maquina. No Gmail, use uma Senha de App, nao a senha
    da conta.
    """
    host = os.environ.get("SPKIDS_SMTP_HOST", "smtp.gmail.com")
    porta = int(os.environ.get("SPKIDS_SMTP_PORTA", "587"))
    usuario = os.environ.get("SPKIDS_SMTP_USUARIO", "")
    senha = os.environ.get("SPKIDS_SMTP_SENHA", "")
    remetente = os.environ.get("SPKIDS_SMTP_DE") or usuario
    if not remetente:
        log.error("defina SPKIDS_SMTP_USUARIO (ou SPKIDS_SMTP_DE) para enviar e-mail")
        return False

    mensagem = EmailMessage()
    mensagem["Subject"] = titulo
    mensagem["From"] = remetente
    mensagem["To"] = ", ".join(destinos)
    mensagem.set_content(f"{corpo}\n\n--\n{assinatura} | {site}")
    mensagem.add_alternative(_corpo_html(titulo, corpo, assinatura, site), subtype="html")

    try:
        conexao = (
            smtplib.SMTP_SSL(host, porta, timeout=30)
            if porta == 465
            else smtplib.SMTP(host, porta, timeout=30)
        )
        with conexao as servidor:
            if porta != 465:
                try:
                    servidor.starttls()
                    servidor.ehlo()
                except smtplib.SMTPNotSupportedError:
                    if senha:
                        log.error(
                            "%s:%s nao oferece STARTTLS; recusando enviar a senha "
                            "em texto claro",
                            host,
                            porta,
                        )
                        return False
                    log.warning("conexao sem TLS com %s:%s", host, porta)
            if usuario and senha:
                servidor.login(usuario, senha)
            servidor.send_message(mensagem)
    except smtplib.SMTPAuthenticationError as erro:
        log.error(
            "SMTP recusou as credenciais (%s). No Gmail e preciso ativar a "
            "verificacao em duas etapas e usar uma Senha de App.",
            erro.smtp_code,
        )
        return False
    except (smtplib.SMTPException, OSError) as erro:
        log.error("falha ao enviar e-mail via %s:%s: %s", host, porta, erro)
        return False

    log.info("e-mail enviado para %s", ", ".join(destinos))
    return True


# ------------------------------------------------------------- teste de login
def testar_login(sessao: requests.Session, timeout: float = 30.0) -> bool:
    """Confere se as credenciais do ambiente autenticam no WooCommerce.

    Le SPKIDS_EMAIL e SPKIDS_SENHA do ambiente; a senha nunca e gravada nem
    registrada no log. O monitor nao precisa disso — a Store API ja devolve os
    precos sem login —, esta funcao existe so para responder se a autenticacao
    por script funciona.
    """
    email = os.environ.get("SPKIDS_EMAIL", "")
    senha = os.environ.get("SPKIDS_SENHA", "")
    if not (email and senha):
        log.error("defina SPKIDS_EMAIL e SPKIDS_SENHA no ambiente para testar o login")
        return False

    url = f"{SITE}/minha-conta/"
    pagina = sessao.get(url, timeout=timeout, headers={"Accept": "text/html"})
    pagina.raise_for_status()
    nonce = re.search(
        r'name="woocommerce-login-nonce"\s+value="([^"]+)"', pagina.text
    )
    if not nonce:
        log.error("nonce de login nao encontrado — o formulario do site mudou")
        return False

    resposta = sessao.post(
        url,
        data={
            "username": email,
            "password": senha,
            "woocommerce-login-nonce": nonce.group(1),
            "_wp_http_referer": "/minha-conta/",
            "rememberme": "forever",
            "login": "Acessar",
        },
        timeout=timeout,
        headers={"Accept": "text/html", "Referer": url},
    )
    resposta.raise_for_status()

    tem_cookie = any(c.startswith("wordpress_logged_in_") for c in sessao.cookies.keys())
    erro = re.search(
        r'<ul class="woocommerce-error".*?</ul>', resposta.text, re.S | re.I
    )
    if tem_cookie and not erro:
        print("login OK: sessao autenticada (cookie wordpress_logged_in_ recebido)")
        return True

    motivo = re.sub(r"<[^>]+>", " ", erro.group(0)) if erro else "sem cookie de sessao"
    print(f"login FALHOU: {' '.join(_texto(motivo).split())}")
    return False


# ----------------------------------------------------------------------- CLI
def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor_spkids",
        description="Avisa quando a colecao de 30 anos de Pokemon aparecer na SP Kids.",
    )
    parser.add_argument(
        "--termo",
        action="append",
        default=[],
        help="padrao extra de busca, em regex (pode repetir)",
    )
    parser.add_argument(
        "--categoria",
        help="limita a busca a um slug de categoria (ex.: colecoes, pokemon)",
    )
    parser.add_argument(
        "--profundo",
        action="store_true",
        help="procurar tambem na descricao curta (mais falso positivo)",
    )
    parser.add_argument(
        "--webhook",
        default=os.environ.get("SPKIDS_WEBHOOK", ""),
        help="URL que recebe a notificacao por POST (ex.: https://ntfy.sh/seu-topico)",
    )
    parser.add_argument(
        "--email",
        action="append",
        default=[d for d in os.environ.get("SPKIDS_ALERTA_PARA", "").split(",") if d.strip()],
        help="destinatario do alerta por e-mail (pode repetir); "
        "servidor e senha vem das variaveis SPKIDS_SMTP_*",
    )
    parser.add_argument(
        "--sempre-notificar",
        action="store_true",
        help="notificar tambem quando nao houver novidade",
    )
    parser.add_argument("--estado", type=Path, default=ESTADO_PADRAO, help="arquivo de estado")
    parser.add_argument(
        "--sem-estado",
        action="store_true",
        help="nao gravar estado (util para testes manuais)",
    )
    parser.add_argument(
        "--testar-login",
        action="store_true",
        help="so testa SPKIDS_EMAIL/SPKIDS_SENHA no site e sai",
    )
    parser.add_argument("-v", "--verboso", action="store_true", help="log em nivel DEBUG")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = montar_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verboso else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    with criar_sessao() as sessao:
        if args.testar_login:
            return 0 if testar_login(sessao) else 1

        try:
            produtos, categorias = baixar_catalogo(sessao)
        except requests.RequestException as erro:
            log.error("falha ao consultar o site: %s", erro)
            return 1

        # O estado sempre acompanha o catalogo inteiro; --categoria estreita
        # apenas a busca por termos, senao alternar entre os dois modos faria
        # a execucao seguinte apontar o catalogo todo como novidade.
        alvos = produtos
        if args.categoria:
            chave = args.categoria.lower()
            alvos = [p for p in produtos if p.na_categoria(chave)]
            if not alvos:
                log.error(
                    "nenhum produto na categoria %r — confira o slug em %s/wp-json"
                    "/wc/store/v1/products/categories",
                    args.categoria,
                    SITE,
                )
                return 1
            log.info("busca restrita a %d produtos em %r", len(alvos), args.categoria)

        regex = compilar(list(PADROES) + args.termo)
        por_termo, cats_termo = procurar(alvos, categorias, regex, args.profundo)

        sitemap = baixar_sitemap(sessao)
        visiveis = {p.url.rstrip("/").rsplit("/", 1)[-1] for p in produtos}
        ocultos = [s for s in sitemap or [] if s not in visiveis]
        if sitemap is not None:
            log.info(
                "sitemap: %d paginas, %d ainda fora do catalogo", len(sitemap), len(ocultos)
            )

        estado = carregar_estado(args.estado)
        ja_avisados = set(estado.get("alertados", []))
        prods_novos, cats_novas, slugs_novos = novidades(
            estado, produtos, categorias, sitemap
        )

        achados = Achados(
            por_termo=por_termo,
            categorias_termo=cats_termo,
            ocultos_suspeitos=[s for s in ocultos if regex.search(s.replace("-", " "))],
            produtos_novos=prods_novos,
            categorias_novas=cats_novas,
            slugs_novos=slugs_novos,
            total=len(produtos),
            ja_avisados=ja_avisados,
        )

        titulo, corpo = montar_relatorio(achados)
        print(titulo)
        print()
        print(corpo)

        houve_novidade = achados.houve_novidade
        entregues: list[bool] = []
        if houve_novidade or args.sempre_notificar:
            if args.webhook:
                entregues.append(notificar(sessao, args.webhook, titulo, corpo))
            if args.email:
                entregues.append(enviar_email([d.strip() for d in args.email], titulo, corpo))
        # Sem canal configurado o relatorio no stdout basta; com canal, basta um
        # ter entregado.
        falhou = bool(entregues) and not any(entregues)

    if falhou and houve_novidade:
        # Sem gravar o estado, a proxima execucao ve as mesmas novidades e tenta
        # avisar de novo, em vez de elas sumirem caladas.
        log.error("nenhum canal entregou o aviso; estado mantido para tentar de novo")
        return 1

    if not args.sem_estado:
        salvar_estado(
            args.estado,
            produtos,
            categorias,
            sitemap if sitemap is not None else estado.get("sitemap", []),
            ja_avisados | achados.chaves_lancamento(),
        )
        log.info("estado gravado em %s", args.estado)

    if falhou:
        log.error("nenhum canal entregou o aviso")
        return 1
    return 10 if houve_novidade else 0


if __name__ == "__main__":
    sys.exit(main())
