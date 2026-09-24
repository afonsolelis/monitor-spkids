#!/usr/bin/env python3
"""Historico de preco das cartas da colecao Pokemon 30 anos.

A cada execucao le o preco de todas as cartas das duas edicoes na LigaPokemon
e acrescenta uma linha por carta no CSV. Depois gera um HTML (sem servidor,
abre direto do disco) com a evolucao de cada carta, a tendencia por regressao
linear e uma caixa para marcar as cartas que voce ja tem.

Fonte: a MYP Cards seria a primeira escolha, mas bloqueia acesso automatizado
com o desafio anti-robo do Cloudflare. A LigaPokemon tem as mesmas duas
edicoes, em reais, e entrega os dados sem desafio: a pagina da edicao traz
embutido um JSON (`var cardsjson = [...]`) com preco minimo, medio e maximo de
cada carta — nao e preciso raspar o HTML.

Codigo de saida 0 quando coletou, 1 em caso de erro.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import quote

import requests

from monitor_spkids import _texto, criar_sessao

log = logging.getLogger("precos")

SITE = "https://www.ligapokemon.com.br"
IMAGENS = "https://repositorio.sbrauble.com"
REPO = Path(__file__).resolve().parent
CSV_PADRAO = REPO / "dados" / "precos-cartas.csv"
HTML_PADRAO = REPO / "dados" / "precos-cartas.html"
MODELO = REPO / "painel_precos.html"

# (sigla, id da edicao na Liga, nome para exibir). A Liga agrupa a colecao
# classica e as cartas RGB sob a edicao 804; as RGB (3 Mew promocionais, hoje
# sem nenhuma oferta) ficam de fora.
EDICOES = (
    ("30C", 804, "Celebração de 30 Anos"),
    ("30C-C", 805, "Cartas Clássicas"),
)

COLUNAS = (
    "coletado_em", "colecao", "numero", "nome_en", "nome_pt",
    "preco_min", "preco_medio", "preco_max",
)

# O HTML carrega o historico inteiro embutido. Hora a hora, 188 cartas dao
# ~4.500 pontos por dia; alem desta janela, cada carta fica com um ponto por dia.
JANELA_HORARIA = timedelta(days=14)


# ------------------------------------------------------------------- coleta
def _cardsjson(pagina: str) -> list[dict[str, Any]]:
    marca = re.search(r"var\s+cardsjson\s*=\s*", pagina)
    if not marca:
        raise ValueError("cardsjson nao encontrado — o layout da Liga mudou")
    cartas, _ = json.JSONDecoder().raw_decode(pagina, marca.end())
    return cartas


def _preco(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None  # 0.00 = sem oferta


def baixar_edicao(
    sessao: requests.Session, sigla: str, edid: int, timeout: float = 30.0
) -> list[dict[str, Any]]:
    resposta = sessao.get(
        f"{SITE}/",
        params={"view": "cards/search", "card": f"edid={edid} ed={sigla}"},
        headers={"Accept": "text/html"},
        timeout=timeout,
    )
    resposta.raise_for_status()
    cartas = []
    for c in _cardsjson(resposta.text):
        if c.get("sSigla") != sigla:
            continue
        nome_en = _texto(c.get("nEN", ""))
        cartas.append(
            {
                "colecao": sigla,
                "numero": c.get("sN", ""),
                "nome_en": nome_en,
                "nome_pt": _texto(c.get("nPT", "")),
                "preco_min": _preco(c.get("p1a")),
                "preco_medio": _preco(c.get("p1b")),
                "preco_max": _preco(c.get("p1c")),
                "imagem": f"{IMAGENS}/{c['sP'].lstrip('/')}" if c.get("sP") else "",
                "url": f"{SITE}/?view=cards/card&card={quote(nome_en)}"
                f"&ed={quote(sigla)}&num={quote(c.get('sN', ''))}",
            }
        )
    log.info("%s: %d cartas", sigla, len(cartas))
    return cartas


# ---------------------------------------------------------------------- CSV
def gravar_csv(caminho: Path, momento: str, cartas: list[dict[str, Any]]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    novo = not caminho.exists() or caminho.stat().st_size == 0
    with caminho.open("a", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        if novo:
            escritor.writerow(COLUNAS)
        for c in cartas:
            escritor.writerow(
                [momento] + [
                    "" if c[col] is None else c[col] for col in COLUNAS[1:]
                ]
            )


def ler_csv(caminho: Path) -> dict[str, list[tuple[datetime, float | None, float | None]]]:
    """Serie por carta: (momento, preco medio, preco minimo)."""
    series: dict[str, list[tuple[datetime, float | None, float | None]]] = defaultdict(list)
    with caminho.open(newline="", encoding="utf-8") as arquivo:
        for linha in csv.DictReader(arquivo):
            chave = f"{linha['colecao']}/{linha['numero']}"
            series[chave].append(
                (
                    datetime.fromisoformat(linha["coletado_em"]),
                    _preco(linha["preco_medio"]),
                    _preco(linha["preco_min"]),
                )
            )
    # Depois de um merge entre maquinas as linhas podem vir fora de ordem.
    for pontos in series.values():
        pontos.sort(key=lambda p: p[0])
    return series


# --------------------------------------------------------------------- HTML
def _compactar(
    pontos: list[tuple[datetime, float | None, float | None]], agora: datetime
) -> list[list[Any]]:
    """Pontos recentes hora a hora; os antigos viram a media do dia."""
    corte = agora - JANELA_HORARIA
    antigos: dict[str, list[tuple[datetime, float | None, float | None]]] = defaultdict(list)
    saida: list[list[Any]] = []
    for ponto in pontos:
        if ponto[0] < corte:
            antigos[ponto[0].date().isoformat()].append(ponto)
        else:
            saida.append([int(ponto[0].timestamp()), ponto[1], ponto[2]])

    def media(valores: list[float | None]) -> float | None:
        validos = [v for v in valores if v is not None]
        return round(fmean(validos), 2) if validos else None

    diarios = [
        [
            int(fmean(p[0].timestamp() for p in grupo)),
            media([p[1] for p in grupo]),
            media([p[2] for p in grupo]),
        ]
        for grupo in antigos.values()
    ]
    return sorted(diarios + saida)


def gerar_html(
    destino: Path, cartas: list[dict[str, Any]], series: dict[str, list], agora: datetime
) -> None:
    nomes_edicao = {sigla: nome for sigla, _, nome in EDICOES}
    dados = {
        "gerado_em": agora.isoformat(timespec="seconds"),
        "coletas": len({p[0] for s in series.values() for p in s}),
        "edicoes": nomes_edicao,
        "cartas": [
            {
                "k": f"{c['colecao']}/{c['numero']}",
                "col": c["colecao"],
                "num": c["numero"],
                "en": c["nome_en"],
                "pt": c["nome_pt"],
                "img": c["imagem"],
                "url": c["url"],
                "s": _compactar(series.get(f"{c['colecao']}/{c['numero']}", []), agora),
            }
            for c in cartas
        ],
    }
    # "</" dentro do JSON fecharia a tag <script> antes da hora.
    embutido = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    pagina = MODELO.read_text(encoding="utf-8").replace("/*__DADOS__*/null", embutido, 1)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".tmp")
    temporario.write_text(pagina, encoding="utf-8")
    temporario.replace(destino)  # quem estiver com a pagina aberta nunca le meio arquivo


# ----------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="precos_cartas",
        description="Grava o preco das cartas Pokemon 30 anos (LigaPokemon) e gera o painel HTML.",
    )
    parser.add_argument("--csv", type=Path, default=CSV_PADRAO, help="arquivo do historico")
    parser.add_argument("--html", type=Path, default=HTML_PADRAO, help="painel gerado")
    parser.add_argument(
        "--so-html", action="store_true", help="nao coletar; so regerar o HTML a partir do CSV"
    )
    parser.add_argument("-v", "--verboso", action="store_true", help="log em nivel DEBUG")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verboso else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    agora = datetime.now().astimezone().replace(microsecond=0)
    cartas: list[dict[str, Any]] = []
    with criar_sessao() as sessao:
        try:
            for sigla, edid, _ in EDICOES:
                cartas += baixar_edicao(sessao, sigla, edid)
        except (requests.RequestException, ValueError) as erro:
            log.error("falha ao consultar a LigaPokemon: %s", erro)
            return 1

    if not cartas:
        log.error("nenhuma carta encontrada; nada gravado")
        return 1
    if not args.so_html:
        gravar_csv(args.csv, agora.isoformat(), cartas)
        log.info("%d precos gravados em %s", len(cartas), args.csv)

    series = ler_csv(args.csv) if args.csv.exists() else {}
    gerar_html(args.html, cartas, series, agora)
    log.info("painel em %s", args.html)
    print(f"{len(cartas)} cartas; painel: file://{args.html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
