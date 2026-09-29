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
import base64
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
    def slug(self) -> str:
        return self.url.rstrip("/").rsplit("/", 1)[-1]

    @property
    def a_venda(self) -> bool:
        """Cadastrado nao basta: a loja publica a pagina com preco antes de
        liberar a compra, com estoque zerado. `is_purchasable` nao serve de
        sinal: sem login ele vem falso em todo o catalogo, ate com estoque."""
        return self.em_estoque

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
        return any(alvo == s or alvo == _sem_acento(n) for s, n in zip(self.slugs, self.categorias, strict=False))

    def campos_de_busca(self, profundo: bool) -> str:
        partes = [self.nome, self.sku, *self.categorias]
        if profundo:
            partes.append(self.descricao)
        return " | ".join(partes)


# ------------------------------------------------------------------- coleta
def criar_sessao(timeout_retries: int = 3, repetir_post: bool = True) -> requests.Session:
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
        # A sessao da compra nao repete POST: um checkout repetido depois de um
        # 502 pode virar dois pedidos.
        allowed_methods=("GET", "POST") if repetir_post else ("GET",),
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


def consultar_ocultos(
    sessao: requests.Session,
    slugs: list[str],
    ids: dict[str, int],
    timeout: float = 30.0,
) -> tuple[list[Produto], list[str]]:
    """Situacao de venda das paginas que estao fora do catalogo.

    A listagem da Store API esconde esses produtos (nem `?slug=` os acha), mas
    `/products/{id}` responde normalmente, com estoque e se da para comprar. O
    id sai da classe `postid-N` da pagina e fica em `ids` (gravado no estado)
    para nao baixar a pagina de novo a cada execucao.

    Devolve (produtos consultados, slugs que nao deu para consultar).
    """
    produtos: list[Produto] = []
    falhas: list[str] = []
    for slug in slugs:
        try:
            if slug not in ids:
                pagina = sessao.get(
                    f"{SITE}/produto/{slug}/", timeout=timeout, headers={"Accept": "text/html"}
                )
                pagina.raise_for_status()
                achado = re.search(r"\bpostid-(\d+)\b", pagina.text)
                if not achado:
                    raise ValueError("id do produto nao encontrado na pagina")
                ids[slug] = int(achado.group(1))
            resposta = sessao.get(f"{API}/products/{ids[slug]}", timeout=timeout)
            resposta.raise_for_status()
            produtos.append(Produto.da_api(resposta.json()))
        except (requests.RequestException, ValueError) as erro:
            log.warning("nao consegui consultar a pagina oculta %s (%s)", slug, erro)
            ids.pop(slug, None)
            falhas.append(slug)
    return produtos, falhas


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
        log.info("estado gravado no Key Value (spkids:%s)", caminho.name)
        return
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8")
    log.info("estado gravado em %s", caminho)


def salvar_estado(
    caminho: Path,
    produtos: list[Produto],
    categorias: list[str],
    sitemap: list[str],
    alertados: set[str],
    ids_ocultos: dict[str, int],
    compras: dict[str, Any] | None = None,
) -> None:
    gravar_estado(
        caminho,
        {
            "produtos": {str(p.id): p.nome for p in produtos},
            "categorias": categorias,
            "sitemap": sorted(sitemap),
            "alertados": sorted(alertados),
            "ids_ocultos": dict(sorted(ids_ocultos.items())),
            "compras": compras or {},
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
    # Paginas fora do catalogo que deu para consultar pela API.
    ocultos: list[Produto] = field(default_factory=list)
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
            | {chave_oculto(p) for p in self.ocultos}
        )

    @property
    def e_lancamento(self) -> bool:
        return bool(
            self.por_termo or self.categorias_termo or self.ocultos_suspeitos or self.ocultos
        )

    @property
    def lancamento_novo(self) -> bool:
        return bool(self.chaves_lancamento() - self.ja_avisados)

    @property
    def venda_nova(self) -> bool:
        """Algum produto passou a poder ser comprado desde o ultimo aviso."""
        return any(
            p.a_venda and chave_produto(p) not in self.ja_avisados
            for p in [*self.por_termo, *self.ocultos]
        )

    @property
    def houve_novidade(self) -> bool:
        return self.lancamento_novo or bool(
            self.produtos_novos or self.categorias_novas or self.slugs_novos
        )


def chave_produto(p: Produto) -> str:
    """Inclui a venda: sair da pre-venda e poder comprar avisa de novo."""
    return f"p:{p.id}:{'estoque' if p.a_venda else 'fora'}"


def chave_oculto(p: Produto) -> str:
    """Pagina oculta sem venda mantem a chave do sitemap (`s:slug`), a mesma de
    antes de consultar a API: quem ja foi avisado da pagina nao recebe de novo."""
    return chave_produto(p) if p.a_venda else f"s:{p.slug}"


def situacao(p: Produto) -> str:
    if p.a_venda:
        return "JA DA PARA COMPRAR"
    return "ainda nao da para comprar: sem estoque"


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
            linhas.append(
                f"- {marca(chave_produto(p))}{p.nome}\n"
                f"  {p.preco_formatado} ({situacao(p)})\n  {p.url}"
            )
        if a.ocultos or a.ocultos_suspeitos:
            linhas.append(
                "\nPaginas ja criadas, ainda invisiveis no catalogo "
                "(achadas no sitemap — o site pode estar montando a colecao agora):"
            )
            for p in a.ocultos:
                linhas.append(
                    f"- {marca(chave_oculto(p))}{p.nome}\n"
                    f"  {p.preco_formatado} ({situacao(p)})\n  {p.url}"
                )
            for slug in a.ocultos_suspeitos:
                linhas.append(f"- {marca(f's:{slug}')}{slug}\n  {SITE}/produto/{slug}/")

    # Novidades genericas que ja nao apareceram acima.
    ids_termo = {p.id for p in a.por_termo}
    prods = [p for p in a.produtos_novos if p.id not in ids_termo]
    cats = [c for c in a.categorias_novas if c not in a.categorias_termo]
    slugs_prods = {p.slug for p in a.produtos_novos}
    slugs_ocultos = set(a.ocultos_suspeitos) | {p.slug for p in a.ocultos}
    somente_sitemap = [
        s for s in a.slugs_novos if s not in slugs_prods and s not in slugs_ocultos
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

    if a.venda_nova:
        titulo = "Pokemon 30 anos: JA DA PARA COMPRAR na SP Kids!"
    elif a.lancamento_novo:
        titulo = "Pokemon 30 anos: cadastrada na SP Kids, mas ainda sem venda"
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
    anexos: list[tuple[bytes, str, str]] | None = None,
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
    for dados, subtipo, nome in anexos or []:
        mensagem.add_attachment(dados, maintype="image", subtype=subtipo, filename=nome)

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


# ---------------------------------------------------------------------- login
def entrar(sessao: requests.Session, timeout: float = 30.0) -> str:
    """Autentica no WooCommerce com SPKIDS_EMAIL e SPKIDS_SENHA do ambiente.

    Devolve "" se entrou, ou o motivo da falha. A senha nunca e gravada nem
    registrada no log. Sem login a Store API ja mostra estoque e preco; ele so
    e preciso para montar o carrinho e fechar o pedido.
    """
    email = os.environ.get("SPKIDS_EMAIL", "")
    senha = os.environ.get("SPKIDS_SENHA", "")
    if not (email and senha):
        return "defina SPKIDS_EMAIL e SPKIDS_SENHA no ambiente"

    url = f"{SITE}/minha-conta/"
    pagina = sessao.get(url, timeout=timeout, headers={"Accept": "text/html"})
    pagina.raise_for_status()
    nonce = re.search(
        r'name="woocommerce-login-nonce"\s+value="([^"]+)"', pagina.text
    )
    if not nonce:
        return "nonce de login nao encontrado — o formulario do site mudou"

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
        return ""
    motivo = re.sub(r"<[^>]+>", " ", erro.group(0)) if erro else "sem cookie de sessao"
    return " ".join(_texto(motivo).split())


def testar_login(sessao: requests.Session, timeout: float = 30.0) -> bool:
    motivo = entrar(sessao, timeout)
    if motivo:
        print(f"login FALHOU: {motivo}")
        return False
    print("login OK: sessao autenticada (cookie wordpress_logged_in_ recebido)")
    return True


# --------------------------------------------------------------------- compra
@dataclass
class Compra:
    """O que aconteceu na tentativa de compra, para o e-mail e o estado."""

    produto: Produto
    quantidade: int
    no_carrinho: bool = False
    tentou_checkout: bool = False
    pedido: int | None = None
    link: str = ""
    pix: str = ""
    validade: str = ""
    # Imagem do QR Code como a pagina do pedido entrega: (bytes, subtipo MIME).
    qrcode: tuple[bytes, str] | None = None
    erro: str = ""

    @property
    def ok(self) -> bool:
        return self.pedido is not None and not self.erro


def _mensagem(resposta: requests.Response) -> str:
    try:
        corpo = resposta.json()
    except ValueError:
        return f"HTTP {resposta.status_code}"
    return _texto(re.sub(r"<[^>]+>", " ", str(corpo.get("message", ""))))


def _normalizar(valor: str) -> str:
    return " ".join(_sem_acento(valor).split())


def comprar(
    sessao: requests.Session,
    produto: Produto,
    quantidade: int,
    frete: str,
    timeout: float = 30.0,
) -> Compra:
    """Monta o carrinho da conta e fecha o pedido com Pix (PagHiper).

    So segue com o carrinho vazio: o pedido fecharia tudo que estivesse nele,
    inclusive coisas que o dono da conta deixou ali por outro motivo. Pedido
    minimo, frete e dados de cobranca vem da propria loja e da conta.
    """
    compra = Compra(produto=produto, quantidade=quantidade)
    try:
        motivo = entrar(sessao, timeout)
        if motivo:
            compra.erro = f"login falhou: {motivo}"
            return compra

        cabecalhos = {"Content-Type": "application/json"}

        def api(metodo: str, rota: str, **kw: Any) -> requests.Response:
            resposta = sessao.request(
                metodo, f"{API}/{rota}", headers=cabecalhos, timeout=timeout, **kw
            )
            # A Store API troca o nonce a cada resposta; POST sem ele e recusado.
            if resposta.headers.get("Nonce"):
                cabecalhos["Nonce"] = resposta.headers["Nonce"]
            return resposta

        carrinho = api("GET", "cart").json()
        if carrinho.get("items_count"):
            compra.erro = (
                "o carrinho da conta ja tinha itens; nao mexi nele para nao fechar "
                "um pedido com o que voce nao pediu"
            )
            return compra

        resposta = api("POST", "cart/add-item", json={"id": produto.id, "quantity": quantidade})
        if not resposta.ok:
            compra.erro = f"nao entrou no carrinho: {_mensagem(resposta)}"
            return compra
        compra.no_carrinho = True

        carrinho = api("GET", "cart").json()
        alvo = _normalizar(frete)
        escolhido = None
        for pacote in carrinho.get("shipping_rates", []):
            for taxa in pacote.get("shipping_rates", []):
                if alvo in _normalizar(_texto(taxa.get("name", ""))):
                    escolhido = (pacote["package_id"], taxa["rate_id"])
                    break
        if escolhido is None:
            compra.erro = f"frete {frete!r} nao oferecido para este carrinho"
            return compra
        resposta = api(
            "POST",
            "cart/select-shipping-rate",
            json={"package_id": escolhido[0], "rate_id": escolhido[1]},
        )
        if not resposta.ok:
            compra.erro = f"nao consegui escolher o frete: {_mensagem(resposta)}"
            return compra
        carrinho = resposta.json()
        if carrinho.get("errors"):
            compra.erro = "; ".join(
                _texto(e.get("message", "")) for e in carrinho["errors"]
            )
            return compra

        cobranca = carrinho.get("billing_address") or {}
        entrega = carrinho.get("shipping_address") or {}
        # Entrega em branco na conta: a loja usa a cobranca, mas a Store API
        # valida os dois enderecos.
        if not entrega.get("address_1"):
            entrega = {k: v for k, v in cobranca.items() if k != "email"}

        compra.tentou_checkout = True
        resposta = api(
            "POST",
            "checkout",
            json={
                "billing_address": cobranca,
                "shipping_address": entrega,
                "payment_method": "paghiper_pix",
                "payment_data": [],
                "customer_note": "Pedido feito pelo monitor_spkids.",
            },
        )
        dados = resposta.json() if resposta.headers.get("Content-Type", "").startswith(
            "application/json"
        ) else {}
        compra.pedido = dados.get("order_id") or None
        resultado = dados.get("payment_result") or {}
        compra.link = resultado.get("redirect_url") or ""
        if not resposta.ok or resultado.get("payment_status") == "failure":
            detalhes = "; ".join(
                f"{d.get('key')}: {d.get('value')}" for d in resultado.get("payment_details", [])
            )
            compra.erro = f"checkout recusado: {_mensagem(resposta) or detalhes}"
            return compra

        if compra.link:
            # A pagina do pedido traz o Pix copia-e-cola do PagHiper.
            pagina = sessao.get(compra.link, timeout=timeout, headers={"Accept": "text/html"})
            ler_pagina_pix(sessao, compra, pagina.text, timeout)
    except (requests.RequestException, ValueError) as erro:
        compra.erro = f"falha de rede ou resposta inesperada: {erro}"
    return compra


def ler_pagina_pix(
    sessao: requests.Session, compra: Compra, pagina: str, timeout: float = 30.0
) -> None:
    """Tira da pagina do pedido o Pix copia-e-cola, o QR Code e a validade.

    O layout e do plugin do PagHiper e nunca foi visto (so aparece com pedido
    feito), entao tudo aqui e tentativa: o que nao achar fica vazio e o e-mail
    manda o link da pagina, que sempre funciona.
    """
    achado = re.search(r"000201[0-9A-Za-z .:/*@\-]{40,}?6304[0-9A-Fa-f]{4}", pagina)
    compra.pix = achado.group(0) if achado else ""

    texto = " ".join(_texto(re.sub(r"<[^>]+>", " ", pagina)).split())
    validade = re.search(
        r"(?:v[aá]lid[oa]|expira|vencimento|pague at[eé])[^.]{0,80}?"
        r"(\d{2}/\d{2}/\d{4}(?:\s*(?:[aà]s)?\s*\d{2}:\d{2})?|\d+\s*(?:minutos?|horas?|dias?))",
        texto,
        re.I,
    )
    compra.validade = validade.group(0) if validade else ""

    for tag, src in re.findall(r'(<img[^>]+src="([^"]+)"[^>]*>)', pagina):
        # O src de imagem embutida e so base64; quem diz que e o QR e a tag.
        if not re.search(r"qr|pix|paghiper", re.sub(r'src="[^"]*"', "", tag) + src[:200], re.I):
            continue
        embutida = re.match(r"data:image/(png|jpe?g|gif);base64,(.+)", src, re.S)
        try:
            if embutida:
                compra.qrcode = (base64.b64decode(embutida.group(2)), embutida.group(1))
            else:
                imagem = sessao.get(html.unescape(src), timeout=timeout)
                imagem.raise_for_status()
                tipo = imagem.headers.get("Content-Type", "")
                if not tipo.startswith("image/"):
                    continue
                compra.qrcode = (imagem.content, tipo.split("/", 1)[1].split(";")[0])
        except (requests.RequestException, ValueError):
            continue
        return


def texto_compra(c: Compra, frete: str) -> str:
    total = c.produto.preco * c.quantidade if c.produto.preco is not None else None
    valor = (
        Produto(0, "", "", "", total, c.produto.decimais, True).preco_formatado
        if total is not None
        else "?"
    )
    linhas = [f"Compra automatica: {c.quantidade}x {c.produto.nome} ({valor} + frete: {frete})"]
    if c.ok:
        linhas.append(f"PEDIDO FEITO: #{c.pedido}. Pague o Pix para garantir:")
        linhas.append(f"  {c.link or SITE + '/minha-conta/orders/'}")
        if c.validade:
            linhas.append(f"  Validade informada pela loja: {c.validade}")
        if c.qrcode:
            linhas.append("  O QR Code vai anexo a este e-mail.")
        if c.pix:
            linhas.append(f"  Pix copia e cola:\n  {c.pix}")
        linhas.append(
            "  O estoque fica reservado so enquanto o pedido espera o pagamento;"
            " se o Pix vencer, a loja pode cancelar o pedido."
        )
        return "\n".join(linhas)

    linhas.append(f"A COMPRA AUTOMATICA FALHOU: {c.erro}")
    if c.pedido:
        linhas.append(f"  Mas o pedido #{c.pedido} foi criado; confira em {SITE}/minha-conta/orders/")
    # Se ja entrou no carrinho, o link de adicionar dobraria a quantidade.
    passo1 = (
        f"ja esta no seu carrinho: {SITE}/carrinho/"
        if c.no_carrinho
        else f"{SITE}/?add-to-cart={c.produto.id}&quantity={c.quantidade}"
    )
    linhas.append(
        "  Faca na mao, rapido:\n"
        f"  1. {passo1}\n"
        f"  2. {SITE}/finalizar-compra/ (frete: {frete}, pagamento: Pix)"
    )
    return "\n".join(linhas)


def tentar_compra(
    args: argparse.Namespace, compras: dict[str, Any], conhecidos: list[Produto]
) -> Compra | None:
    """Compra o produto de --comprar se ele estiver a venda e ainda nao foi comprado.

    `compras` e o registro do estado: depois que o checkout foi tentado, nunca
    tenta de novo sozinho — um pedido criado com a resposta perdida no caminho
    viraria pedido duplicado. Falha antes do checkout (login, rede) tenta de novo
    na proxima execucao.
    """
    try:
        id_txt, _, qtd_txt = args.comprar.partition(":")
        alvo_id, quantidade = int(id_txt), int(qtd_txt or 1)
    except ValueError:
        log.error("--comprar deve ser ID:QTD (ex.: 2003:3), veio %r", args.comprar)
        return None
    if str(alvo_id) in compras:
        return None

    with criar_sessao(repetir_post=False) as sessao:
        produto = next((p for p in conhecidos if p.id == alvo_id), None)
        if produto is None:
            # Nem no catalogo nem entre as paginas ocultas achadas pelo nome.
            try:
                resposta = sessao.get(f"{API}/products/{alvo_id}", timeout=30)
                resposta.raise_for_status()
                produto = Produto.da_api(resposta.json())
            except (requests.RequestException, ValueError) as erro:
                log.warning("nao consegui consultar o produto %s da compra (%s)", alvo_id, erro)
                return None
        if not produto.a_venda:
            log.info("compra automatica: %s ainda sem estoque", produto.nome)
            return None

        log.warning("compra automatica: %s entrou em estoque, comprando %d", produto.nome, quantidade)
        compra = comprar(sessao, produto, quantidade, args.frete)

    if compra.ok:
        log.warning("compra automatica: pedido %s criado", compra.pedido)
    else:
        log.error("compra automatica falhou: %s", compra.erro)
    if compra.tentou_checkout or compra.ok:
        compras[str(alvo_id)] = {
            "em": datetime.now().astimezone().isoformat(timespec="seconds"),
            "quantidade": quantidade,
            "pedido": compra.pedido,
            "link": compra.link,
            "erro": compra.erro,
        }
    return compra


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
        "--comprar",
        default=os.environ.get("SPKIDS_COMPRA", ""),
        metavar="ID:QTD",
        help="fecha o pedido com Pix assim que o produto ID tiver estoque "
        "(ex.: 2003:3); usa SPKIDS_EMAIL/SPKIDS_SENHA. Compra uma vez so",
    )
    parser.add_argument(
        "--frete",
        default=os.environ.get("SPKIDS_FRETE", "Retirada"),
        help="trecho do nome do frete da compra automatica (padrao: Retirada)",
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
        visiveis = {p.slug for p in produtos}
        ocultos = [s for s in sitemap or [] if s not in visiveis]
        if sitemap is not None:
            log.info(
                "sitemap: %d paginas, %d ainda fora do catalogo", len(sitemap), len(ocultos)
            )

        estado = carregar_estado(args.estado)
        ja_avisados = set(estado.get("alertados", []))
        ids_ocultos = {
            s: int(i) for s, i in (estado.get("ids_ocultos") or {}).items() if s in ocultos
        }
        prods_ocultos, sem_consulta = consultar_ocultos(
            sessao, [s for s in ocultos if regex.search(s.replace("-", " "))], ids_ocultos
        )
        prods_novos, cats_novas, slugs_novos = novidades(
            estado, produtos, categorias, sitemap
        )

        achados = Achados(
            por_termo=por_termo,
            categorias_termo=cats_termo,
            ocultos_suspeitos=sem_consulta,
            ocultos=prods_ocultos,
            produtos_novos=prods_novos,
            categorias_novas=cats_novas,
            slugs_novos=slugs_novos,
            total=len(produtos),
            ja_avisados=ja_avisados,
        )

        titulo, corpo = montar_relatorio(achados)

        compras = dict(estado.get("compras") or {})
        compra: Compra | None = None
        if args.comprar:
            compra = tentar_compra(args, compras, [*produtos, *prods_ocultos])
        if compra is not None:
            corpo = texto_compra(compra, args.frete) + "\n\n" + corpo
            titulo = (
                f"Pokemon 30 anos: PEDIDO #{compra.pedido} FEITO, pague o Pix!"
                if compra.ok
                else "Pokemon 30 anos: A VENDA ABRIU e a compra automatica falhou"
            )
        print(titulo)
        print()
        print(corpo)

        houve_novidade = achados.houve_novidade or compra is not None
        entregues: list[bool] = []
        if houve_novidade or args.sempre_notificar:
            if args.webhook:
                entregues.append(notificar(sessao, args.webhook, titulo, corpo))
            if args.email:
                anexos = (
                    [(compra.qrcode[0], compra.qrcode[1], f"pix-pedido-{compra.pedido}.{compra.qrcode[1]}")]
                    if compra is not None and compra.qrcode
                    else None
                )
                entregues.append(
                    enviar_email([d.strip() for d in args.email], titulo, corpo, anexos=anexos)
                )
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
            ids_ocultos,
            compras,
        )

    if falhou:
        log.error("nenhum canal entregou o aviso")
        return 1
    return 10 if houve_novidade else 0


if __name__ == "__main__":
    sys.exit(main())
