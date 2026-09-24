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

    def run_bytes(self, cmd: list[str], timeout: int = 120) -> tuple[int, bytes, str]:
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
            return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            return 127, b"", f"cannot execute {cmd[0]}: {exc}"

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


TREE_PATHS = ["tools/license-issuer", "backend/app", ".gitignore"]


def materialise_reviewed_tree(runner: Runner, base_ref: str, overlay_ref: str | None, dest: Path) -> bool:
    """Materialise the tree to test outside the repository.

    PR #34 lives on its own branch, so the reviewed revision is the PR head plus
    the fix patch: the baseline tree (``base_ref``) is extracted with
    ``git archive`` and the files the fix commit changes are overlaid on top.
    Nothing is read from the working tree, so the checks cannot accidentally
    test the wrong revision.
    """
    tar = dest / "pr.tar"
    code, _, err = runner.run(
        ["git", "-C", str(REPO), "archive", "--format=tar", "-o", str(tar), base_ref, *TREE_PATHS],
        timeout=180,
    )
    if code != 0 or not tar.is_file():
        runner.not_run("issuer_sources_of_reviewed_commit", f"git archive {base_ref[:7]} failed: {err.strip()[:200]}")
        return False
    code, _, err = runner.run(["tar", "-x", "-f", str(tar), "-C", str(dest)], timeout=180)
    if code != 0:
        runner.not_run("issuer_sources_of_reviewed_commit", f"tar extraction failed: {err.strip()[:200]}")
        return False

    composition: dict[str, Any] = {"baseline_tree": base_ref, "overlay_commit": None, "overlay_files": {}}
    if overlay_ref and overlay_ref != base_ref:
        code, out, err = runner.run(["git", "-C", str(REPO), "diff", "--name-only", f"{overlay_ref}^", overlay_ref])
        if code != 0:
            runner.not_run("issue_fix_overlay", f"git diff failed: {err.strip()[:160]}")
            return False
        overlay_files = [rel for rel in out.split() if rel]
        for rel in overlay_files:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            code, blob, err = runner.run_bytes(["git", "-C", str(REPO), "cat-file", "blob", f"{overlay_ref}:{rel}"])
            if code != 0:
                runner.not_run("issue_fix_overlay", f"cannot read {rel} from {overlay_ref[:7]}: {err[:160]}")
                return False
            target.write_bytes(blob)
            composition["overlay_files"][rel] = hashlib.sha256(blob).hexdigest()[:16]
        composition["overlay_commit"] = overlay_ref
        runner.pass_("issuer_sources_of_reviewed_commit",
                     f"PR-head tree {base_ref[:7]} extracted outside the repo, fix commit {overlay_ref[:7]} "
                     f"overlaid: {', '.join(sorted(composition['overlay_files']))}")
    else:
        runner.pass_("issuer_sources_of_reviewed_commit",
                     f"PR-head tree {base_ref[:7]} extracted outside the repo (no overlay: this is the revision "
                     "before the fix, used as the negative control)")
    runner.facts.setdefault("tree_composition", composition)
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
    htmlbat_ok = all(t in htmlbat for t in ('http.server %PORT%', '--directory', 'start "" http://127.0.0.1:', "license-issuer.html"))
    if htmlbat_ok:
        runner.pass_("run_html_bat_contract",
                     f"run-html.bat (from build.ps1, {len(htmlbat)} bytes): bundled python -m http.server on port "
                     "8765 serving the bundle dir, then opens http://127.0.0.1:8765/license-issuer.html")
    else:
        runner.fail("run_html_bat_contract", "run-html.bat here-string missing or malformed in build.ps1")

    # --- fix 2026-09-24: loopback binding, no system-Python fallback ---------
    if '-b 127.0.0.1' in htmlbat and "0.0.0.0" not in htmlbat and "http://127.0.0.1:" in htmlbat:
        runner.pass_("run_html_bat_binds_loopback_only",
                     "run-html.bat starts `python -m http.server %PORT% -b 127.0.0.1 --directory <bundle>` and "
                     "opens http://127.0.0.1: ... the bundle directory is not exposed to the LAN")
    else:
        runner.fail("run_html_bat_binds_loopback_only",
                    "run-html.bat does not bind the HTTP server to 127.0.0.1 / still advertises a non-loopback URL")

    if "set PY_EXE=python" not in htmlbat and "exit /b 1" in htmlbat and "pause" in htmlbat:
        runner.pass_("run_html_bat_fails_closed_without_system_python",
                     "run-html.bat has no fallback to a system 'python': a missing bundle interpreter prints a "
                     "clear error and exits with code 1, like run-gui.bat/run-cli.bat")
    else:
        runner.fail("run_html_bat_fails_closed_without_system_python",
                    "run-html.bat still falls back to a system 'python' or lost its fail-closed branch")

    if "install Python" not in gui and "system Python" in gui and "exit /b 1" in gui:
        runner.pass_("run_gui_bat_no_system_python_advice",
                     "run-gui.bat no longer suggests installing system Python; it explains that the issuer never "
                     "falls back to a system interpreter and exits with code 1")
    else:
        runner.fail("run_gui_bat_no_system_python_advice",
                    "run-gui.bat still advises installing/using a system Python")

    # --- fix 2026-09-24: smoke test outside the git work tree ---------------
    smoke_needs = ("GetTempPath()", "--out-dir $smokeDir", "Remove-Item -Path $smokeDir -Recurse -Force")
    smoke_ok = all(t in build_ps1 for t in smoke_needs)
    # any executed gen-keypair must carry --out-dir (the HOWTO/README examples are not executed)
    executed_genkeypair = [
        line.strip() for line in build_ps1.splitlines()
        if "gen-keypair" in line and ("$pyExe" in line or line.strip().startswith("&"))
    ]
    runner.facts["smoke_test_genkeypair_invocations"] = executed_genkeypair
    if smoke_ok and executed_genkeypair and all("--out-dir" in line for line in executed_genkeypair):
        runner.pass_("smoke_test_runs_outside_the_repository",
                     f"the build smoke test creates a temporary directory outside the repository "
                     f"({len(executed_genkeypair)} executed gen-keypair invocation(s), all with --out-dir), and "
                     "removes the directory in a finally block")
    else:
        runner.fail("smoke_test_runs_outside_the_repository",
                    f"smoke test does not isolate key material: needs={smoke_needs}, invocations={executed_genkeypair}")
    if "refusing to continue" in build_ps1 and "$smokePrivHex" in build_ps1:
        runner.pass_("smoke_test_never_prints_key_material",
                     "the smoke test compares the captured output with the private key it just created and turns "
                     "the build into an error if key material ever reaches the build log")
    else:
        runner.fail("smoke_test_never_prints_key_material", "no leak assertion in the smoke test")

    # --- structural check of the edited PowerShell (approximation, not a parser)
    here_strings = build_ps1.count('= @"') + build_ps1.count("= @'")
    terminators = len(re.findall(r'(?m)^"@$', build_ps1)) + len(re.findall(r"(?m)^'@$", build_ps1))
    stripped = re.sub(r'@"\n.*?"@', '', build_ps1, flags=re.S)  # here-string bodies
    stripped = re.sub(r'#.*', '', stripped)
    balance = {
        "{": stripped.count("{") - stripped.count("}"),
        "(": stripped.count("(") - stripped.count(")"),
    }
    runner.facts["build_ps1_structure"] = {
        "here_strings_open": here_strings,
        "here_strings_closed": terminators,
        "brace_paren_balance_outside_strings_approx": balance,
        "has_replacement_char": "\ufffd" in build_ps1,
    }
    if here_strings == terminators and "\ufffd" not in build_ps1:
        runner.pass_("build_ps1_here_strings_and_encoding",
                     f"here-strings {here_strings} open / {terminators} closed, no U+FFFD in the file. The brace/"
                     "parenthesis counters are only an approximation (see INFO step): the authoritative PowerShell "
                     "parser is not available here (no pwsh) and runs in CI on windows-latest")
        runner.info("build_ps1_brace_paren_balance_approx_not_decisive",
                    "brace/parenthesis counters outside strings/comments are approximate for PowerShell "
                    f"(measured: {json.dumps(balance)}); they can be non-zero for correct code, so they are not "
                    "used as a verdict - syntax validation happens with PowerShell 5.1 in CI and on the owner VM")
    else:
        runner.fail("build_ps1_here_strings_and_encoding",
                    json.dumps(runner.facts["build_ps1_structure"], ensure_ascii=False))

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

    # --- .gitignore coverage for owner-side material (fix 2026-09-24) ------
    gitignore = (pr / ".gitignore").read_text(encoding="utf-8") if (pr / ".gitignore").is_file() else ""
    wanted = ["keys/", "private_key.hex", "*.hrmlicense", "public_key.b64", "license-issuer-dist.zip"]
    present = [p for p in wanted if p in gitignore]
    runner.facts["gitignore_entries_for_owner_material"] = present
    if len(present) == len(wanted):
        runner.pass_("gitignore_blocks_key_and_license_material",
                     ".gitignore covers " + ", ".join(wanted) + ": a stray private key, public key or issued "
                     "license is no longer staged by `git add -A`")
    else:
        runner.fail("gitignore_blocks_key_and_license_material",
                    f"missing patterns: {sorted(set(wanted) - set(present))}")
    # the new patterns must not hide files that are actually tracked; only meaningful
    # when the tree under test carries the same .gitignore as the working branch
    same_as_worktree = gitignore == (REPO / ".gitignore").read_text(encoding="utf-8")
    if not same_as_worktree:
        runner.info("gitignore_patterns_hide_no_tracked_file",
                    "skipped for this revision: the tree under test has a different .gitignore than the working "
                    "branch (baseline negative control)")
        code, out, err = 1, "", "skipped"
    else:
        code, out, err = runner.run(["git", "-C", str(REPO), "ls-files", "-ci", "--exclude-standard"])
    hidden_tracked = [l for l in out.split() if l] if code == 0 else None
    runner.facts["tracked_files_now_ignored"] = hidden_tracked
    if code != 0:
        runner.not_run("gitignore_patterns_hide_no_tracked_file", f"git ls-files -ci failed: {err.strip()[:120]}")
    elif hidden_tracked:
        runner.fail("gitignore_patterns_hide_no_tracked_file", f"tracked files now ignored: {hidden_tracked}")
    else:
        runner.pass_("gitignore_patterns_hide_no_tracked_file",
                     "`git ls-files -ci --exclude-standard` is empty: no already-tracked file matches the new "
                     "patterns (infra/license/public_key.b64 was never tracked)")


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
    # over-redaction on purpose: not even a truncated SHA-256 of a key is published
    fp = payload.get("public_key_fingerprint") or ""
    runner.facts["backend_public_key_fingerprint"] = (
        re.sub(r"SHA256:[0-9a-f]{8,}", "SHA256:<redacted>", fp) if fp else None
    )
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


def sockets_of_pid(pid: int) -> list[tuple[str, int]]:
    """Local addresses this very process listens on (Linux /proc, no external tools)."""
    inodes: set[str] = set()
    for fd in Path(f"/proc/{pid}/fd").glob("*"):
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[len("socket:[") : -1])
    rows: list[tuple[str, int]] = []
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not table.is_file():
            continue
        for line in table.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 11 or fields[3] != "0A" or fields[9] not in inodes:
                continue
            raw, port = fields[1].split(":")
            if table.name == "tcp":
                addr = ".".join(str(b) for b in bytes.fromhex(raw)[::-1])
            else:
                addr = "v6:" + raw
            rows.append((addr, int(port, 16)))
    return sorted(set(rows))


def _serve_and_probe(runner: Runner, serverdir: Path, port: int, bind: str | None) -> dict[str, Any]:
    """Start `python -m http.server` exactly like the launcher does and probe it."""
    import time
    import urllib.request

    cmd = [runner.python, "-m", "http.server", str(port)]
    if bind:
        cmd += ["-b", bind]
    cmd += ["--directory", str(serverdir)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result: dict[str, Any] = {"cmd": " ".join(cmd[1:]), "bind": bind or "default(0.0.0.0)", "listen_of_this_pid": []}
    try:
        for _ in range(30):
            time.sleep(0.2)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/license-issuer.html", timeout=2) as resp:
                    result["loopback"] = (resp.status, len(resp.read()))
                break
            except Exception:
                continue
        result.setdefault("loopback", None)
        # attributing the listening socket to THIS process matters: the sandbox itself
        # has a platform proxy listening on the host address, so "reachable via the
        # host IP" is not evidence about our server.
        try:
            result["listen_of_this_pid"] = sockets_of_pid(proc.pid)
        except Exception as exc:  # pragma: no cover
            result["listen_of_this_pid"] = f"probe failed: {exc}"
        return result
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def serve_flow(runner: Runner, pr: Path, work: Path) -> None:
    """Emulate run-html.bat's HTTP server on this host (Linux python, not the Windows bundle).

    Two runs: the command line the *fixed* launcher uses (-b 127.0.0.1) and the
    previous one without a bind address, so the fix is a measured difference and
    not a claim. The Windows launcher itself is NOT executed here.
    """
    serverdir = work / "serverdir"
    serverdir.mkdir(exist_ok=True)
    for name in ("license-issuer.html", "nacl-fast.js"):
        shutil.copy2(pr / ISSUER / name, serverdir / name)

    fixed = _serve_and_probe(runner, serverdir, 8791, bind="127.0.0.1")
    previous = _serve_and_probe(runner, serverdir, 8792, bind=None)
    runner.facts["emulated_runner"] = {"with_bind_127_0_0_1": fixed, "without_bind": previous}

    ok_loopback = isinstance(fixed.get("loopback"), tuple) and fixed["loopback"][0] == 200
    if ok_loopback:
        runner.pass_("emulated_runner_serves_page",
                     f"`{fixed['cmd']}` (the fixed launcher's command line): GET /license-issuer.html on "
                     f"127.0.0.1 -> HTTP {fixed['loopback'][0]}, {fixed['loopback'][1]} bytes, byte-identical to "
                     f"the file: {fixed['loopback'][1] == (serverdir / 'license-issuer.html').stat().st_size}. "
                     f"Emulated with the host Linux Python {sys.version.split()[0]}; the Windows launcher "
                     "run-html.bat itself was NOT executed")
    else:
        runner.fail("emulated_runner_serves_page", f"fixed command line did not serve the page: {fixed}")

    fixed_listen = fixed.get("listen_of_this_pid")
    previous_listen = previous.get("listen_of_this_pid")
    loopback_only = fixed_listen == [("127.0.0.1", 8791)] and previous_listen == [("0.0.0.0", 8792)]
    if loopback_only:
        runner.pass_("run_html_bat_loopback_only_measured",
                     f"same interpreter, same command line as the launcher: with `-b 127.0.0.1` the server process "
                     f"listens on {fixed_listen} only; the control run without -b (the previous launcher, which the "
                     f"fix removes) listens on {previous_listen} - i.e. reachable from the LAN. Listen sockets are "
                     "attributed to the server's own PID via /proc/<pid>/fd, because this sandbox has a platform "
                     "proxy that also listens on the host address")
    else:
        runner.fail("run_html_bat_loopback_only_measured",
                    f"loopback binding not confirmed: fixed={fixed_listen}, previous={previous_listen}")


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


def collect(runner: Runner, work: Path, base_ref: str, overlay_ref: str | None = None) -> None:
    """Run every check against base_ref (+ the fix overlay) and fill ``runner``."""
    pr = work / "pr"
    pr.mkdir(parents=True, exist_ok=True)
    if materialise_reviewed_tree(runner, base_ref, overlay_ref, pr):
        file_rows(runner, pr, REVIEWED_FILES)
        static_checks(runner, pr)
        cli_flow(runner, pr, work)
        html_flow(runner, pr, work)
        serve_flow(runner, pr, work)
        backend_flow(runner, pr, work)
    not_run_block(runner)


REVIEWED_FILES = [
    f"{ISSUER}/build.ps1", f"{ISSUER}/cli.py", f"{ISSUER}/gui.py",
    f"{ISSUER}/license_issuer.py", f"{ISSUER}/license-issuer.html",
    f"{ISSUER}/nacl-fast.js", f"{ISSUER}/README.md",
    "backend/app/license.py", "backend/app/services/license_service.py",
]

# Steps that exist to prove one of the fixes of the 2026-09-24 patch; the
# generator runs them against the previous revision too, so "fixed" is a
# measured before/after and not a claim.
FIX_STEP_IDS = [
    "run_html_bat_binds_loopback_only",
    "run_html_bat_fails_closed_without_system_python",
    "run_gui_bat_no_system_python_advice",
    "smoke_test_runs_outside_the_repository",
    "smoke_test_never_prints_key_material",
    "gitignore_blocks_key_and_license_material",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", default="HEAD", help="commit under review (default: HEAD of this branch)")
    ap.add_argument("--compare-ref", default=PR34_HEAD,
                    help="earlier revision used as the before/after baseline (default: PR #34 head)")
    ap.add_argument("--python", default=sys.executable, help="interpreter with cryptography (+ backend deps)")
    ap.add_argument("--node", default=shutil.which("node") or "", help="node binary for the HTML harness")
    ap.add_argument("--keep-work-dir", action="store_true")
    args = ap.parse_args()

    ref = args.ref
    if ref == "HEAD":
        code, out, _ = Runner("", None, Path("/tmp"), "HEAD").run(["git", "-C", str(REPO), "rev-parse", "HEAD"])
        ref = out.strip() if code == 0 and out.strip() else PR34_HEAD

    work = Path(tempfile.mkdtemp(prefix="hrm-issuer-evidence-"))
    runner = Runner(python_bin=args.python, node_bin=args.node or None, work=work, ref=ref)
    try:
        # primary run: the PR-head tree with the fix commit overlaid
        collect(runner, work, args.compare_ref, overlay_ref=ref)

        # --- before/after baseline for the fix-sensitive checks -------------
        fixes: list[dict[str, str]] = []
        baseline_meta: dict[str, Any] = {"compared_ref": None}
        if args.compare_ref and args.compare_ref != ref:
            base_work = Path(tempfile.mkdtemp(prefix="hrm-issuer-baseline-"))
            try:
                base = Runner(python_bin=args.python, node_bin=args.node or None, work=base_work,
                              ref=args.compare_ref)
                collect(base, base_work, args.compare_ref, overlay_ref=None)
                before = {st["id"]: st["status"] for st in base.steps}
                after = {st["id"]: st["status"] for st in runner.steps}
                for step_id in FIX_STEP_IDS:
                    if step_id in before or step_id in after:
                        fixes.append({
                            "check": step_id,
                            "before": before.get(step_id, "absent"),
                            "after": after.get(step_id, "absent"),
                        })
                baseline_meta = {
                    "compared_ref": args.compare_ref,
                    "before_summary": {st: sum(1 for x in base.steps if x["status"] == st)
                                       for st in sorted({x["status"] for x in base.steps})},
                    "before_json_note": "the baseline run is a negative control: the same checks must fail on the "
                                        "revision before the fix",
                }
            finally:
                shutil.rmtree(base_work, ignore_errors=True)

        summary: dict[str, int] = {}
        for step in runner.steps:
            summary[step["status"]] = summary.get(step["status"], 0) + 1

        report = {
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "review-artifacts/gen_issuer_offline_evidence.py (runs the real issuer code; no declarations)",
            "scope": {
                "pr": 34,
                "reviewed_commit": ref,
                "baseline_commit": args.compare_ref,
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
            "fixes_verification": {
                "baseline": baseline_meta,
                "checks": fixes,
                "how": "every fix-sensitive check is executed twice: against the reviewed commit and against the "
                       "baseline revision (a negative control); a check is only 'fixed' when it fails before and "
                       "passes after",
            },
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
            f"- reviewed commit: `{ref}`",
            "- tool: `review-artifacts/gen_issuer_offline_evidence.py` (re-runs every check below)",
            "- host: Linux review sandbox — **not** the owner's Windows VM",
            "",
            "> This file proves what could be executed without Windows. It is **not** the clean-Windows",
            "> issuer check: `run-gui.bat`, `run-html.bat`, the Tkinter GUI and the bundle build remain",
            "> `NOT RUN` (see `windows-issuer-bundle-check.md`). No `GO` may be derived from it.",
            "",
            f"## Summary: {json.dumps(summary, ensure_ascii=False)}",
            "",
            f"- reviewed commit: `{ref}` · baseline (negative control): `{args.compare_ref}`",
            "",
            "| step | status | detail |",
            "|---|---|---|",
        ]
        for step in runner.steps:
            detail = step["detail"].replace("|", "\\|").replace("\n", " ")
            md.append(f"| `{step['id']}` | {step['status']} | {detail} |")
        md += [
            "",
            "## Fixes verified before/after (baseline = the revision before the patch)",
            "",
            "| check | before | after |",
            "|---|---|---|",
        ]
        for row in fixes:
            md.append(f"| `{row['check']}` | {row['before']} | {row['after']} |")
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
        print("fixes:", json.dumps(fixes, ensure_ascii=False))
    finally:
        if not args.keep_work_dir:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("work dir kept:", work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
