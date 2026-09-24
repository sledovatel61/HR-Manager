#!/usr/bin/env python3
"""Regenerate review-artifacts/issuer-offline-evidence.{json,md}.

What this script is
-------------------
An *evidence generator* for the owner-side offline license issuer of PR #34
(``tools/license-issuer/``). It extracts the issuer sources of the reviewed
commit from the local object database (``git archive``), then runs every check
that can be executed **without Windows** and writes only redacted results.

What this script is NOT
-----------------------
It is **not** the clean-Windows check. It cannot launch ``run-gui.bat`` /
``run-html.bat``, it cannot open the Tkinter GUI, and it cannot observe a real
Windows 10/11 network stack. Those steps are reported as ``NOT RUN`` and the
release verdict must stay NO-GO until the owner performs them on the VM
(``review-artifacts/windows-issuer-bundle-check.md``).

Honesty rules baked in
----------------------
* Every status is computed here from real files/processes; nothing is declared.
* A check whose prerequisites are missing (no ``cryptography``, no ``node``,
  no backend dependencies) is ``NOT RUN``, never ``PASS``.
* Only redacted material is written: key fingerprints, lengths and booleans.
  The script collects every secret it creates and asserts that none of them
  appears in the produced artifacts.
* No key material or license file is ever written inside the repository: all
  generated material lives in a temporary directory outside the work tree.

Usage
-----
    python3 review-artifacts/gen_issuer_offline_evidence.py \
        [--ref e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784] \
        [--python /path/to/venv/bin/python] [--node node]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT_JSON = HERE / "issuer-offline-evidence.json"
OUT_MD = HERE / "issuer-offline-evidence.md"

PR34_HEAD = "e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784"
ISSUER = "tools/license-issuer"
SYNTHETIC_CLIENT = "Синтетический Пилот (синтетическое тестовое значение)"
SYNTHETIC_EXPIRES = "2027-06-30"

# Every value that must never reach the artifacts (filled while running).
SECRETS: set[str] = set()


def secret(value: str) -> None:
    if value and len(value) >= 8:
        SECRETS.add(value)


def redact_fp(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:16]}... (redacted)"


def redact_b64(value: str) -> str:
    return f"{value[:4]}...{value[-2:]} ({len(value)} chars, redacted)"


def redact_hex(value: str) -> str:
    return f"{value[:8]}... ({len(value)} chars, redacted)"


class Runner:
    """Collects step results; never raises on a failed check."""

    def __init__(self, python_bin: str, node_bin: str | None, work: Path, ref: str) -> None:
        self.python = python_bin
        self.node = node_bin
        self.work = work
        self.ref = ref
        self.steps: list[dict[str, Any]] = []
        self.facts: dict[str, Any] = {}

    def add(self, step_id: str, status: str, detail: str) -> None:
        self.steps.append({"id": step_id, "status": status, "detail": detail})

    def pass_(self, step_id: str, detail: str) -> None:
        self.add(step_id, "PASS", detail)

    def fail(self, step_id: str, detail: str) -> None:
        self.add(step_id, "FAIL", detail)

    def gap(self, step_id: str, detail: str) -> None:
        self.add(step_id, "GAP", detail)

    def not_run(self, step_id: str, detail: str) -> None:
        self.add(step_id, "NOT RUN", detail)

    def info(self, step_id: str, detail: str) -> None:
        self.add(step_id, "INFO", detail)

    def run(
        self, cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 300
    ) -> tuple[int, str, str]:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - environment dependent
            return 127, "", f"cannot execute {cmd[0]}: {exc}"


NETBLOCK = '''"""sitecustomize for the review harness: record + deny network APIs."""
import atexit, json, os, socket

ATTEMPTS = []
_MARKER = os.environ.get("NETBLOCK_MARKER")


def _deny(what):
    def f(*a, **k):
        ATTEMPTS.append(what)
        raise OSError("network access denied by review harness: " + what)
    return f


socket.getaddrinfo = _deny("getaddrinfo")
socket.gethostbyname = _deny("gethostbyname")
socket.create_connection = _deny("create_connection")
_Real = socket.socket


class _Blocked(_Real):
    def connect(self, *a, **k):
        ATTEMPTS.append("socket.connect")
        raise OSError("network access denied by review harness: socket.connect")

    def connect_ex(self, *a, **k):
        ATTEMPTS.append("socket.connect_ex")
        raise OSError("network access denied by review harness: socket.connect_ex")

    def sendto(self, *a, **k):
        ATTEMPTS.append("socket.sendto")
        raise OSError("network access denied by review harness: socket.sendto")


socket.socket = _Blocked
socket.SocketType = _Blocked


@atexit.register
def _dump():
    if _MARKER:
        with open(_MARKER, "w", encoding="utf-8") as fh:
            json.dump({"network_api_attempts": ATTEMPTS}, fh)
'''

NODE_HARNESS = r"""
'use strict';
// Runs the *real* inline script of tools/license-issuer/license-issuer.html
// (and its bundled nacl-fast.js fallback) in a JS VM with a minimal DOM shim,
// while every Node network entry point is denied and recorded.
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const PR = process.env.PR_ROOT;
const WORK = process.env.WORK;
const HTML = fs.readFileSync(path.join(PR, 'tools/license-issuer/license-issuer.html'), 'utf8');
const NACL = fs.readFileSync(path.join(PR, 'tools/license-issuer/nacl-fast.js'), 'utf8');

const netAttempts = [];
function deny(what) {
  return function () { netAttempts.push(what); throw new Error('network denied by review harness: ' + what); };
}
const net = require('net');
const dns = require('dns');
const http = require('http');
const https = require('https');
net.Socket.prototype.connect = deny('net.Socket.connect');
net.connect = net.createConnection = deny('net.connect');
dns.lookup = deny('dns.lookup');
dns.resolve = deny('dns.resolve');
http.request = http.get = deny('http.request');
https.request = https.get = deny('https.request');
global.fetch = deny('fetch');
global.WebSocket = class { constructor() { deny('WebSocket'); } };

function inlineScript(html) {
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
  const out = [];
  let m;
  while ((m = re.exec(html))) out.push(m[1]);
  if (out.length !== 1) throw new Error('expected exactly 1 inline script, got ' + out.length);
  return out[0];
}

function makeContext(withSubtle) {
  const counter = { subtle_sign: 0, nacl_sign: 0, nacl_verify: 0, nacl_keypair: 0 };
  const realSubtle = crypto.subtle;
  const subtleWrapped = new Proxy(realSubtle, {
    get(target, prop) {
      const value = target[prop];
      if (typeof value !== 'function') return value;
      return function (...a) {
        if (prop === 'sign') counter.subtle_sign++;
        return value.apply(target, a);
      };
    },
  });
  const cryptoImpl = withSubtle
    ? { getRandomValues: (a) => crypto.getRandomValues(a), subtle: subtleWrapped, randomUUID: (a) => crypto.randomUUID(a) }
    : { getRandomValues: (a) => crypto.getRandomValues(a) };
  const elements = {};
  const alerts = [];
  const sandbox = {
    console, TextEncoder, TextDecoder, atob, btoa, URL, setTimeout, clearTimeout,
    crypto: cryptoImpl, __counter: counter, Uint8Array, Array, JSON, Math, Date, String, Number,
    parseInt, isNaN, Error, Promise, Object, Blob: class Blob {},
    alert: (...a) => alerts.push(a.map(String).join(' ')),
  };
  sandbox.self = sandbox;
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.document = {
    getElementById: (id) => elements[id] ||
      (elements[id] = { value: '', textContent: '', className: '', id, onclick: null }),
    createElement: () => ({ href: '', download: '', click() {} }),
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(NACL, ctx, { filename: 'nacl-fast.js' });
  // record whether the TweetNaCl fallback is exercised (it is the path used on
  // Windows file:// without a secure context, i.e. crypto.subtle missing)
  vm.runInContext(
    "var __sign = nacl.sign;"
    + "var __det = __sign.detached, __ver = __det.verify;"
    + "var __detWrapped = function (m, s) { __counter.nacl_sign++; return __det(m, s); };"
    + "__detWrapped.verify = function (m, s, p) { __counter.nacl_verify++; return __ver.call(__det, m, s, p); };"
    + "__sign.detached = __detWrapped;"
    + "var __kp = __sign.keyPair, __fromSeed = __kp.fromSeed;"
    + "var __kpWrapped = function () { __counter.nacl_keypair++; return __kp.apply(null, arguments); };"
    + "__kpWrapped.fromSeed = function (seed) { __counter.nacl_keypair++; return __fromSeed.call(__kp, seed); };"
    + "__kpWrapped.fromSecretKey = __kp.fromSecretKey;"
    + "__sign.keyPair = __kpWrapped;",
    ctx,
  );
  vm.runInContext(inlineScript(HTML), ctx, { filename: 'license-issuer.html[inline]' });
  const get = (id) => sandbox.document.getElementById(id);
  return { ctx, sandbox, get, alerts, counter };
}

async function issue(context, clientName, expiresAt, maxUsers) {
  const g = context.get;
  g('clientName').value = clientName;
  g('expiresAt').value = expiresAt;
  g('maxUsers').value = maxUsers;
  await context.ctx.genKeypair();
  const privHex = g('privHex').value;
  const pubB64 = g('pubB64').value;
  await context.ctx.issueLicense();
  const text = g('result').textContent.trim();
  return { text, privHex, pubB64 };
}

(async () => {
  const out = { steps: [], alerts: [], network_attempts: netAttempts };
  const push = (id, status, detail) => out.steps.push({ id, status, detail });
  const writeLicense = (name, text, pub) => {
    fs.writeFileSync(path.join(WORK, name + '.hrmlicense'), text);
    fs.writeFileSync(path.join(WORK, name + '.pub.b64'), pub + '\n');
  };

  // --- WebCrypto path (Edge/Chrome on Windows 10/11) ---------------------
  const wc = makeContext(true);
  const res = await issue(wc, process.env.CLIENT_NAME, process.env.EXPIRES_AT, '5');
  const lic = JSON.parse(res.text);
  writeLicense('html_webcrypto', res.text, res.pubB64);
  push('html_webcrypto_keypair', /^[0-9a-f]{64}$/.test(res.privHex) && /^[A-Za-z0-9+/]{43}=$/.test(res.pubB64) ? 'PASS' : 'FAIL',
    'priv=' + res.privHex.length + ' hex chars, pub=' + res.pubB64.length + ' base64 chars');
  push('html_webcrypto_issue', /^[0-9a-f]{128}$/.test(lic.signature) ? 'PASS' : 'FAIL',
    'fields=' + Object.keys(lic).sort().join(','));
  push('html_webcrypto_used_webcrypto_not_fallback',
    wc.counter.subtle_sign > 0 && wc.counter.nacl_sign === 0 ? 'PASS' : 'FAIL',
    'crypto.subtle.sign calls=' + wc.counter.subtle_sign + ', nacl.sign.detached calls=' + wc.counter.nacl_sign
    + ', nacl keypair calls=' + wc.counter.nacl_keypair + ' (Edge 120+/Chrome 120+ path)');
  push('html_webcrypto_self_verify', (await wc.ctx.verifyLicenseData(lic, res.pubB64)) ? 'PASS' : 'FAIL',
    'issuer verifies its own signature');
  const pyLic = JSON.parse(fs.readFileSync(path.join(WORK, 'cli.hrmlicense'), 'utf8'));
  const pyPub = fs.readFileSync(path.join(WORK, 'cli.pub.b64'), 'utf8').trim();
  push('html_webcrypto_verifies_cli_license', (await wc.ctx.verifyLicenseData(pyLic, pyPub)) ? 'PASS' : 'FAIL',
    'HTML issuer accepts a license signed by the Python issuer core');

  // --- TweetNaCl fallback (no secure context / no WebCrypto) -------------
  const na = makeContext(false);
  const resN = await issue(na, process.env.CLIENT_NAME, process.env.EXPIRES_AT, '5');
  const licN = JSON.parse(resN.text);
  writeLicense('html_nacl', resN.text, resN.pubB64);
  push('html_nacl_status_line', 'INFO', String(na.alerts.length) + ' alert(s); page status line: ' +
    String(na.get('status').textContent).replace(/\s+/g, ' ').slice(0, 160));
  push('html_nacl_issue', /^[0-9a-f]{128}$/.test(licN.signature) ? 'PASS' : 'FAIL',
    'fallback signing path without crypto.subtle');
  const naclCross = await na.ctx.verifyLicenseData(pyLic, pyPub);
  push('html_nacl_verifies_cli_license', naclCross ? 'PASS' : 'FAIL', 'fallback verify path');
  push('html_nacl_used_tweetnacl_fallback',
    na.counter.nacl_sign > 0 && na.counter.nacl_verify > 0 && na.counter.subtle_sign === 0 ? 'PASS' : 'FAIL',
    'crypto.subtle removed: nacl.sign.detached calls=' + na.counter.nacl_sign
    + ', nacl verify calls=' + na.counter.nacl_verify + ', nacl keypair calls=' + na.counter.nacl_keypair
    + ' (Windows file:// path, Edge without secure context)');

  // --- validation parity probes -----------------------------------------
  const past = makeContext(true);
  const resPast = await issue(past, process.env.CLIENT_NAME, '2020-01-01', '5');
  if (resPast.text) writeLicense('html_past_expiry', resPast.text, resPast.pubB64);
  push('html_accepts_expires_at_in_the_past', resPast.text ? 'GAP' : 'PASS',
    resPast.text
      ? 'HTML issuer signed a license whose expires_at is in the past (no issued_at<=expires_at check)'
      : 'HTML issuer refused the input');

  const cc = makeContext(true);
  const resCc = await issue(cc, 'Bad\nName', process.env.EXPIRES_AT, '5');
  if (resCc.text) writeLicense('html_control_char', resCc.text, resCc.pubB64);
  push('html_signs_control_char_client_name', resCc.text ? 'GAP' : 'PASS',
    resCc.text ? 'HTML issuer signed a client_name containing a control character (backend rejects such payloads)'
               : 'HTML issuer refused the input');

  out.network_attempts = netAttempts;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS ERROR ' + (e && e.stack ? e.stack : e)); process.exit(3); });
"""

BACKEND_CHECK = r'''
import json, pathlib, sys
PR, WORK = sys.argv[1], pathlib.Path(sys.argv[2])
sys.path.insert(0, PR + "/backend")
sys.path.insert(0, PR + "/tools/license-issuer")
from datetime import UTC, datetime
from app.license import LicenseError, fingerprint_public_key, validate_time_consistency
from app.services.license_service import parse_and_verify_license_text
from license_issuer import issue_license

result = {"checks": [], "public_key_fingerprint": None}


def add(cid, status, detail):
    result["checks"].append({"id": cid, "status": status, "detail": detail})


def backend_accepts(lic_name, pub_name):
    text = (WORK / lic_name).read_text(encoding="utf-8")
    pub = (WORK / pub_name).read_text(encoding="utf-8").strip()
    data = parse_and_verify_license_text(text, pub)
    validate_time_consistency(data["issued_at"], data["expires_at"], None, datetime.now(UTC))
    return data, pub


def backend_rejects(lic_name, pub_name):
    text = (WORK / lic_name).read_text(encoding="utf-8")
    pub = (WORK / pub_name).read_text(encoding="utf-8").strip()
    try:
        parse_and_verify_license_text(text, pub)
    except LicenseError as exc:
        return exc.code
    return None


# 1. CLI-issued license through the real backend service function
try:
    data, pub = backend_accepts("cli.hrmlicense", "cli.pub.b64")
    result["public_key_fingerprint"] = fingerprint_public_key(pub)
    add("backend_verifies_cli_license", "PASS",
        "app.services.license_service.parse_and_verify_license_text + validate_time_consistency accepted it "
        "(endpoint path: POST /license/upload -> upload_license_json)")
except Exception as exc:  # pragma: no cover
    add("backend_verifies_cli_license", "FAIL", f"{type(exc).__name__}: {exc}")

# 2. HTML-issued licenses through the same backend function
for cid, lic in (("backend_verifies_html_webcrypto_license", "html_webcrypto.hrmlicense"),
                 ("backend_verifies_html_nacl_license", "html_nacl.hrmlicense")):
    name = lic.split(".")[0]
    try:
        backend_accepts(lic, name + ".pub.b64")
        add(cid, "PASS", "backend accepted the license produced by the HTML issuer")
    except Exception as exc:
        add(cid, "FAIL", f"{type(exc).__name__}: {exc}")

# 3. Negative controls on the CLI-issued license
try:
    text = (WORK / "cli.hrmlicense").read_text(encoding="utf-8")
    pub = (WORK / "cli.pub.b64").read_text(encoding="utf-8").strip()
    tampered = json.loads(text)
    tampered["max_active_users"] = 999
    (WORK / "tampered.hrmlicense").write_text(json.dumps(tampered), encoding="utf-8")
    (WORK / "tampered.pub.b64").write_text(pub + "\n", encoding="utf-8")
    code = backend_rejects("tampered.hrmlicense", "tampered.pub.b64")
    add("backend_rejects_tampered_field", "PASS" if code else "FAIL",
        f"tampered max_active_users -> LicenseError code={code}")
except Exception as exc:  # pragma: no cover
    add("backend_rejects_tampered_field", "FAIL", f"{type(exc).__name__}: {exc}")

try:
    import base64, os
    (WORK / "wrongkey.pub.b64").write_text(base64.b64encode(os.urandom(32)).decode() + "\n", encoding="utf-8")
    code = backend_rejects("cli.hrmlicense", "wrongkey.pub.b64")
    add("backend_rejects_wrong_public_key", "PASS" if code else "FAIL",
        f"wrong public key -> LicenseError code={code}")
except Exception as exc:  # pragma: no cover
    add("backend_rejects_wrong_public_key", "FAIL", f"{type(exc).__name__}: {exc}")

# 4. Parity probes: the same inputs through the Python issuer core
priv, pub2 = __import__("license_issuer").generate_keypair()
try:
    issue_license(client_name="X", expires_at="2020-01-01", max_active_users=5, private_hex=priv)
    add("python_cli_refuses_past_expiry", "FAIL", "python issuer signed expires_at in the past")
except ValueError as exc:
    add("python_cli_refuses_past_expiry", "PASS", f"refused with ValueError: {exc}")

try:
    d = issue_license(client_name="Bad\nName", expires_at="2027-06-30", max_active_users=5, private_hex=priv)
    add("python_cli_signs_control_char_client_name", "GAP",
        "python issuer core signs a client_name with a control character (backend rejects such payloads); "
        "reachable from the CLI via a shell argument")
except ValueError as exc:
    add("python_cli_signs_control_char_client_name", "PASS", f"refused: {exc}")

print(json.dumps(result, ensure_ascii=False))
'''


def extract_ref(runner: Runner, ref: str, dest: Path) -> bool:
    """Materialise the reviewed commit's issuer sources outside the repo."""
    tar = dest / "pr.tar"
    code, _, err = runner.run(
        ["git", "-C", str(REPO), "archive", "--format=tar", "-o", str(tar), ref, "tools/license-issuer", "backend/app"],
        timeout=180,
    )
    if code != 0 or not tar.is_file():
        runner.not_run("issuer_sources_of_reviewed_commit", f"git archive {ref[:7]} failed: {err.strip()[:200]}")
        return False
    code, _, err = runner.run(["tar", "-x", "-f", str(tar), "-C", str(dest)], timeout=180)
    if code != 0:
        runner.not_run("issuer_sources_of_reviewed_commit", f"tar extraction failed: {err.strip()[:200]}")
        return False
    runner.pass_("issuer_sources_of_reviewed_commit", f"extracted from {ref[:7]} into a temp dir outside the repo")
    return True


def file_rows(runner: Runner, pr: Path, rels: list[str]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for rel in rels:
        path = pr / rel
        if not path.is_file():
            rows[rel] = {"exists": False}
            continue
        data = path.read_bytes()
        rows[rel] = {
            "exists": True,
            "bytes": len(data),
            "lines": data.count(b"\n"),
            "sha256_prefix": hashlib.sha256(data).hexdigest()[:16],
        }
    runner.facts["reviewed_files"] = rows
    return rows


def extract_launchers(build_ps1: str) -> dict[str, str]:
    """Pull the here-strings that build.ps1 writes as run-*.bat (its only source)."""
    out: dict[str, str] = {}
    lines = build_ps1.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r'^\$(\w+)\s*=\s*@"\s*$', lines[i])
        if m:
            name, body = m.group(1), []
            i += 1
            while i < len(lines) and lines[i].rstrip() != '"@':
                body.append(lines[i])
                i += 1
            out[name] = "\n".join(body) + "\n"
        i += 1
    return out


def static_checks(runner: Runner, pr: Path) -> None:
    issuer = pr / ISSUER
    py_sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(issuer.glob("*.py"))}
    html = (issuer / "license-issuer.html").read_text(encoding="utf-8")
    nacl = (issuer / "nacl-fast.js").read_text(encoding="utf-8")
    build_ps1 = (issuer / "build.ps1").read_text(encoding="utf-8")

    # --- Python issuer core: no network/shell primitives ------------------
    banned_py = [
        "import socket",
        "import ssl",
        "import urllib",
        "import http.client",
        "import requests",
        "from urllib",
        "import webbrowser",
        "import subprocess",
        "os.system",
        "urlopen",
    ]
    hits = {name: [b for b in banned_py if b in text] for name, text in py_sources.items()}
    hits = {k: v for k, v in hits.items() if v}
    if hits:
        runner.fail("python_issuer_has_no_network_primitives", f"found: {json.dumps(hits, ensure_ascii=False)}")
    else:
        runner.pass_("python_issuer_has_no_network_primitives",
                     f"{len(py_sources)} files scanned ({', '.join(sorted(py_sources))}): no socket/urllib/"
                     "requests/subprocess/webbrowser usage")

    # --- HTML: no network APIs, no external resources ---------------------
    banned_js = ["fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "importScripts"]
    js_hits = [b for b in banned_js if b in html]
    if js_hits:
        runner.fail("html_has_no_network_apis", f"found: {js_hits}")
    else:
        runner.pass_("html_has_no_network_apis", f"none of {banned_js} appear in license-issuer.html")

    external = re.findall(r'(?:src|href)\s*=\s*"(https?:)?//[^"]+"', html)
    external += re.findall(r"@import\s+url\(\s*['\"]?https?:", html)
    external += re.findall(r'url\(\s*["\']?https?:', html)
    if external:
        runner.fail("html_loads_no_external_resources", f"external references: {external}")
    else:
        runner.pass_("html_loads_no_external_resources",
                     "only local <script src=\"nacl-fast.js\">; no http(s) src/href/@import/url() in the page")
    urls = re.findall(r"https?://[^\s\"'<>)]+", html)
    runner.info("html_url_strings_are_text_only",
                f"{len(urls)} absolute URL(s) appear in license-issuer.html, all inside human-readable text "
                "(jsdelivr fallback hint / documentation), none as a resource reference"
                if urls else "no absolute URL in license-issuer.html")

    # --- bundled TweetNaCl -------------------------------------------------
    nacl_net = [b for b in ("fetch(", "XMLHttpRequest", "WebSocket", "require('http") if b in nacl]
    if nacl_net:
        runner.fail("nacl_fast_js_has_no_network_apis", f"found: {nacl_net}")
    else:
        runner.pass_("nacl_fast_js_has_no_network_apis",
                     f"nacl-fast.js ({len(nacl)} chars, {nacl.count(chr(10))} lines) contains no fetch/XHR/WebSocket/HTTP access")

    # --- launchers: only generated, never committed ------------------------
    launchers = extract_launchers(build_ps1)
    bats = {k: v for k, v in launchers.items() if k.lower().endswith("bat")}
    committed = sorted(p.name for p in REPO.rglob("*.bat") if ".git" not in p.parts and ".venv" not in p.parts)
    runner.facts["launchers"] = {
        "generated_by_build_ps1": {
            k: {"bytes": len(v), "sha256_prefix": hashlib.sha256(v.encode()).hexdigest()[:16]}
            for k, v in bats.items()
        },
        "committed_bat_files_in_repo": committed,
    }
    if committed:
        runner.fail("no_bat_files_committed", f"committed .bat files: {committed}")
    else:
        runner.pass_("no_bat_files_committed",
                     "no run-gui.bat / run-html.bat anywhere in the reviewed commit: they exist only as "
                     "PowerShell here-strings inside build.ps1 and are written by build.ps1 at build time")

    gui = bats.get("runGuiBat", "")
    if gui and r'..\python\python.exe' in gui and "gui.py" in gui and "exit /b 1" in gui and "pause" in gui:
        runner.pass_("run_gui_bat_contract",
                     "run-gui.bat (from build.ps1): bundled %~dp0..\\python\\python.exe, gui.py, fail-closed "
                     f"exit /b 1 when the bundle is incomplete ({len(gui)} bytes)")
    else:
        runner.fail("run_gui_bat_contract", "run-gui.bat here-string missing or malformed in build.ps1")

    htmlbat = bats.get("runHtmlBat", "")
    htmlbat_ok = all(t in htmlbat for t in ('http.server %PORT%', '--directory', 'start "" http://localhost:', "license-issuer.html"))
    if htmlbat_ok:
        runner.pass_("run_html_bat_contract",
                     f"run-html.bat (from build.ps1, {len(htmlbat)} bytes): bundled python -m http.server on port "
                     "8765 serving the bundle dir, then opens http://localhost:8765/license-issuer.html")
    else:
        runner.fail("run_html_bat_contract", "run-html.bat here-string missing or malformed in build.ps1")

    if 'set PY_EXE=python' in htmlbat and "exit /b 1" not in htmlbat:
        runner.gap("run_html_bat_autonomy_contradiction",
                   "run-html.bat silently falls back to a system 'python' when the bundled interpreter is "
                   "missing, while the same file's comment, HOWTO.txt and README promise 'no system Python "
                   "needed'; run-gui.bat/run-cli.bat fail closed instead. On a clean Windows VM without Python "
                   "this yields a confusing 'python is not recognized' error instead of the clear message.")
    if re.search(r'^\s*start "" http://localhost', htmlbat, re.M):
        runner.gap("run_html_bat_opens_browser_before_server",
                   "run-html.bat opens the browser before starting the HTTP server, and does not check the "
                   "port: a slow start or an occupied port 8765 shows 'site can't be reached' even though the "
                   "bundle is fine")
    if "-b 127.0.0.1" not in htmlbat:
        runner.gap("run_html_bat_binds_all_interfaces",
                   "run-html.bat runs `python -m http.server 8765` without -b: http.server binds 0.0.0.0, so "
                   "while the window is open the bundle directory (and any key file the owner saved next to it) "
                   "is reachable from the LAN. Windows firewall may also prompt. Passing -b 127.0.0.1 fixes it.")

    # --- key material must not be committed --------------------------------
    patterns = {
        "private key hex (64 hex chars)": re.compile(r"\b[0-9a-fA-F]{64}\b"),
        "hrmlicense file tracked": re.compile(r"\.hrmlicense\b"),
    }
    tracked = runner.run(["git", "-C", str(REPO), "ls-tree", "-r", "--name-only", runner.ref])[1].split()
    suspicious: dict[str, list[str]] = {}
    if tracked:
        runner.facts["tracked_files_scanned_for_key_material"] = len(tracked)
        lic_files = [t for t in tracked if t.endswith(".hrmlicense")]
        if lic_files:
            suspicious["license files committed"] = lic_files
        key_paths = [t for t in tracked if t in ("infra/license/public_key.b64", "tools/license-issuer/keys/private_key.hex")]
        if key_paths:
            suspicious["key files committed"] = key_paths
        pk = re.compile(r"\b[0-9a-fA-F]{64}\b")
        issuers = [t for t in tracked if t.startswith(ISSUER) and t.endswith(".py")]
        for rel in issuers:
            text = runner.run(["git", "-C", str(REPO), "show", f"{runner.ref}:{rel}"])[1]
            for m in pk.finditer(text):
                suspicious.setdefault("64-hex literal in issuer source", []).append(f"{rel}:{m.group(0)[:8]}...")
    if suspicious:
        runner.fail("no_key_material_committed", json.dumps(suspicious, ensure_ascii=False))
    else:
        runner.pass_("no_key_material_committed",
                     f"{runner.facts.get('tracked_files_scanned_for_key_material', 0)} tracked files: no "
                     "*.hrmlicense, no infra/license/public_key.b64, no keys/ directory, no 64-hex literal in "
                     "tools/license-issuer/*.py")

    # --- .gitignore coverage for owner-side material -----------------------
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    ignored = [p for p in ("keys/", "private_key.hex", "*.hrmlicense") if p in gitignore]
    runner.facts["gitignore_entries_for_owner_material"] = ignored
    if not ignored:
        runner.gap("gitignore_does_not_cover_owner_material",
                   ".gitignore has no entry for keys/, private_key.hex or *.hrmlicense. build.ps1 step 5 runs "
                   "`cli.py gen-keypair` without --out-dir, so the maintainer's smoke test writes "
                   "keys\\private_key.hex relative to the current working directory - inside the git work tree "
                   "when build.ps1 is started from the repo root - and `git add -A` would stage a private key.")


def cli_flow(runner: Runner, pr: Path, work: Path) -> None:
    harness = work / "harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "sitecustomize.py").write_text(NETBLOCK, encoding="utf-8")
    bundle = work / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    for name in ("cli.py", "license_issuer.py"):
        shutil.copy2(pr / ISSUER / name, bundle / name)
    keys, licenses = work / "keys", work / "licenses"
    keys.mkdir(exist_ok=True)
    licenses.mkdir(exist_ok=True)

    probe = runner.run([runner.python, "-c", "import cryptography;print(cryptography.__version__)"])
    if probe[0] != 0:
        runner.not_run("cli_offline_flow", "no 'cryptography' module for the harness interpreter: " + probe[2].strip()[:160])
        return
    runner.facts["cryptography_version"] = probe[1].strip()

    marker = work / "netblock.json"
    env = {"PYTHONPATH": str(work / "harness"), "NETBLOCK_MARKER": str(marker)}
    steps = [
        ("cli_gen_keypair", ["cli.py", "gen-keypair", "--out-dir", str(keys)]),
        ("cli_issue", ["cli.py", "issue", "--private-key-file", str(keys / "private_key.hex"),
                       "--client", SYNTHETIC_CLIENT, "--expires", SYNTHETIC_EXPIRES, "--max-users", "5",
                       "--out", str(licenses / "cli.hrmlicense")]),
        ("cli_verify", ["cli.py", "verify", "--public-key-file", str(keys / "public_key.b64"),
                        "--license-file", str(licenses / "cli.hrmlicense")]),
    ]
    captured: list[str] = []
    for step_id, argv in steps:
        code, out, err = runner.run([runner.python, *argv], cwd=bundle, env=env)
        captured += [out, err]
        if code == 0:
            label = {"cli_gen_keypair": "gen-keypair", "cli_issue": "issue", "cli_verify": "verify"}[step_id]
            runner.pass_(f"{step_id}_offline", f"cli.py {label}: exit 0, offline (see network harness below)")
        else:
            runner.fail(f"{step_id}_offline", f"cli.py exit {code}: {(err or out).strip()[:200]}")

    if marker.is_file():
        attempts = json.loads(marker.read_text(encoding="utf-8")).get("network_api_attempts", [])
        if attempts:
            runner.fail("cli_makes_no_network_calls", f"network API attempts recorded: {attempts}")
        else:
            runner.pass_("cli_makes_no_network_calls",
                         "gen-keypair + issue + verify ran under a socket-denying harness "
                         "(socket.connect/connect_ex/sendto/getaddrinfo/create_connection raise) and recorded "
                         "0 network API attempts")
    else:
        runner.not_run("cli_makes_no_network_calls", "harness marker not written")

    priv_hex = (keys / "private_key.hex").read_text(encoding="utf-8").strip()
    pub_b64 = (keys / "public_key.b64").read_text(encoding="utf-8").strip()
    lic_text = (licenses / "cli.hrmlicense").read_text(encoding="utf-8")
    secret(priv_hex)
    secret(lic_text)
    secret(pub_b64)
    runner.facts["cli_public_key"] = redact_b64(pub_b64) if len(pub_b64) == 44 else "unexpected length"
    runner.facts["cli_private_key_length"] = len(priv_hex)

    leaks = [step for step, blob in zip([s[0] for s in steps] * 2, captured) if priv_hex in blob]
    if leaks:
        runner.fail("no_private_key_in_cli_output", f"private key found in output of {leaks}")
    else:
        runner.pass_("no_private_key_in_cli_output",
                     "stdout+stderr of gen-keypair/issue/verify contain the file paths and license metadata only; "
                     "the 64-hex private key never appears")

    # copy for the backend stage
    shutil.copy2(licenses / "cli.hrmlicense", work / "cli.hrmlicense")
    shutil.copy2(keys / "public_key.b64", work / "cli.pub.b64")


def backend_flow(runner: Runner, pr: Path, work: Path) -> None:
    probe = runner.run([runner.python, "-c", "import fastapi, sqlalchemy, pydantic_settings;print('ok')"])
    if probe[0] != 0:
        runner.not_run("backend_verification_of_issuer_license",
                       "backend runtime deps not importable by the harness interpreter: " + probe[2].strip()[:160])
        return
    script = work / "backend_check.py"
    script.write_text(BACKEND_CHECK, encoding="utf-8")
    code, out, err = runner.run([runner.python, str(script), str(pr), str(work)], timeout=300)
    if code != 0:
        runner.fail("backend_verification_of_issuer_license", f"harness exit {code}: {err.strip()[:300]}")
        return
    payload = json.loads(out)
    runner.facts["backend_public_key_fingerprint"] = payload.get("public_key_fingerprint")
    for check in payload["checks"]:
        runner.add(check["id"], check["status"], check["detail"])


def html_flow(runner: Runner, pr: Path, work: Path) -> None:
    if not runner.node:
        runner.not_run("html_issuer_dom_harness", "node is not available in this environment")
        return
    harness = work / "harness"
    harness.mkdir(exist_ok=True)
    (harness / "sitecustomize.py").write_text(NETBLOCK, encoding="utf-8")
    js = harness / "html_harness.js"
    js.write_text(NODE_HARNESS, encoding="utf-8")
    env = {"PR_ROOT": str(pr), "WORK": str(work), "CLIENT_NAME": SYNTHETIC_CLIENT, "EXPIRES_AT": SYNTHETIC_EXPIRES}
    code, out, err = runner.run([runner.node, str(js)], env=env, timeout=300)
    if code != 0:
        runner.fail("html_issuer_dom_harness", f"node harness exit {code}: {(err or out).strip()[:300]}")
        return
    payload = json.loads(out.strip().splitlines()[-1])
    for check in payload["steps"]:
        runner.add(check["id"], check["status"], check["detail"])
    if payload["network_attempts"]:
        runner.fail("html_makes_no_network_calls", f"network attempts: {payload['network_attempts']}")
    else:
        runner.pass_("html_makes_no_network_calls",
                     "the page's own inline script ran with net.Socket.connect / dns.lookup / http(s).request / "
                     "fetch / WebSocket denied and recorded 0 attempts, for both the WebCrypto and the TweetNaCl path")
    # collect secret material produced by the browser-side harness
    for name in ("html_webcrypto", "html_nacl", "html_past_expiry", "html_control_char"):
        lic = work / f"{name}.hrmlicense"
        if lic.is_file():
            text = lic.read_text(encoding="utf-8")
            secret(text)
            try:
                secret(json.loads(text)["signature"])
            except Exception:  # pragma: no cover
                pass
        pub = work / f"{name}.pub.b64"
        if pub.is_file():
            secret(pub.read_text(encoding="utf-8").strip())


def serve_flow(runner: Runner, pr: Path, work: Path) -> None:
    """Emulate run-html.bat's server on this host (Linux python, not the Windows bundle)."""
    serverdir = work / "serverdir"
    serverdir.mkdir(exist_ok=True)
    for name in ("license-issuer.html", "nacl-fast.js"):
        shutil.copy2(pr / ISSUER / name, serverdir / name)
    port = 8791
    proc = subprocess.Popen(
        [runner.python, "-m", "http.server", str(port), "--directory", str(serverdir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import time
        import urllib.request

        body = None
        for _ in range(30):
            time.sleep(0.2)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/license-issuer.html", timeout=2) as resp:
                    body = (resp.status, resp.read())
                break
            except Exception:
                continue
        if body is None:
            runner.fail("emulated_runner_serves_page", "local http.server did not answer on 127.0.0.1")
        else:
            status, data = body
            same = data == (serverdir / "license-issuer.html").read_bytes()
            detail = (
                f"python -m http.server {port} --directory <bundle>: GET /license-issuer.html -> HTTP {status}, "
                f"{len(data)} bytes, byte-identical to the file: {same}. Emulated with the host Linux Python "
                f"{sys.version.split()[0]} (cryptography {runner.facts.get('cryptography_version', 'n/a')}); the "
                "Windows launcher run-html.bat itself was NOT executed"
            )
            if status == 200 and same:
                runner.pass_("emulated_runner_serves_page", detail)
            else:
                runner.fail("emulated_runner_serves_page", detail)
        # bind scope
        listening: list[str] = []
        tcp = Path("/proc/net/tcp")
        if tcp.is_file():
            for line in tcp.read_text().splitlines()[1:]:
                f = line.split()
                if len(f) > 3 and f[3] == "0A" and int(f[1].split(":")[1], 16) == port:
                    addr = bytes.fromhex(f[1].split(":")[0])[::-1]
                    listening.append(".".join(str(b) for b in addr))
        runner.facts["emulated_runner_listen_addresses"] = sorted(set(listening))
        if any(a == "0.0.0.0" for a in listening):
            runner.gap("http_server_binds_all_interfaces_measured",
                       "the runner's `python -m http.server` listens on 0.0.0.0 (measured on this host): the bundle "
                       "directory is reachable from the LAN for as long as the window is open")
        else:
            runner.info("http_server_binds_all_interfaces_measured",
                        f"listen addresses observed: {sorted(set(listening))} (python -m http.server defaults to "
                        "0.0.0.0; -b 127.0.0.1 would restrict it)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def not_run_block(runner: Runner) -> None:
    for step_id, detail in [
        ("windows_vm_run_gui_bat", "double-click run-gui.bat on a clean Windows 10/11 VM without Python/pip/internet "
                                   "- needs the owner's VM; NOT executed here (this sandbox has no Windows, no "
                                   "virtualisation and no KVM)"),
        ("windows_vm_run_html_bat", "double-click run-html.bat, browser opens http://localhost:8765/license-issuer.html"),
        ("windows_vm_issue_license_via_gui", "issue a real .hrmlicense through the Tkinter GUI on the VM"),
        ("windows_vm_issue_license_via_html", "issue a real .hrmlicense through the HTML page on the VM's Edge"),
        ("windows_vm_no_network_capture", "confirm with Wireshark / Resource Monitor that the issuer performs no "
                                          "outbound connection (loopback HTTP server from run-html.bat excluded)"),
        ("windows_vm_no_private_key_in_logs_or_artifacts", "after issuing, grep the VM's Temp/AppData/logs and the "
                                                           "bundle dir for the private key hex"),
        ("gui_tkinter_runtime", "gui.py executed (Tkinter needs a desktop session; static review only here)"),
        ("bundle_build_with_embeddable_python", "build.ps1 executed (downloads python.org embeddable + get-pip at "
                                                "build time; not possible from this sandbox)"),
    ]:
        runner.not_run(step_id, detail)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", default=PR34_HEAD, help="commit under review (default: PR #34 head)")
    ap.add_argument("--python", default=sys.executable, help="interpreter with cryptography (+ backend deps)")
    ap.add_argument("--node", default=shutil.which("node") or "", help="node binary for the HTML harness")
    ap.add_argument("--keep-work-dir", action="store_true")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="hrm-issuer-evidence-"))
    runner = Runner(python_bin=args.python, node_bin=args.node or None, work=work, ref=args.ref)
    try:
        pr = work / "pr"
        pr.mkdir(parents=True, exist_ok=True)
        if extract_ref(runner, args.ref, pr):
            issuer = pr / ISSUER
            file_rows(runner, pr, [
                f"{ISSUER}/build.ps1", f"{ISSUER}/cli.py", f"{ISSUER}/gui.py",
                f"{ISSUER}/license_issuer.py", f"{ISSUER}/license-issuer.html",
                f"{ISSUER}/nacl-fast.js", f"{ISSUER}/README.md",
                "backend/app/license.py", "backend/app/services/license_service.py",
            ])
            static_checks(runner, pr)
            cli_flow(runner, pr, work)
            html_flow(runner, pr, work)
            serve_flow(runner, pr, work)
            backend_flow(runner, pr, work)
        not_run_block(runner)

        summary: dict[str, int] = {}
        for step in runner.steps:
            summary[step["status"]] = summary.get(step["status"], 0) + 1

        report = {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "review-artifacts/gen_issuer_offline_evidence.py (runs the real issuer code; no declarations)",
            "scope": {
                "pr": 34,
                "reviewed_commit": args.ref,
                "host": "Linux review sandbox (no Windows, no virtualisation/KVM, no wine) - NOT the owner VM",
                "what_this_proves": [
                    "the issuer sources of the reviewed commit are consistent, offline-only and cross-compatible "
                    "with the backend verifier",
                    "a license produced by the Python core and by the HTML page verifies with the backend's own "
                    "license_service.parse_and_verify_license_text",
                ],
                "what_this_does_not_prove": [
                    "that run-gui.bat / run-html.bat start on a clean Windows 10/11 VM",
                    "that the bundles work without system Python (build.ps1 was never executed)",
                    "that Windows makes no network request while issuing (see verdict: still NO-GO)",
                ],
            },
            "summary": summary,
            "facts": runner.facts,
            "steps": runner.steps,
            "privacy": {
                "redaction": "private keys, public keys, signatures and license bodies never appear; only "
                             "fingerprints, lengths and booleans",
                "pii": "the test license uses the synthetic client name 'Синтетический Пилот "
                       "(синтетическое тестовое значение)'; no real client name is used or stored",
            },
        }
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"

        md = [
            "# Offline issuer — evidence from automated Linux checks (redacted)",
            "",
            f"- generated: {report['generated_at']}",
            f"- reviewed commit: `{args.ref}`",
            "- tool: `review-artifacts/gen_issuer_offline_evidence.py` (re-runs every check below)",
            "- host: Linux review sandbox — **not** the owner's Windows VM",
            "",
            "> This file proves what could be executed without Windows. It is **not** the clean-Windows",
            "> issuer check: `run-gui.bat`, `run-html.bat`, the Tkinter GUI and the bundle build remain",
            "> `NOT RUN` (see `windows-issuer-bundle-check.md`). No `GO` may be derived from it.",
            "",
            f"## Summary: {json.dumps(summary, ensure_ascii=False)}",
            "",
            "| step | status | detail |",
            "|---|---|---|",
        ]
        for step in runner.steps:
            detail = step["detail"].replace("|", "\\|").replace("\n", " ")
            md.append(f"| `{step['id']}` | {step['status']} | {detail} |")
        md += [
            "",
            "## Files of the reviewed commit (hashes of what was tested)",
            "",
            "| file | exists | bytes | lines | sha256 prefix |",
            "|---|---|---|---|---|",
        ]
        for rel, row in runner.facts.get("reviewed_files", {}).items():
            if row.get("exists"):
                md.append(f"| `{rel}` | yes | {row['bytes']} | {row['lines']} | `{row['sha256_prefix']}` |")
            else:
                md.append(f"| `{rel}` | **no** | | | |")
        md += [
            "",
            "## Launchers (extracted from `build.ps1`, the only source of `run-*.bat`)",
            "",
            "| launcher | bytes | sha256 prefix |",
            "|---|---|---|",
        ]
        for name, row in runner.facts.get("launchers", {}).get("generated_by_build_ps1", {}).items():
            md.append(f"| `{name}` | {row['bytes']} | `{row['sha256_prefix']}` |")
        md += [
            "",
            f"- committed `.bat` files in the repository: "
            f"{runner.facts.get('launchers', {}).get('committed_bat_files_in_repo') or 'none'}",
            f"- `cryptography` used by the harness: {runner.facts.get('cryptography_version', 'n/a')}",
            f"- backend fingerprint of the ephemeral test key: {runner.facts.get('backend_public_key_fingerprint')}",
            f"- emulated runner listen addresses: {runner.facts.get('emulated_runner_listen_addresses')}",
            "",
            "## Redaction",
            "",
            report["privacy"]["redaction"] + ". " + report["privacy"]["pii"],
            "",
        ]
        md_text = "\n".join(md)

        for artifact_text in (text, md_text):
            for value in SECRETS:
                assert value not in artifact_text, "refusing to write an artifact containing secret material"
        OUT_JSON.write_text(text, encoding="utf-8")
        OUT_MD.write_text(md_text, encoding="utf-8")
        print(f"wrote {OUT_JSON.name} ({len(text)} bytes) and {OUT_MD.name} ({len(md_text)} bytes)")
        print("summary:", json.dumps(summary, ensure_ascii=False))
    finally:
        if not args.keep_work_dir:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("work dir kept:", work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
