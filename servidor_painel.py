#!/usr/bin/env python3
"""Serve o painel de precos em http://127.0.0.1:8787 com um botao de atualizar.

Aberto direto do disco (file://), o painel nao consegue rodar nada na maquina.
Este servidor entrega o mesmo HTML e aceita um POST em /atualizar, que roda a
coleta pelo rodar_monitor.sh — o mesmo caminho do cron, com a mesma trava, de
modo que o botao e o cron nunca coletam ao mesmo tempo.

So escuta em 127.0.0.1. Mesmo assim, qualquer site aberto no navegador poderia
tentar um POST para ca; por isso o /atualizar exige um cabecalho proprio (que
forca a checagem de CORS, que este servidor nunca libera) e confere o Host
(contra DNS rebinding).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent
PAINEL = REPO / "dados" / "precos-cartas.html"
RODAR = REPO / "rodar_monitor.sh"
LOG = Path.home() / ".local" / "state" / "spkids" / "precos.log"
CABECALHO = "X-Painel-Precos"


class Painel(BaseHTTPRequestHandler):
    server_version = "painel-precos"
    hosts: set[str] = set()

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.hosts

    def _responder(self, status: int, corpo: bytes, tipo: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, status: int, dados: dict) -> None:
        self._responder(status, json.dumps(dados, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"erro": "host nao permitido"})
        if self.path.split("?")[0] not in ("/", "/index.html"):
            return self._json(HTTPStatus.NOT_FOUND, {"erro": "nao encontrado"})
        if not PAINEL.exists():
            return self._responder(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Painel ainda nao gerado. Rode: ./rodar_monitor.sh precos".encode(),
                "text/plain; charset=utf-8",
            )
        self._responder(HTTPStatus.OK, PAINEL.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/atualizar":
            return self._json(HTTPStatus.NOT_FOUND, {"erro": "nao encontrado"})
        if not self._host_ok() or self.headers.get(CABECALHO) != "1":
            return self._json(HTTPStatus.FORBIDDEN, {"erro": "requisicao recusada"})
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as log:
            log.write("(pedido pelo botao do painel)\n")
            log.flush()
            try:
                resultado = subprocess.run(
                    [str(RODAR), "precos"], stdout=log, stderr=log, timeout=300, check=False
                )
            except subprocess.TimeoutExpired:
                return self._json(HTTPStatus.GATEWAY_TIMEOUT, {"erro": "a coleta passou de 5 minutos"})
        if resultado.returncode == 0:
            return self._json(HTTPStatus.OK, {"ok": True})
        # flock -n tambem sai com 1 quando o cron esta coletando neste instante.
        return self._json(
            HTTPStatus.BAD_GATEWAY,
            {"erro": f"a coleta falhou (codigo {resultado.returncode}); veja {LOG}"},
        )

    def log_message(self, formato: str, *args) -> None:  # silencioso: o systemd ja guarda o que importa
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve o painel de precos com botao de atualizar.")
    parser.add_argument("--porta", type=int, default=8787)
    args = parser.parse_args()
    Painel.hosts = {f"127.0.0.1:{args.porta}", f"localhost:{args.porta}"}
    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), Painel)
    print(f"painel em http://127.0.0.1:{args.porta}", flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
