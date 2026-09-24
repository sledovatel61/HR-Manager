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
- proxies disabled: the request never goes to a system/environment proxy
- HRM_NO_BROWSER=1: report readiness but do not open a browser (CI/checklists)
- prints one "[ready] ..." or "[browser] ..." line; never prints key material
- exit 0 = page ready (browser opened unless HRM_NO_BROWSER), 1 = not ready
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from urllib.parse import urlsplit

MARKER = b"License Issuer"
TIMEOUT_S = 30.0
POLL_S = 0.25


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("[browser] usage: open_when_ready.py http://127.0.0.1:<port>/license-issuer.html")
        return 1
    url = argv[1]
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1":
        print(f"[browser] refusing non-loopback URL: {url}")
        return 1

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    start = time.monotonic()
    attempts = 0
    last = "no attempt"
    while time.monotonic() - start < TIMEOUT_S:
        attempts += 1
        try:
            with opener.open(url, timeout=2) as resp:
                body = resp.read()
                if resp.status == 200 and MARKER in body:
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    print(f"[ready] HTTP 200 {url} after {attempts} attempt(s), {elapsed_ms} ms (last before ready: {last})", flush=True)
                    if os.environ.get("HRM_NO_BROWSER"):
                        return 0
                    os.startfile(url)  # default browser via the shell (Windows)
                    return 0
                last = f"HTTP {resp.status} without issuer marker"
        except Exception as exc:  # connection refused / reset while the server starts
            last = type(exc).__name__
        time.sleep(POLL_S)
    print(f"[browser] server not ready after {int(TIMEOUT_S)} s ({last}); open {url} manually once the server window shows it is serving", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
