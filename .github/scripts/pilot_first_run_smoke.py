#!/usr/bin/env python3
"""CI smoke for the pilot compose profile (phase 12).

Exercises the real loopback security model end-to-end: the installer path
issues a one-time pairing code through the container CLI (STDIN only), a
browser-like claim exchanges it for a session, the owner sets their own
password, and the chosen credentials work through the ordinary login. The
pairing is then proven dead, and no second pilot can exist.

Never echoes the code, surname or passwords into the job log beyond what the
test itself needs (all values are ephemeral CI secrets).
"""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

COMPOSE = [
    "docker", "compose", "-p", "hr-manager-pilot",
    "-f", "infra/docker-compose.yml",
    "-f", "infra/compose.pilot.yml",
]
BASE = "http://127.0.0.1:8081"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def fail(msg: str) -> None:
    print(f"PILOT SMOKE FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def compose(*args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(
        COMPOSE + list(args), input=stdin, text=True, capture_output=True
    )
    if proc.returncode != 0:
        fail(f"compose {' '.join(args)} exit {proc.returncode}: {proc.stderr[-500:]}")
    return proc.stdout


def http(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Origin": BASE, **(headers or {})},
    )
    try:
        with opener.open(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except Exception:
            return exc.code, None


def main() -> None:
    # 0) wait for the published frontend loopback port
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            status, body = http("GET", "/api/health")
            if status == 200 and body and body.get("status") == "ok":
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        fail("frontend did not become healthy on 127.0.0.1:8081")

    # 1) readiness gate: fresh install, no owner yet
    status, state = http("GET", "/api/setup/first-run/state")
    if status != 200 or state is None or state.get("fresh_install") is not True:
        fail(f"unexpected first-run state on fresh volume: {status} {state}")
    if state.get("pilot_owner_exists"):
        fail("pilot owner already exists on a fresh volume?!")

    # 2) the engine-side pairing issuance: code + surname travel via STDIN
    code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    compose("exec", "-T", "backend", "python", "-m", "app.cli", "pilot-pairing", "issue",
            stdin=json.dumps({"code": code, "surname": "Смолов", "work_role": "manager"}))

    status, state = http("GET", "/api/setup/first-run/state")
    if status != 200 or not state.get("pending"):
        fail("pairing issuance did not open a pending first-run window")

    # 3) wrong code first: must be rejected without consuming the pairing
    status, _ = http("POST", "/api/setup/first-run/claim", {"code": "ZZZZZZ"})
    if status != 403:  # uniform "неверный код" — существование пары не раскрывается
        fail(f"wrong code should be rejected with 403 (got {status})")

    # 4) the claim: session cookies + deterministic username, no password ever shown
    status, claimed = http("POST", "/api/setup/first-run/claim", {"code": code})
    if status != 200 or not claimed:
        fail(f"valid code claim failed ({status})")
    user = claimed["user"]
    if not user["username"].startswith("smolov-pilot-"):
        fail(f"unexpected deterministic username: {user['username']}")
    if user["role"] != "admin" or not claimed.get("must_set_password"):
        fail("claimed owner must be admin with must_set_password=true")
    csrf = claimed.get("csrf_token") or ""
    if not csrf:
        fail("claim must hand out the CSRF token like login does")

    # 5) the claim is one-shot: a second attempt cannot succeed
    status, _ = http("POST", "/api/setup/first-run/claim", {"code": code})
    if status == 200:
        fail("pairing code was reusable!")
    if status != 410:  # «активной установки не найдено» — окно закрыто намертво
        fail(f"unexpected status for a dead pairing: {status}")

    # 6) the owner sets their own password (session + CSRF double-submit)
    password = "Str0ng-Pilot-2026!"
    status, _ = http("PUT", "/api/setup/first-run/password", {"password": "short1"},
                     headers={"X-CSRF-Token": csrf})
    if status not in (422, 400):
        fail(f"weak password must be rejected (got {status})")
    status, _ = http("PUT", "/api/setup/first-run/password", {"password": password},
                     headers={"X-CSRF-Token": csrf})
    if status != 200:
        fail(f"password change failed ({status})")

    # 7) ordinary login works with the chosen password from a "clean" client
    clean = urllib.request.build_opener()  # no cookies
    req = urllib.request.Request(
        f"{BASE}/api/auth/login",
        data=json.dumps({"username": user["username"], "password": password}).encode(),
        headers={"Content-Type": "application/json", "Origin": BASE},
        method="POST",
    )
    with clean.open(req, timeout=15) as resp:
        if resp.status != 200:
            fail("login with the chosen password returned non-200")

    # 8) the bootstrap window is closed forever (even with a valid CSRF header)
    status, _ = http("PUT", "/api/setup/first-run/password", {"password": "Another-Password-1"},
                     headers={"X-CSRF-Token": csrf})
    if status != 403:
        fail(f"password endpoint must be closed after bootstrap (got {status})")

    # 9) ops readiness over the same loopback (release_sha present, no secrets)
    status, ops = http("GET", "/api/ops/status")
    if status != 200 or "release_sha" not in (ops or {}):
        fail(f"ops/status unavailable for the engine ({status})")

    print("pilot first-run smoke OK")


if __name__ == "__main__":
    main()
