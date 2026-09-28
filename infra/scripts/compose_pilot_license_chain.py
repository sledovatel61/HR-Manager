#!/usr/bin/env python3
"""Pilot overlay — license public-key chain check (Phase 15).

Proves, with the REAL ``docker compose`` and the REAL overlay files, that the
license public key travels intact along the documented chain and that the
chain is fail-closed:

    public_key.b64  ->  pilot.env (engine format)  ->  docker compose
    (interpolation of ${HRM_LICENSE_PUBLIC_KEY:?})  ->  resolved service
    environment  ->  app.config.Settings inside the running image

Every hop is compared by SHA-256 fingerprint of the exact text that is
transported; the key value itself is never written to the report or the
log. Negative cases: an env file WITHOUT the variable and one with an EMPTY
value must both make ``docker compose config`` refuse with the message from
the overlay.

The engine's own writer (``Write-HrmPilotEnv`` in Secrets.psm1) is
exercised by the Windows job (engine.tests.ps1); this script writes the env
file in the same line format, in both encodings the pilot can produce:
UTF-8 without BOM / LF and — as Windows PowerShell 5.1 ``Set-Content
-Encoding UTF8`` does — UTF-8 WITH BOM / CRLF.

No Docker daemon -> runtime steps are ``skipped`` (exit 2) unless
``--require-runtime`` is given, in which case that is a failure (CI).

All ephemeral values (key, passwords, tokens) are generated per run and
masked in every captured string.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("infra/docker-compose.yml", "infra/compose.pilot.yml")
PILOT_PROJECT = "hr-manager-pilot"
REQUIRED_MESSAGE = "HRM_LICENSE_PUBLIC_KEY is required for the pilot"
SETTINGS_SERVICES = ("backend", "worker", "backup")
UTF8_BOM = b"\xef\xbb\xbf"

# Executed INSIDE the built images: prints only fingerprints/flags.
SETTINGS_PROBE = (
    "import hashlib, os\n"
    "from app.config import get_settings\n"
    "s = get_settings()\n"
    "k = s.license_public_key or ''\n"
    "print('APP_ENV=' + s.environment)\n"
    "print('LICENSE_PUBLIC_KEY_LEN=%d' % len(k))\n"
    "print('LICENSE_PUBLIC_KEY_SHA256=' + hashlib.sha256(k.encode()).hexdigest())\n"
    "print('ENV_HAS_BACKUP_ENC_KEY=%s' % ('BACKUP_ENC_KEY' in os.environ))\n"
    "print('ENV_HAS_HRM_RAW=%s' % any(n.startswith('HRM_') for n in os.environ))\n"
)


def fingerprint(text: str) -> str:
    """Redacted SHA-256 fingerprint of the exact transported text."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] + "…"


def full_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class StepResult:
    id: str
    status: str  # pass | fail | skipped
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    mandatory: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "mandatory": self.mandatory,
            "summary": self.summary,
            "evidence": self.evidence,
        }


class Masker:
    def __init__(self) -> None:
        self._values: list[str] = []

    def add(self, *values: str) -> None:
        for value in values:
            if value and len(value) >= 8:
                self._values.append(value)

    def __call__(self, text: str) -> str:
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, "***")
        return text


def which_compose() -> list[str] | None:
    docker = shutil.which("docker")
    if docker:
        proc = subprocess.run(
            [docker, "compose", "version"], capture_output=True, text=True, check=False
        )
        if proc.returncode == 0:
            return [docker, "compose"]
    legacy = shutil.which("docker-compose")
    if legacy:
        return [legacy]
    return None


def daemon_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    proc = subprocess.run([docker, "info"], capture_output=True, text=True, check=False)
    return proc.returncode == 0


def clean_env() -> dict[str, str]:
    """Process environment without any license variable: the ONLY source of
    HRM_LICENSE_PUBLIC_KEY during the checks must be the env file."""
    env = dict(os.environ)
    for name in ("HRM_LICENSE_PUBLIC_KEY", "LICENSE_PUBLIC_KEY"):
        env.pop(name, None)
    return env


def run(
    cmd: list[str], *, timeout: int = 300, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env or clean_env(),
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def engine_env_lines(values: dict[str, str]) -> list[str]:
    """Exactly the line set and order of Secrets.psm1:Write-HrmPilotEnv."""
    keys_json = "[]"
    return [
        f"HRM_POSTGRES_PASSWORD={values['pg']}",
        f"HRM_SIGNING_KEY={values['signing']}",
        f"HRM_BOOTSTRAP_ADMIN_PASSWORD={values['bootstrap']}",
        f"HRM_EXCHANGE_TOKEN={values['exchange']}",
        f"HRM_BACKUP_KEY={values['backup_key']}",
        "HRM_BACKUP_KEY_ID=ci-chain-1",
        f"HRM_RELEASE_SHA={values['release_sha']}",
        "HRM_PILOT_PORT=8080",
        f"HRM_UPDATE_ENGINE_TOKEN={values['engine_token']}",
        f"HRM_STAGING_DIR={values['staging_dir']}",
        "HRM_UPDATE_CHANNEL_URL=",
        'HRM_UPDATE_CHANNEL_PUBLIC_KEYS="' + keys_json.replace('"', '\\"') + '"',
        "HRM_UPDATE_CHECK_MIN_INTERVAL=300",
        f"HRM_LICENSE_PUBLIC_KEY={values['license_pub']}",
    ]


def write_env(path: Path, lines: list[str], *, bom: bool, crlf: bool) -> None:
    newline = "\r\n" if crlf else "\n"
    data = (newline.join(lines) + newline).encode("utf-8")
    if bom:
        data = UTF8_BOM + data
    path.write_bytes(data)


def parse_env_value(path: Path, name: str) -> str | None:
    raw = path.read_bytes()
    if raw.startswith(UTF8_BOM):
        raw = raw[len(UTF8_BOM) :]
    for line in raw.decode("utf-8").splitlines():
        if line.startswith(name + "="):
            return line[len(name) + 1 :]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", default="compose-license-chain")
    parser.add_argument("--json-out", default="")
    parser.add_argument(
        "--require-runtime",
        action="store_true",
        help="fail (instead of skip) when the Docker daemon is unavailable",
    )
    parser.add_argument(
        "--skip-runtime", action="store_true", help="only static + `config` checks"
    )
    parser.add_argument("--keep", action="store_true", help="keep the temp dir (debug)")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = Path(args.json_out) if args.json_out else out_dir / "compose-license-chain.json"
    md_path = out_dir / "compose-license-chain.md"

    mask = Masker()
    results: list[StepResult] = []
    started = time.monotonic()

    def record(step: StepResult) -> None:
        step.summary = mask(step.summary)
        step.evidence = {
            k: (mask(v) if isinstance(v, str) else v) for k, v in step.evidence.items()
        }
        results.append(step)
        print(f"[{step.status}] {step.id}: {step.summary}", flush=True)

    compose = which_compose()
    tmp = Path(tempfile.mkdtemp(prefix="hrm-license-chain-"))
    compose_version = ""
    if compose:
        _, out, _ = run([*compose, "version", "--short"], timeout=30)
        compose_version = out.strip()

    try:
        # ---- 1. ephemeral public key (never printed) -------------------------
        license_pub = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        assert len(license_pub) == 44
        key_fp = fingerprint(license_pub)
        key_sha = full_sha256(license_pub)
        values = {
            "license_pub": license_pub,
            "pg": secrets.token_hex(16),
            "signing": secrets.token_hex(32),
            "bootstrap": secrets.token_hex(16),
            "exchange": secrets.token_hex(16),
            "backup_key": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
            "engine_token": secrets.token_hex(16),
            "release_sha": "0" * 40,
            "staging_dir": str(tmp / "staging"),
        }
        mask.add(*(v for k, v in values.items() if k not in ("release_sha", "staging_dir")))
        (tmp / "staging").mkdir(parents=True, exist_ok=True)
        record(
            StepResult(
                "ephemeral_public_key",
                "pass",
                "32 random bytes, base64 44 chars (ephemeral, never persisted)",
                {"fingerprint": key_fp, "length": len(license_pub)},
            )
        )

        # ---- 2. owner file: infra/license/public_key.b64 ---------------------
        key_file = tmp / "release" / "infra" / "license" / "public_key.b64"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes((license_pub + "\r\n").encode("ascii"))  # owner PC: CRLF
        read_back = key_file.read_text(encoding="utf-8").strip()  # Secrets.psm1: .Trim()
        record(
            StepResult(
                "public_key_file",
                "pass" if fingerprint(read_back) == key_fp else "fail",
                "infra/license/public_key.b64 written (CRLF) and read back trimmed",
                {"fingerprint": fingerprint(read_back), "bytes": key_file.stat().st_size},
            )
        )
        values["license_pub"] = read_back

        # ---- 3. pilot.env in the engine's format, both encodings ------------
        state = tmp / "state"
        state.mkdir()
        lines = engine_env_lines(values)
        env_plain = state / "pilot.env"
        env_bom = state / "pilot.bom.env"
        write_env(env_plain, lines, bom=False, crlf=False)
        write_env(env_bom, lines, bom=True, crlf=True)
        for path, label in ((env_plain, "utf8-lf"), (env_bom, "utf8bom-crlf")):
            value = parse_env_value(path, "HRM_LICENSE_PUBLIC_KEY") or ""
            value = value.rstrip("\r")
            ok = fingerprint(value) == key_fp
            record(
                StepResult(
                    f"pilot_env_{label}",
                    "pass" if ok else "fail",
                    f"HRM_LICENSE_PUBLIC_KEY line present as the last line ({label})",
                    {
                        "fingerprint": fingerprint(value),
                        "line_count": len(lines),
                        "last_line_is_license": lines[-1].startswith("HRM_LICENSE_PUBLIC_KEY="),
                    },
                )
            )

        if compose is None:
            record(
                StepResult(
                    "compose_available",
                    "fail" if args.require_runtime else "skipped",
                    "docker compose not found — interpolation checks cannot run here",
                )
            )
            return finish(results, json_path, md_path, compose_version, started, args)

        record(
            StepResult(
                "compose_available",
                "pass",
                f"docker compose {compose_version}",
                {"version": compose_version},
            )
        )

        def base(env_file: Path) -> list[str]:
            cmd = [*compose, "-p", PILOT_PROJECT]
            for f in COMPOSE_FILES:
                cmd.extend(["-f", f])
            cmd.extend(["--env-file", str(env_file)])
            return cmd

        # ---- 4. config WITH key: resolved environment per service -----------
        for env_file, label in ((env_plain, "utf8-lf"), (env_bom, "utf8bom-crlf")):
            code, out, err = run([*base(env_file), "config", "--format", "json"], timeout=120)
            if code != 0:
                record(
                    StepResult(
                        f"compose_config_with_key_{label}",
                        "fail",
                        f"docker compose config exit {code}",
                        {"stderr_tail": err[-800:]},
                    )
                )
                continue
            rendered = json.loads(out)
            services = rendered.get("services", {})
            per_service: dict[str, Any] = {}
            ok = True
            for name in SETTINGS_SERVICES:
                env = services.get(name, {}).get("environment", {}) or {}
                value = str(env.get("LICENSE_PUBLIC_KEY", ""))
                fp = fingerprint(value)
                match = fp == key_fp and len(value) == 44
                app_env = env.get("APP_ENV")
                per_service[name] = {
                    "fingerprint": fp,
                    "matches_public_key_file": match,
                    "APP_ENV": app_env,
                    "has_env_file_directive": "env_file" in services.get(name, {}),
                }
                ok = ok and match and app_env == "pilot"
            backend_env = services.get("backend", {}).get("environment", {}) or {}
            leak_names = sorted(
                n
                for n in backend_env
                if n in ("BACKUP_ENC_KEY", "BACKUP_KEY_ID") or n.startswith("HRM_")
            )
            leak_values = values["backup_key"] in " ".join(str(v) for v in backend_env.values())
            env_file_anywhere = sorted(n for n, s in services.items() if "env_file" in s)
            ok = ok and not leak_names and not leak_values and not env_file_anywhere
            record(
                StepResult(
                    f"compose_config_with_key_{label}",
                    "pass" if ok else "fail",
                    "resolved LICENSE_PUBLIC_KEY of backend/worker/backup equals the "
                    "public_key.b64 fingerprint; backend gets no backup key; no env_file",
                    {
                        "services": per_service,
                        "backend_forbidden_names": leak_names,
                        "backend_has_backup_key_value": leak_values,
                        "services_with_env_file": env_file_anywhere,
                    },
                )
            )

        # ---- 5. config WITHOUT key / with EMPTY key must refuse --------------
        negative_cases = {
            "missing": [ln for ln in lines if not ln.startswith("HRM_LICENSE_PUBLIC_KEY=")],
            "empty": [
                ("HRM_LICENSE_PUBLIC_KEY=" if ln.startswith("HRM_LICENSE_PUBLIC_KEY=") else ln)
                for ln in lines
            ],
        }
        for label, neg_lines in negative_cases.items():
            env_file = state / f"pilot.{label}.env"
            write_env(env_file, neg_lines, bom=False, crlf=False)
            code, out, err = run([*base(env_file), "config", "-q"], timeout=120)
            combined = out + "\n" + err
            refused = code != 0 and REQUIRED_MESSAGE in combined
            record(
                StepResult(
                    f"compose_config_without_key_{label}",
                    "pass" if refused else "fail",
                    f"HRM_LICENSE_PUBLIC_KEY {label}: docker compose config exit {code}"
                    + (" with the overlay's required-message" if refused else ""),
                    {
                        "exit_code": code,
                        "message_present": REQUIRED_MESSAGE in combined,
                        "stderr_tail": err.strip()[-300:],
                    },
                )
            )

        # ---- 6. runtime: Settings inside the images ------------------------
        if args.skip_runtime:
            record(StepResult("runtime_settings", "skipped", "--skip-runtime"))
            return finish(results, json_path, md_path, compose_version, started, args)
        if not daemon_available():
            record(
                StepResult(
                    "runtime_settings",
                    "fail" if args.require_runtime else "skipped",
                    "Docker daemon unavailable — cannot start the images",
                )
            )
            return finish(results, json_path, md_path, compose_version, started, args)

        runtime_env_file = env_bom  # the realistic Windows file
        try:
            for name in SETTINGS_SERVICES:
                code, out, err = run(
                    [*base(runtime_env_file), "build", name], timeout=1800
                )
                if code != 0:
                    record(
                        StepResult(
                            f"runtime_settings_{name}",
                            "fail",
                            f"image build failed (exit {code})",
                            {"stderr_tail": err[-800:]},
                        )
                    )
                    continue
                cmd = [*base(runtime_env_file), "run", "--rm", "--no-deps", "-T"]
                if name == "backup":
                    # The backup image's ENTRYPOINT is the scheduler script.
                    cmd.extend(["--entrypoint", "python", name, "-c", SETTINGS_PROBE])
                else:
                    cmd.extend([name, "python", "-c", SETTINGS_PROBE])
                code, out, err = run(cmd, timeout=600)
                probe = dict(
                    line.split("=", 1) for line in out.splitlines() if "=" in line
                )
                sha_ok = probe.get("LICENSE_PUBLIC_KEY_SHA256") == key_sha
                ok = (
                    code == 0
                    and probe.get("APP_ENV") == "pilot"
                    and probe.get("LICENSE_PUBLIC_KEY_LEN") == "44"
                    and sha_ok
                    and probe.get("ENV_HAS_HRM_RAW") == "False"
                    and (name == "backup" or probe.get("ENV_HAS_BACKUP_ENC_KEY") == "False")
                )
                record(
                    StepResult(
                        f"runtime_settings_{name}",
                        "pass" if ok else "fail",
                        f"{name}: app.config.Settings loaded in APP_ENV=pilot; "
                        "license_public_key fingerprint equals public_key.b64",
                        {
                            "exit_code": code,
                            "APP_ENV": probe.get("APP_ENV"),
                            "license_public_key_len": probe.get("LICENSE_PUBLIC_KEY_LEN"),
                            "fingerprint": (
                                "sha256:" + probe.get("LICENSE_PUBLIC_KEY_SHA256", "")[:16] + "…"
                            ),
                            "matches_public_key_file": sha_ok,
                            "container_env_has_backup_enc_key": probe.get(
                                "ENV_HAS_BACKUP_ENC_KEY"
                            ),
                            "container_env_has_raw_HRM_vars": probe.get("ENV_HAS_HRM_RAW"),
                            "stderr_tail": err.strip()[-300:] if code != 0 else "",
                        },
                    )
                )
        finally:
            run([*base(runtime_env_file), "down", "-v", "--remove-orphans"], timeout=300)
        return finish(results, json_path, md_path, compose_version, started, args)
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


def finish(
    results: list[StepResult],
    json_path: Path,
    md_path: Path,
    compose_version: str,
    started: float,
    args: argparse.Namespace,
) -> int:
    failed = [r for r in results if r.status == "fail" and r.mandatory]
    skipped = [r for r in results if r.status == "skipped"]
    if failed:
        verdict = "FAIL"
        code = 1
    elif skipped:
        verdict = "PARTIAL (runtime skipped)"
        code = 2
    else:
        verdict = "PASS"
        code = 0
    report = {
        "schema": 1,
        "title": "Pilot overlay: license public-key chain (public_key.b64 -> pilot.env "
        "-> docker compose -> resolved environment -> Settings)",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "compose_version": compose_version,
        "compose_files": list(COMPOSE_FILES),
        "verdict": verdict,
        "redaction": "only SHA-256 fingerprints (16 hex) of the transported text; "
        "no key material, no secrets, no rendered config is stored",
        "duration_seconds": round(time.monotonic() - started, 1),
        "steps": [r.as_dict() for r in results],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    # Belt and braces: 32-byte base64 material (44 chars) or a 32+ hex token
    # must never reach the report even if a message unexpectedly echoed one.
    leak_pattern = re.compile(r"[A-Za-z0-9+/]{43}=|(?<![0-9a-f])[0-9a-f]{32,}(?![0-9a-f])")
    if leak_pattern.search(text):
        report["verdict"] = verdict = "FAIL"
        report["leak_guard_triggered"] = True
        code = 1
        text = leak_pattern.sub("***", json.dumps(report, ensure_ascii=False, indent=2))
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(text + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    print(f"verdict: {verdict} -> {json_path}", flush=True)
    return code


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['title']}",
        "",
        f"- generated: {report['generated_at']}",
        f"- docker compose: `{report['compose_version'] or 'n/a'}`",
        f"- verdict: **{report['verdict']}**",
        f"- redaction: {report['redaction']}",
        "",
        "| step | status | summary | fingerprint / evidence |",
        "|---|---|---|---|",
    ]
    for step in report["steps"]:
        ev = step["evidence"]
        fp = ev.get("fingerprint")
        if fp is None and "services" in ev:
            fp = "; ".join(f"{n}: {d['fingerprint']}" for n, d in ev["services"].items())
        if fp is None:
            fp = ", ".join(
                f"{k}={v}" for k, v in ev.items() if k not in ("stderr_tail",) and v != ""
            )
        lines.append(f"| `{step['id']}` | {step['status']} | {step['summary']} | {fp} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
