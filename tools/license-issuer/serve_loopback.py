# -*- coding: utf-8 -*-
"""Loopback-only static server for license-issuer.html (used by run-html.bat).

Replaces ``python -m http.server``: the stock server calls
``socket.getfqdn(host)`` in ``server_bind()``, i.e. a name-service lookup on
every start even when bound to 127.0.0.1 (seen in the Windows acceptance run as
DNS-Client events of the bundled python.exe). The issuer must not touch the
network at all, so this server:

- binds to 127.0.0.1 only (hard-coded, there is no bind-address option);
- performs no name resolution (no getfqdn in server_bind, no reverse DNS in logs);
- serves only the bundle directory given on the command line, no directory
  listings;
- sends ``Cache-Control: no-store`` so the browser keeps no cached copy of the page.

Usage: python serve_loopback.py <port> <directory>
"""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
import sys

HOST = "127.0.0.1"


class LoopbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # socketserver.TCPServer.server_bind only binds; http.server.HTTPServer
        # would additionally call socket.getfqdn(host) -> DNS. Skip that.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class Handler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):  # no directory listings
        self.send_error(404, "Not found")
        return None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def address_string(self) -> str:  # never reverse-resolve the client
        return str(self.client_address[0])


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[0].isdigit():
        print("usage: serve_loopback.py <port> <directory>", file=sys.stderr)
        return 2
    port = int(argv[0])
    directory = os.path.abspath(argv[1])
    if not os.path.isfile(os.path.join(directory, "license-issuer.html")):
        print("[server] license-issuer.html not found in " + directory, file=sys.stderr)
        return 2
    handler = functools.partial(Handler, directory=directory)
    try:
        httpd = LoopbackServer((HOST, port), handler)
    except OSError as exc:
        print("[server] cannot listen on {}:{} ({}). Is another run-html.bat already open?".format(HOST, port, exc), file=sys.stderr)
        return 1
    with httpd:
        print("Serving HTTP on {} port {} (http://{}:{}/) ...".format(HOST, port, HOST, port), flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nKeyboard interrupt received, exiting.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
