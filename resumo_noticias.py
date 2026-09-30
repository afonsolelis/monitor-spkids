#!/usr/bin/env python3
"""Resumo diario das noticias por e-mail.

Le as noticias do Supabase pela mesma chamada do site (rpc painel_noticias,
chave publicavel, so leitura), separa as das ultimas N horas por tema e manda
um e-mail so. Roda no GitHub Actions (.github/workflows/resumo.yml); as
credenciais SMTP vem do ambiente, nunca da linha de comando.

    python resumo_noticias.py --sem-enviar      # so mostra o texto
    python resumo_noticias.py --horas 48        # janela maior
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
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

SUPABASE_URL = "https://lwamaovuxcevsjfvtqhf.supabase.co"
# Publicavel de proposito: a mesma do site, so le.
SUPABASE_CHAVE = "sb_publishable_j0O_u0t7-lDCtBbmqaIz3A_8vAIGcyJ"
SITE = "https://afonsolelis.github.io/monitor-spkids/"
FUSO = ZoneInfo("America/Sao_Paulo")

TEMAS = {
    "pokemon-tcg": "Pokémon TCG",
    "pokemon": "Pokémon",
    "tecnologia": "Tecnologia",
    "ia": "IA",
    "games": "Games",
    "ciencia": "Ciência",
}

log = logging.getLogger("resumo_noticias")


def baixar(dias: int) -> dict:
    # URL fixa em https (constante acima), nunca vinda de fora: o S310 nao se aplica.
    pedido = urllib.request.Request(  # noqa: S310
        f"{SUPABASE_URL}/rest/v1/rpc/painel_noticias",
        data=json.dumps({"dias": dias}).encode(),
        headers={"apikey": SUPABASE_CHAVE, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(pedido, timeout=60) as resposta:  # noqa: S310
        return json.load(resposta)


def separar(dados: dict, horas: int, por_tema: int) -> dict[str, list[dict]]:
    """Noticias das ultimas `horas`, por tema, mais nova primeiro."""
    fontes = {f["id"]: f["nome"] for f in dados["fontes"]}
    corte = datetime.now(timezone.utc) - timedelta(hours=horas)
    grupos: dict[str, list[dict]] = {tema: [] for tema in TEMAS}
    for n in dados["noticias"]:
        quando = datetime.fromisoformat(n["p"])
        if quando < corte or n["tm"] not in grupos:
            continue
        titulo, site = n["t"], fontes.get(n["f"], n["f"])
        # Google Noticias: "Titulo - Site"; o site vira a fonte, como no site.
        m = re.match(r"^(.*\S)\s+-\s+([^-]+)$", titulo) if n["f"].startswith("gn-") else None
        if m:
            titulo, site = m.group(1), m.group(2).strip()
        grupos[n["tm"]].append(
            {"titulo": titulo, "link": n["l"], "resumo": n["r"], "site": site, "quando": quando.astimezone(FUSO)}
        )
    for tema, lista in grupos.items():
        lista.sort(key=lambda x: x["quando"], reverse=True)
        grupos[tema] = lista[:por_tema]
    return {tema: lista for tema, lista in grupos.items() if lista}


def montar(grupos: dict[str, list[dict]], total: int) -> tuple[str, str, str]:
    """Assunto, texto puro e HTML."""
    hoje = datetime.now(FUSO).strftime("%d/%m")
    assunto = f"Notícias de {hoje} · {total} destaques"

    texto: list[str] = []
    partes: list[str] = []
    for tema, lista in grupos.items():
        nome = TEMAS[tema]
        texto.append(f"== {nome} ==")
        partes.append(f'<h2 style="font:600 17px system-ui,sans-serif;margin:28px 0 8px">{html.escape(nome)}</h2>')
        for n in lista:
            meta = f"{n['site']} · {n['quando']:%H:%M}"
            texto.append(f"- {n['titulo']} ({meta})\n  {n['link']}")
            resumo = (
                f'<div style="color:#52514e;margin-top:2px">{html.escape(n["resumo"])}</div>' if n["resumo"] else ""
            )
            partes.append(
                '<div style="margin:0 0 14px">'
                f'<a href="{html.escape(n["link"])}" style="color:#0b0b0b;font-weight:600;text-decoration:none">'
                f"{html.escape(n['titulo'])}</a>"
                f'<div style="color:#898781;font-size:12px">{html.escape(meta)}</div>{resumo}</div>'
            )
        texto.append("")
    texto.append(f"Tudo no site: {SITE}")
    corpo_html = (
        '<html><body style="font:14px/1.45 system-ui,sans-serif;color:#0b0b0b;max-width:640px">'
        f'<h1 style="font:650 22px system-ui,sans-serif;margin:0">{html.escape(assunto)}</h1>'
        + "".join(partes)
        + f'<p style="font-size:12px;color:#898781;margin-top:32px">Tudo no site: <a href="{SITE}">{SITE}</a></p>'
        "</body></html>"
    )
    return assunto, "\n".join(texto), corpo_html


def enviar_email(destinos: list[str], assunto: str, texto: str, corpo_html: str) -> bool:
    """Envia por SMTP. Credenciais do ambiente (SMTP_*); no Gmail, Senha de App."""
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    porta = int(os.environ.get("SMTP_PORTA", "587"))
    usuario = os.environ.get("SMTP_USUARIO", "")
    senha = os.environ.get("SMTP_SENHA", "")
    remetente = os.environ.get("SMTP_DE") or usuario
    if not remetente:
        log.error("defina SMTP_USUARIO (ou SMTP_DE) para enviar e-mail")
        return False

    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = remetente
    mensagem["To"] = ", ".join(destinos)
    mensagem.set_content(texto)
    mensagem.add_alternative(corpo_html, subtype="html")

    try:
        conexao = (
            smtplib.SMTP_SSL(host, porta, timeout=30) if porta == 465 else smtplib.SMTP(host, porta, timeout=30)
        )
        with conexao as servidor:
            if porta != 465:
                try:
                    servidor.starttls()
                    servidor.ehlo()
                except smtplib.SMTPNotSupportedError:
                    if senha:
                        log.error("%s:%s nao oferece STARTTLS; recusando enviar a senha em texto claro", host, porta)
                        return False
                    log.warning("conexao sem TLS com %s:%s", host, porta)
            if usuario and senha:
                servidor.login(usuario, senha)
            servidor.send_message(mensagem)
    except smtplib.SMTPAuthenticationError as erro:
        log.error("SMTP recusou as credenciais (%s). No Gmail use uma Senha de App.", erro.smtp_code)
        return False
    except (smtplib.SMTPException, OSError) as erro:
        log.error("falha ao enviar e-mail via %s:%s: %s", host, porta, erro)
        return False

    log.info("e-mail enviado para %s", ", ".join(destinos))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--horas", type=int, default=24, help="janela de noticias (padrao 24)")
    parser.add_argument("--por-tema", type=int, default=10, help="maximo de noticias por tema (padrao 10)")
    parser.add_argument("--sem-enviar", action="store_true", help="so mostra o texto, nao manda e-mail")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        dados = baixar(dias=max(1, -(-args.horas // 24)))
    except OSError as erro:
        log.error("nao consegui ler as noticias do Supabase: %s", erro)
        return 1

    grupos = separar(dados, args.horas, args.por_tema)
    total = sum(len(lista) for lista in grupos.values())
    if not total:
        # Um dia inteiro sem nada quer dizer coleta parada: falhar avisa.
        log.error("nenhuma noticia nas ultimas %s horas; a coleta no Supabase parou?", args.horas)
        return 1

    assunto, texto, corpo_html = montar(grupos, total)
    if args.sem_enviar:
        print(assunto, texto, sep="\n\n")
        return 0

    destinos = [d.strip() for d in os.environ.get("RESUMO_PARA", "").split(",") if d.strip()]
    if not destinos:
        log.error("defina RESUMO_PARA com o e-mail de destino")
        return 1
    return 0 if enviar_email(destinos, assunto, texto, corpo_html) else 1


if __name__ == "__main__":
    sys.exit(main())
