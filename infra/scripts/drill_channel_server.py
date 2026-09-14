# -*- coding: utf-8 -*-
"""HTTPS-сервер канала обновлений для pilot drill (Phase 14, CI-контур).

Обслуживает статические файлы из каталога ``/srv`` поверх TLS с сертификатом,
выданным эфемерным drill-CA (сертификаты НИКОГДА не являются production и
живут только внутри прогона drill). Поддерживает один динамический маршрут:

* ``/redirect/<path>`` — отвечает 302 на URL из файла
  ``/srv/redirect-target.txt``. Нужен для проверки, что клиент канала
  отклоняет redirect на запрещённый хост ДО сетевого обращения (политика
  каждого hop); query-строки в package_url запрещены контрактом канала.

Никаких секретов, PII и production-данных: содержимое — подписанные fixture
артефакты, сгенерированные самим drill.

Запуск (в контейнере python:3.12-slim, см. infra/compose.drill.yml):

    python drill_channel_server.py --port 8443 \
        --cert /certs/channel.crt --key /certs/channel.key --root /srv
"""

from __future__ import annotations

import argparse
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def build_handler(root: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "hrm-drill-channel/1.0"

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            # Минимальный лог: метод/путь/код — без тел, заголовков и токенов.
            print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

        def _serve_redirect(self) -> None:
            try:
                target = (root / "redirect-target.txt").read_text(encoding="utf-8").strip()
            except OSError:
                target = ""
            if not target:
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/redirect" or path.startswith("/redirect/"):
                self._serve_redirect()
                return
            candidate = (root / path.lstrip("/")).resolve()
            if not str(candidate).startswith(str(root.resolve())):
                self.send_response(404)
                self.end_headers()
                return
            if candidate.is_dir():
                candidate = candidate / "update-channel.json"
            if not candidate.is_file():
                self.send_response(404)
                self.end_headers()
                return
            data = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--root", default="/srv")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), build_handler(Path(args.root)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"drill channel serving {args.root} on :{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
