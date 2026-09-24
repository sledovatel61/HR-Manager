# -*- coding: utf-8 -*-
"""Open the issuer page in the default browser only once the local server really serves it.

Started in the background by run-html.bat right before the loopback http.server.
The old launcher opened the browser after a fixed ~2 s delay; on a slow or
freshly unzipped machine (antivirus scanning python.exe on first start) the
browser could hit the port before http.server was listening and show
"connection refused" as the owner's first impression. This helper removes the
race: it polls the exact page URL on 127.0.0.1 and opens the browser only after
an HTTP 200 whose body contains the issuer page marker (so a different program
that happens to occupy the port is never mistaken for the issuer).

- loopback only: refuses any URL that is not http://127.0.0.1:<port>/...
- raw IPv4 socket to 127.0.0.1: no proxy code path at all and no name resolution
  (urllib/socket.create_connection would call getaddrinfo, which on Windows goes
  through the DNS Client service even for a numeric address)
- the browser is opened by `cmd /d /c start` (not ShellExecute in this process: no WPAD lookup
  under the bundled python.exe)
- HRM_NO_BROWSER=1: report readiness but do not open a browser (CI/checklists)
- prints one "[ready] ..." or "[browser] ..." line; never prints key material
- exit 0 = page ready (browser opened unless HRM_NO_BROWSER), 1 = not ready
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

MARKER = b"License Issuer"
TIMEOUT_S = 30.0
POLL_S = 0.25
MAX_BODY = 4 * 1024 * 1024


def fetch(port: int, path: str) -> tuple[int, bytes]:
    """HTTP/1.0 GET over a plain AF_INET socket to 127.0.0.1 (dotted quad: no resolver)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(("127.0.0.1", port))
        request = "GET {} HTTP/1.0\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n".format(path, port)
        sock.sendall(request.encode("ascii"))
        chunks = []
        total = 0
        while total <= MAX_BODY:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
            total += len(data)
    finally:
        sock.close()
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].split()
    if len(status_line) < 2 or not status_line[0].startswith(b"HTTP/") or not status_line[1].isdigit():
        raise ValueError("not an HTTP response")
    return int(status_line[1]), body


URL_RE = re.compile(r"^http://127\.0\.0\.1:[0-9]{1,5}/[A-Za-z0-9._/-]*$")


def open_browser(url: str) -> None:
    """Open the default browser from a separate cmd.exe, not via ShellExecute in this process.

    os.startfile(url) runs ShellExecute inside the bundled python.exe; for an http URL that
    loads shell/urlmon, which may perform WPAD proxy auto-discovery - the Windows acceptance
    run saw DNS queries for "wpad" attributed to the bundled python.exe. Handing the URL to
    `cmd /c start` keeps the issuer process itself free of any network activity (opening a
    browser is then the same OS action as double-clicking a link). URL_RE guarantees the
    URL has no characters cmd.exe would interpret.
    """
    system_root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    cmd = os.path.join(system_root, "System32", "cmd.exe")
    subprocess.Popen([cmd, "/d", "/c", "start", "", url], close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("[browser] usage: open_when_ready.py http://127.0.0.1:<port>/license-issuer.html")
        return 1
    url = argv[1]
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not port or not URL_RE.match(url):
        print(f"[browser] refusing URL (only http://127.0.0.1:<port>/<path of A-Z a-z 0-9 . _ / ->): {url}")
        return 1
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")

    start = time.monotonic()
    attempts = 0
    last = "no attempt"
    while time.monotonic() - start < TIMEOUT_S:
        attempts += 1
        try:
            status, body = fetch(port, path)
            if status == 200 and MARKER in body:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                print(f"[ready] HTTP 200 {url} after {attempts} attempt(s), {elapsed_ms} ms (last before ready: {last})", flush=True)
                if os.environ.get("HRM_NO_BROWSER"):
                    return 0
                open_browser(url)
                return 0
            last = f"HTTP {status}" if status != 200 else "HTTP 200 without issuer marker"
        except Exception as exc:  # connection refused / reset while the server starts
            last = type(exc).__name__
        time.sleep(POLL_S)
    print(f"[browser] server not ready after {int(TIMEOUT_S)} s ({last}); open {url} manually once the server window shows it is serving", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
