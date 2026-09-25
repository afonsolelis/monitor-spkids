#!/usr/bin/env python3
"""Historico de preco das cartas da colecao Pokemon 30 anos.

A cada execucao le o preco de todas as cartas das duas edicoes na API da
PokeWallet e acrescenta uma linha por carta no CSV. Depois gera um HTML (sem
servidor, abre direto do disco) com a evolucao de cada carta, a tendencia por
regressao linear e uma caixa para marcar as cartas que voce ja tem.

Fonte: a MYP Cards e a LigaPokemon seriam as primeiras escolhas, por serem
lojas brasileiras, mas as duas passaram a responder o desafio anti-robo do
Cloudflare a qualquer acesso automatizado; contornar isso seria burlar a
protecao do site. A pokemontcg.io, a seguinte, parou de publicar precos (e
sai do ar em marco de 2027). A PokeWallet tem API documentada, plano gratis
(1.000 pedidos por dia, chave em POKEWALLET_KEY) e os precos do TCGplayer; a
busca devolve 100 cartas com preco por pedido, entao a coleta toda sao ~5.

Os precos do TCGplayer sao em dolar. Para o historico continuar comparavel com
o que ja foi coletado em real, cada coleta converte pela cotacao do dia
(AwesomeAPI) e grava em real. E preco de mercado americano convertido, nao o
que se paga numa loja brasileira — serve para a tendencia, nao para o bolso.

Codigo de saida 0 quando coletou, 1 em caso de erro.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

import requests

from monitor_spkids import criar_sessao

log = logging.getLogger("precos")

API = "https://api.pokewallet.io/search"
# A listagem da edicao (/sets/:id) vem sem preco no plano gratis; a busca vem
# com. O termo pega tambem a edicao japonesa e produtos lacrados, que o filtro
# por set_id e product_type descarta.
BUSCA = "30th Celebration"
POR_PAGINA = 100
IMAGEM = "https://tcgplayer-cdn.tcgplayer.com/product/{}_400w.jpg"
COTACAO = "https://economia.awesomeapi.com.br/last/USD-BRL"
# A AwesomeAPI responde 429 aos IPs compartilhados do GitHub Actions; esta e a
# reserva (atualiza uma vez por dia, o que basta para a tendencia).
COTACAO_RESERVA = "https://open.er-api.com/v6/latest/USD"
REPO = Path(__file__).resolve().parent
CSV_PADRAO = REPO / "dados" / "precos-cartas.csv"
HTML_PADRAO = REPO / "dados" / "precos-cartas.html"
META_PADRAO = REPO / "dados" / "precos-cartas-meta.json"
MODELO = REPO / "painel_precos.html"

# (sigla, set_id na PokeWallet, nome para exibir). A sigla e a mesma que a
# LigaPokemon usava, para as linhas ja gravadas no CSV continuarem casando com
# as novas.
EDICOES = (
    ("30C", "24722", "Celebração de 30 Anos"),
    ("30C-C", "24837", "Cartas Clássicas"),
)

# A Classic Collection reimprime cartas antigas com o numero original, e tres
# pares se repetem. A Liga separava com letra; a PokeWallet traz o numero
# completo (numero/total da edicao original), que resolve.
NUMEROS_REPETIDOS = {
    ("30C-C", "11/101"): "011b",   # Genesect-EX (Metagross e 11/113)
    ("30C-C", "106/160"): "106b",  # M Gardevoir-EX (Palkia LV.X e 106/106)
    ("30C-C", "106/105"): "106c",  # Shining Celebi
}

# A variante que vale na hora de escolher o preco: misturar daria uma media
# sem sentido, porque a holografica custa varias vezes a normal.
VARIANTES = ("Holofoil", "Reverse Holofoil", "Normal")

# A API cai com 500/502 de vez em quando e volta sozinha na tentativa seguinte.
TENTATIVAS = 4
ESPERA = 3.0

COLUNAS = (
    "coletado_em", "colecao", "numero", "nome_en", "nome_pt",
    "preco_min", "preco_medio", "preco_max",
)

# O HTML carrega o historico inteiro embutido. Hora a hora, 188 cartas dao
# ~4.500 pontos por dia; alem desta janela, cada carta fica com um ponto por dia.
JANELA_HORARIA = timedelta(days=14)


# ------------------------------------------------------------------- coleta
def _preco(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None  # 0.00 = sem oferta


def _insistir(sessao: requests.Session, url: str, **kwargs: Any) -> dict[str, Any]:
    """GET que tolera os 500/502 esporadicos da API."""
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resposta = sessao.get(url, timeout=kwargs.pop("timeout", 30.0), **kwargs)
            resposta.raise_for_status()
            return resposta.json()
        except (requests.RequestException, ValueError) as erro:
            if tentativa == TENTATIVAS:
                raise
            log.debug("tentativa %d de %d falhou (%s)", tentativa, TENTATIVAS, erro)
            time.sleep(ESPERA * tentativa)
    raise AssertionError("inalcancavel")


def cotacao_dolar(sessao: requests.Session) -> float:
    """Quanto vale um dolar em reais agora."""
    try:
        # Uma tentativa so: se falhar, a reserva resolve mais rapido que insistir.
        resposta = sessao.get(COTACAO, timeout=15)
        resposta.raise_for_status()
        valor = _preco(resposta.json().get("USDBRL", {}).get("bid"))
    except (requests.RequestException, ValueError) as erro:
        log.warning("AwesomeAPI falhou (%s); usando a cotacao reserva", erro)
        valor = None
    if valor is None:
        valor = _preco(_insistir(sessao, COTACAO_RESERVA).get("rates", {}).get("BRL"))
    if valor is None:
        raise ValueError("cotacao do dolar veio vazia")
    log.info("dolar a R$ %.2f", valor)
    return valor


def _numero(sigla: str, completo: str) -> str:
    """'58/102' -> '058': o CSV antigo guarda o numero com tres digitos."""
    if (sigla, completo) in NUMEROS_REPETIDOS:
        return NUMEROS_REPETIDOS[(sigla, completo)]
    bruto = completo.split("/")[0]
    return bruto.zfill(3) if bruto.isdigit() else bruto


def _precos_da_carta(carta: dict[str, Any], dolar: float) -> dict[str, float | None]:
    """Menor, medio e maior preco da carta no TCGplayer, em reais."""
    precos = (carta.get("tcgplayer") or {}).get("prices") or []
    por_variante = {p.get("sub_type_name"): p for p in precos if isinstance(p, dict)}
    escolhida = next(
        (por_variante[v] for v in VARIANTES if v in por_variante),
        precos[0] if precos else {},
    )

    def reais(campo: str) -> float | None:
        valor = _preco(escolhida.get(campo))
        return round(valor * dolar, 2) if valor is not None else None

    return {
        "preco_min": reais("low_price"),
        "preco_medio": reais("market_price") or reais("mid_price"),
        "preco_max": reais("high_price"),
    }


def buscar_cartas(sessao: requests.Session, chave: str) -> list[dict[str, Any]]:
    """Todas as paginas da busca, com preco."""
    resultados: list[dict[str, Any]] = []
    pagina, paginas = 1, 1
    while pagina <= paginas:
        dados = _insistir(
            sessao,
            API,
            params={"q": BUSCA, "limit": POR_PAGINA, "page": pagina},
            headers={"X-API-Key": chave, "Accept": "application/json"},
            timeout=60.0,
        )
        resultados += dados.get("results") or []
        paginas = (dados.get("pagination") or {}).get("total_pages") or 1
        pagina += 1
    return resultados


def cartas_da_edicao(
    resultados: list[dict[str, Any]], sigla: str, set_id: str, dolar: float
) -> list[dict[str, Any]]:
    cartas: dict[str, dict[str, Any]] = {}
    for c in resultados:
        info = c.get("card_info") or {}
        if str(info.get("set_id")) != set_id or info.get("product_type") != "card":
            continue
        if not info.get("card_number"):  # "Code Card" do booster, nao e carta
            continue
        numero = _numero(sigla, str(info.get("card_number", "")))
        url = (c.get("tcgplayer") or {}).get("url", "")
        produto = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        cartas[numero] = {
            "colecao": sigla,
            "numero": numero,
            # Algumas vem com o numero no nome ("Mew ex - 066/128").
            "nome_en": re.sub(r"\s+-\s+\S+/\S+$", "", info.get("name", "")),
            # A API so tem o nome em ingles; o nome em portugues vem do que
            # a Liga ja gravou no CSV, quando gravou.
            "nome_pt": "",
            **_precos_da_carta(c, dolar),
            "imagem": IMAGEM.format(produto) if produto.isdigit() else "",
            "url": url,
        }
    lista = sorted(cartas.values(), key=lambda c: c["numero"])
    com_preco = sum(1 for c in lista if c["preco_medio"] is not None)
    log.info("%s: %d cartas, %d com preco", sigla, len(lista), com_preco)
    return lista


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


def nomes_pt(caminho: Path) -> dict[str, str]:
    """Nome em portugues por carta, herdado das coletas antigas da Liga.

    A PokeWallet so tem o nome em ingles, e o painel mostra os dois.
    """
    nomes: dict[str, str] = {}
    with caminho.open(newline="", encoding="utf-8") as arquivo:
        for linha in csv.DictReader(arquivo):
            if linha.get("nome_pt"):
                nomes[f"{linha['colecao']}/{linha['numero']}"] = linha["nome_pt"]
    return nomes


def gravar_meta(caminho: Path, cartas: list[dict[str, Any]]) -> None:
    """Nome, imagem e link de cada carta, para o --so-html nao precisar da rede."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(
            [
                {k: c[k] for k in ("colecao", "numero", "nome_en", "nome_pt", "imagem", "url")}
                for c in cartas
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def ler_meta(caminho: Path) -> list[dict[str, Any]]:
    cartas = json.loads(caminho.read_text(encoding="utf-8"))
    for c in cartas:  # sem coleta nao ha preco de agora; so o historico do CSV
        c.update(preco_min=None, preco_medio=None, preco_max=None)
    return cartas


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
        description="Grava o preco das cartas Pokemon 30 anos (PokeWallet) e gera o painel HTML.",
    )
    parser.add_argument("--csv", type=Path, default=CSV_PADRAO, help="arquivo do historico")
    parser.add_argument("--html", type=Path, default=HTML_PADRAO, help="painel gerado")
    parser.add_argument("--meta", type=Path, default=META_PADRAO, help="cache das cartas")
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

    if args.so_html:
        if not args.meta.exists():
            log.error("%s nao existe; rode uma coleta antes do --so-html", args.meta)
            return 1
        cartas = ler_meta(args.meta)
    else:
        chave = os.environ.get("POKEWALLET_KEY", "")
        if not chave:
            log.error("defina POKEWALLET_KEY no ambiente (chave gratis em pokewallet.io)")
            return 1
        with criar_sessao() as sessao:
            try:
                dolar = cotacao_dolar(sessao)
                resultados = buscar_cartas(sessao, chave)
            except (requests.RequestException, ValueError) as erro:
                log.error("falha ao consultar a PokeWallet: %s", erro)
                return 1
        for sigla, set_id, _ in EDICOES:
            da_edicao = cartas_da_edicao(resultados, sigla, set_id, dolar)
            if not da_edicao:
                # A busca mudou de formato ou de nome: gravar so metade das
                # cartas deixaria buracos no historico sem ninguem notar.
                log.error("%s: nenhuma carta na busca; nada gravado", sigla)
                return 1
            cartas += da_edicao

    if not cartas:
        log.error("nenhuma carta encontrada; nada gravado")
        return 1

    if args.csv.exists():
        herdados = nomes_pt(args.csv)
        for c in cartas:
            c["nome_pt"] = c["nome_pt"] or herdados.get(f"{c['colecao']}/{c['numero']}", "")

    com_preco = sum(1 for c in cartas if c["preco_medio"] is not None)
    if not args.so_html:
        gravar_meta(args.meta, cartas)
        # Sem preco nenhum nao ha o que guardar, e uma coleta vazia so sujaria
        # o historico.
        if com_preco:
            gravar_csv(args.csv, agora.isoformat(), cartas)
            log.info("%d precos gravados em %s", com_preco, args.csv)
        else:
            log.warning("nenhuma carta com preco publicado ainda; CSV intacto")

    series = ler_csv(args.csv) if args.csv.exists() else {}
    gerar_html(args.html, cartas, series, agora)
    log.info("painel em %s", args.html)
    print(f"{len(cartas)} cartas, {com_preco} com preco; painel: file://{args.html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
