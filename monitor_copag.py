#!/usr/bin/env python3
"""Monitor da colecao de 30 anos de Pokemon na loja B2B da Copag.

A loja roda em VTEX. A API de catalogo classica (`/api/catalog_system/pub`)
recusa consultas porque os canais de venda do B2B sao restritos, mas o
Intelligent Search (`/api/io/_v/api/intelligent-search`) e publico e devolve
nome, categoria, preco e estoque sem login.

Diferente da SP Kids, aqui a colecao ja esta cadastrada (categoria
`/Pokemon/30 Anos/`) — so que toda com estoque zero. Entao o evento que
importa nao e o produto aparecer, e sim ficar disponivel. Cada execucao
compara o estoque com o da execucao anterior e avisa quando um item de 30 anos
passa de 0 para disponivel, alem de produtos Pokemon novos.

Credenciais de e-mail e webhook sao as mesmas do monitor da SP Kids
(SPKIDS_*), lidas de ~/.config/spkids/env pelo rodar_monitor.sh.

Codigo de saida 10 quando ha novidade, 0 quando nao ha, 1 em caso de erro.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import requests

from monitor_spkids import (
    PADROES,
    _sem_acento,
    _texto,
    carregar_estado,
    compilar,
    criar_sessao,
    enviar_email,
    notificar,
)

log = logging.getLogger("copag")

SITE = "https://www.b2b.copagloja.com.br"
BUSCA = f"{SITE}/api/io/_v/api/intelligent-search/product_search/"
SITEMAP = f"{SITE}/sitemap/product-0.xml"
ESTADO_PADRAO = Path(__file__).resolve().parent / "dados" / "copag-estado.json"
POR_PAGINA = 50  # maximo aceito pelo Intelligent Search


# --------------------------------------------------------------------- modelo
@dataclass
class Produto:
    id: str
    nome: str
    slug: str
    preco: float | None
    estoque: int
    categorias: list[str] = field(default_factory=list)

    @classmethod
    def da_api(cls, bruto: dict[str, Any]) -> "Produto":
        # Nenhum produto da loja tem mais de um SKU ou vendedor (conferido),
        # entao o primeiro item/oferta representa o produto.
        itens = bruto.get("items") or [{}]
        vendedores = itens[0].get("sellers") or [{}]
        oferta = vendedores[0].get("commertialOffer") or {}
        return cls(
            id=str(bruto["productId"]),
            nome=_texto(bruto.get("productName", "")),
            slug=bruto.get("linkText", ""),
            preco=oferta.get("Price"),
            estoque=int(oferta.get("AvailableQuantity") or 0),
            categorias=[_texto(c) for c in bruto.get("categories", [])],
        )

    @property
    def url(self) -> str:
        return f"{SITE}/{self.slug}/p"

    @property
    def disponivel(self) -> bool:
        return self.estoque > 0

    @property
    def preco_formatado(self) -> str:
        if not self.preco:
            return "sob consulta"
        return f"R$ {self.preco:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")

    @property
    def e_pokemon(self) -> bool:
        """Categoria `/Pokemon/...` ou nome com Pokemon — "poker" nao casa."""
        return any(_sem_acento(c).startswith("/pokemon/") for c in self.categorias) or (
            "pokemon" in _sem_acento(self.nome)
        )

    def campos_de_busca(self) -> str:
        # A categoria entra porque e ela que carrega o "30 Anos" de forma
        # confiavel; o slug, porque ha produto com slug generico (box-greninja).
        return " | ".join([self.nome, self.slug, *self.categorias])

    def linha(self) -> str:
        estoque = "DISPONIVEL" if self.disponivel else "sem estoque"
        return f"- {self.nome}\n  {self.preco_formatado} ({estoque})\n  {self.url}"


# ------------------------------------------------------------------- coleta
def _paginar(sessao: requests.Session, timeout: float) -> Iterator[dict[str, Any]]:
    pagina = 1
    while True:
        resposta = sessao.get(
            BUSCA,
            params={"count": POR_PAGINA, "page": pagina, "locale": "pt-BR"},
            timeout=timeout,
        )
        resposta.raise_for_status()
        dados = resposta.json()
        itens = dados.get("products") or []
        yield from itens
        if not itens or pagina * POR_PAGINA >= int(dados.get("recordsFiltered", 0)):
            return
        pagina += 1


def baixar_catalogo(sessao: requests.Session, timeout: float = 30.0) -> list[Produto]:
    """O catalogo inteiro tem ~170 produtos: 4 requisicoes, e nao depende de a
    colecao continuar na categoria Pokemon."""
    produtos = [Produto.da_api(p) for p in _paginar(sessao, timeout)]
    log.info("catalogo: %d produtos, %d Pokemon", len(produtos), sum(p.e_pokemon for p in produtos))
    return produtos


def baixar_sitemap(sessao: requests.Session, timeout: float = 30.0) -> list[str]:
    """Slugs de produto no sitemap: a pagina existe antes de entrar na busca."""
    try:
        resposta = sessao.get(SITEMAP, timeout=timeout, headers={"Accept": "application/xml"})
        resposta.raise_for_status()
    except requests.RequestException as erro:
        log.warning("sitemap indisponivel (%s); seguindo so com a busca", erro)
        return []
    return re.findall(rf"<loc>{re.escape(SITE)}/([^<]+)/p</loc>", resposta.text)


# -------------------------------------------------------------------- estado
def salvar_estado(caminho: Path, produtos: list[Produto], sitemap: list[str]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(
            {
                "verificado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
                "produtos": {
                    p.id: {"nome": p.nome, "estoque": p.estoque, "preco": p.preco}
                    for p in produtos
                },
                "sitemap": sorted(sitemap),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ deteccao
@dataclass
class Achados:
    colecao: list[Produto] = field(default_factory=list)
    liberados: list[Produto] = field(default_factory=list)
    esgotados: list[Produto] = field(default_factory=list)
    colecao_nova: list[Produto] = field(default_factory=list)
    pokemon_novos: list[Produto] = field(default_factory=list)
    slugs_novos: list[str] = field(default_factory=list)

    @property
    def urgente(self) -> bool:
        return bool(self.liberados or self.colecao_nova)

    @property
    def houve_novidade(self) -> bool:
        return self.urgente or bool(self.esgotados or self.pokemon_novos or self.slugs_novos)


def analisar(
    estado: dict[str, Any],
    produtos: list[Produto],
    sitemap: list[str],
    regex: re.Pattern[str],
) -> Achados:
    colecao = [p for p in produtos if regex.search(p.campos_de_busca())]
    a = Achados(colecao=colecao)
    anteriores: dict[str, dict[str, Any]] = estado.get("produtos", {})

    if not estado:
        # Primeira execucao: nada e "novo", mas se ja houver item a venda,
        # vale avisar — e exatamente o que o monitor espera acontecer.
        a.liberados = [p for p in colecao if p.disponivel]
        return a

    for p in colecao:
        antes = anteriores.get(p.id)
        if antes is None:
            a.colecao_nova.append(p)
        elif p.disponivel and not antes.get("estoque"):
            a.liberados.append(p)
        elif not p.disponivel and antes.get("estoque"):
            a.esgotados.append(p)

    ids_colecao = {p.id for p in colecao}
    a.pokemon_novos = [
        p for p in produtos if p.e_pokemon and p.id not in anteriores and p.id not in ids_colecao
    ]
    visiveis = {p.slug for p in produtos}
    antes_sitemap = set(estado.get("sitemap", sitemap))
    a.slugs_novos = [
        s for s in sitemap
        if s not in antes_sitemap and s not in visiveis
        and ("pokemon" in s or regex.search(s.replace("-", " ")))
    ]
    return a


# ----------------------------------------------------------------- relatorio
def montar_relatorio(a: Achados) -> tuple[str, str]:
    linhas: list[str] = []

    if a.liberados:
        titulo = "Pokemon 30 anos: DISPONIVEL na Copag B2B!"
        linhas.append("Entraram em estoque:")
        linhas.extend(p.linha() for p in a.liberados)
    elif a.colecao_nova:
        titulo = "Pokemon 30 anos: item novo na Copag B2B"
    elif a.houve_novidade:
        titulo = "Copag B2B: mudancas no Pokemon (30 anos ainda sem estoque)"
    else:
        titulo = "Copag B2B: colecao de 30 anos ainda sem estoque"

    if a.colecao_nova:
        linhas.append("\nItens de 30 anos novos no catalogo:")
        linhas.extend(p.linha() for p in a.colecao_nova)
    if a.esgotados:
        linhas.append("\nEsgotaram desde a ultima verificacao:")
        linhas.extend(p.linha() for p in a.esgotados)
    if a.pokemon_novos:
        linhas.append("\nOutros produtos Pokemon novos:")
        linhas.extend(p.linha() for p in a.pokemon_novos)
    if a.slugs_novos:
        linhas.append("\nPaginas novas no sitemap, ainda fora da busca:")
        linhas.extend(f"- {SITE}/{s}/p" for s in a.slugs_novos)

    disponiveis = sum(p.disponivel for p in a.colecao)
    linhas.append(
        f"\nSituacao da colecao ({disponiveis} de {len(a.colecao)} disponiveis):"
    )
    linhas.extend(p.linha() for p in sorted(a.colecao, key=lambda p: (not p.disponivel, p.nome)))
    linhas.append(
        "\nPreco publico da loja; o preco B2B de quem esta logado pode ser outro."
    )
    return titulo, "\n".join(linhas).lstrip("\n")


# ----------------------------------------------------------------------- CLI
def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor_copag",
        description="Avisa quando a colecao de 30 anos de Pokemon entrar em estoque na Copag B2B.",
    )
    parser.add_argument(
        "--termo", action="append", default=[], help="padrao extra de busca, em regex (pode repetir)"
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
        "--sempre-notificar", action="store_true", help="notificar tambem quando nao houver novidade"
    )
    parser.add_argument("--estado", type=Path, default=ESTADO_PADRAO, help="arquivo de estado")
    parser.add_argument(
        "--sem-estado", action="store_true", help="nao gravar estado (util para testes manuais)"
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
        try:
            produtos = baixar_catalogo(sessao)
        except (requests.RequestException, ValueError) as erro:
            log.error("falha ao consultar o site: %s", erro)
            return 1
        if not produtos:
            # Busca vazia e quase certamente falha do site, nao catalogo vazio:
            # gravar isso faria a proxima execucao ver tudo como novo.
            log.error("a busca devolveu 0 produtos; nada gravado")
            return 1

        sitemap = baixar_sitemap(sessao)
        regex = compilar(list(PADROES) + args.termo)
        achados = analisar(carregar_estado(args.estado), produtos, sitemap, regex)
        if not achados.colecao:
            log.warning("nenhum item de 30 anos no catalogo — a categoria pode ter mudado de nome")

        titulo, corpo = montar_relatorio(achados)
        print(titulo)
        print()
        print(corpo)

        if achados.houve_novidade or args.sempre_notificar:
            if args.webhook:
                notificar(sessao, args.webhook, titulo, corpo)
            if args.email:
                enviar_email(
                    [d.strip() for d in args.email], titulo, corpo,
                    assinatura="monitor_copag", site=SITE,
                )

    if not args.sem_estado:
        salvar_estado(args.estado, produtos, sitemap)
        log.info("estado gravado em %s", args.estado)

    return 10 if achados.houve_novidade else 0


if __name__ == "__main__":
    sys.exit(main())
