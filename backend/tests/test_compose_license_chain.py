"""Phase 15 — license public-key chain: the parts that need no Docker.

The authoritative end-to-end check (real ``docker compose``, real images) is
``infra/scripts/compose_pilot_license_chain.py`` in the ``stack`` CI job.
These tests pin down everything around it that can be verified in plain
pytest:

* the env-file line set the script writes is exactly the one the Windows
  engine writes (``Secrets.psm1:Write-HrmPilotEnv``) — same names, same order;
* BOM/CRLF handling of the env file (Windows PowerShell 5.1 output);
* the report is redacted (fingerprints only) and the leak guard trips;
* the ``environment:`` block the pilot overlay maps into the backend, worker
  and backup — interpolated exactly like Compose does for ``${VAR:?}`` /
  ``${VAR:-default}`` — is accepted by ``app.config.Settings`` in
  ``APP_ENV=pilot`` and yields the same key fingerprint (runtime equality,
  emulated interpolation; the real interpolation is the CI step);
* the same block WITHOUT ``LICENSE_PUBLIC_KEY`` is rejected by Settings
  (fail-closed also below Compose).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "infra" / "scripts" / "compose_pilot_license_chain.py"
SECRETS_PSM1 = REPO_ROOT / "infra" / "windows" / "engine" / "Secrets.psm1"
PILOT_OVERLAY = REPO_ROOT / "infra" / "compose.pilot.yml"
DEV_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
BACKEND_DIR = REPO_ROOT / "backend"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("compose_pilot_license_chain", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


chain = _load_script()


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_reset(loader: yaml.Loader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return None


_ComposeLoader.add_constructor("!reset", _construct_reset)

_INTERP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::\?([^}]*)|:-([^}]*))?\}")


def _interpolate(value: str, variables: dict[str, str]) -> str:
    """Compose semantics for the two forms the overlays use."""

    def repl(match: re.Match[str]) -> str:
        name, required_msg, default = match.groups()
        current = variables.get(name, "")
        if required_msg is not None:
            if not current:
                raise KeyError(f"required variable {name} is missing a value: {required_msg}")
            return current
        if default is not None:
            return current if current else default
        return current

    return _INTERP.sub(repl, value)


def _ephemeral_values() -> dict[str, str]:
    return {
        "license_pub": base64.b64encode(secrets.token_bytes(32)).decode(),
        "pg": secrets.token_hex(16),
        "signing": secrets.token_hex(32),
        "bootstrap": secrets.token_hex(16),
        "exchange": secrets.token_hex(16),
        "backup_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "engine_token": secrets.token_hex(16),
        "release_sha": "0" * 40,
        "staging_dir": "/tmp/hrm-staging",
    }


def _env_file_variables(values: dict[str, str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    for line in chain.engine_env_lines(values):
        name, _, raw = line.partition("=")
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"')
        variables[name] = raw
    return variables


def _resolved_service_environment(service: str, variables: dict[str, str]) -> dict[str, str]:
    """Base file + pilot overlay ``environment`` for one service, interpolated."""
    with DEV_COMPOSE.open() as fh:
        base = yaml.load(fh, Loader=_ComposeLoader)
    with PILOT_OVERLAY.open() as fh:
        overlay = yaml.load(fh, Loader=_ComposeLoader)
    merged: dict[str, Any] = {}
    for doc in (base, overlay):
        merged.update(doc["services"].get(service, {}).get("environment", {}) or {})
    return {str(k): _interpolate("" if v is None else str(v), variables) for k, v in merged.items()}


# --------------------------------------------------------------------------
# Env file format == Windows engine writer
# --------------------------------------------------------------------------


def test_script_env_lines_match_secrets_psm1_writer_exactly() -> None:
    text = SECRETS_PSM1.read_text(encoding="utf-8")
    start = text.index("function Write-HrmPilotEnv")
    body = text[start : text.index("Get-HrmEnvFile", start)]
    engine_names = re.findall(r'\("(HRM_[A-Z_]+)=', body)
    assert engine_names, "could not read the engine's pilot.env line list"
    script_names = [line.partition("=")[0] for line in chain.engine_env_lines(_ephemeral_values())]
    assert script_names == engine_names
    assert script_names[-1] == "HRM_LICENSE_PUBLIC_KEY"


def test_engine_writer_emits_license_key_even_when_empty() -> None:
    """Write-HrmPilotEnv writes ``HRM_LICENSE_PUBLIC_KEY=`` unconditionally, so
    a missing owner file yields an EMPTY value — which ``${VAR:?}`` refuses.
    The compose guard therefore covers both 'unset' and 'empty'."""
    text = SECRETS_PSM1.read_text(encoding="utf-8")
    assert '("HRM_LICENSE_PUBLIC_KEY={0}" -f $licensePub)' in text
    assert "function Get-HrmLicensePublicKey" in text
    assert 'return ""' in text[text.index("function Get-HrmLicensePublicKey") :]


# --------------------------------------------------------------------------
# Env file encodings
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("bom", "crlf"), [(False, False), (True, True), (True, False)])
def test_env_file_roundtrip_handles_bom_and_crlf(tmp_path: Path, bom: bool, crlf: bool) -> None:
    values = _ephemeral_values()
    path = tmp_path / "pilot.env"
    chain.write_env(path, chain.engine_env_lines(values), bom=bom, crlf=crlf)
    raw = path.read_bytes()
    assert raw.startswith(chain.UTF8_BOM) is bom
    assert (b"\r\n" in raw) is crlf
    value = chain.parse_env_value(path, "HRM_LICENSE_PUBLIC_KEY")
    assert value == values["license_pub"]
    assert chain.fingerprint(value or "") == chain.fingerprint(values["license_pub"])
    # The first variable must not be polluted by the BOM either.
    assert chain.parse_env_value(path, "HRM_POSTGRES_PASSWORD") == values["pg"]


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def test_fingerprint_is_redacted_and_deterministic() -> None:
    key = base64.b64encode(bytes(range(32))).decode()
    fp = chain.fingerprint(key)
    assert fp == chain.fingerprint(key)
    assert fp.startswith("sha256:") and fp.endswith("…")
    assert len(fp) == len("sha256:") + 16 + 1
    assert key not in fp
    assert fp[7:23] == hashlib.sha256(key.encode()).hexdigest()[:16]


def test_report_leak_guard_masks_key_material(tmp_path: Path) -> None:
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    token = secrets.token_hex(32)
    results = [
        chain.StepResult("ok", "pass", "fine", {"fingerprint": chain.fingerprint(key)}),
        chain.StepResult("oops", "pass", f"echoed {key} and {token}", {"stderr_tail": key}),
    ]
    args = type("Args", (), {"require_runtime": True})()
    code = chain.finish(
        results, tmp_path / "r.json", tmp_path / "r.md", "2.x", time.monotonic(), args
    )
    assert code == 1
    text = (tmp_path / "r.json").read_text(encoding="utf-8")
    assert key not in text and token not in text
    report = json.loads(text)
    assert report["verdict"] == "FAIL"
    assert report["leak_guard_triggered"] is True


def test_report_without_leaks_is_pass_and_has_no_base64_material(tmp_path: Path) -> None:
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    results = [chain.StepResult("ok", "pass", "fine", {"fingerprint": chain.fingerprint(key)})]
    args = type("Args", (), {"require_runtime": True})()
    code = chain.finish(
        results, tmp_path / "r.json", tmp_path / "r.md", "2.x", time.monotonic(), args
    )
    assert code == 0
    text = (tmp_path / "r.json").read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z0-9+/]{43}=", text)
    assert json.loads(text)["verdict"] == "PASS"
    assert "| `ok` | pass |" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_masker_hides_every_ephemeral_value() -> None:
    mask = chain.Masker()
    values = _ephemeral_values()
    mask.add(*(v for k, v in values.items() if k not in ("release_sha", "staging_dir")))
    line = " ".join(values.values())
    masked = mask(line)
    for k, v in values.items():
        if k in ("release_sha", "staging_dir"):
            continue
        assert v not in masked


# --------------------------------------------------------------------------
# Runtime equality below Compose: overlay environment -> Settings
# --------------------------------------------------------------------------


def _run_probe(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", chain.SETTINGS_PROBE],
        cwd=str(BACKEND_DIR),
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(BACKEND_DIR), **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("service", ["backend", "worker", "backup"])
def test_pilot_overlay_environment_satisfies_settings_and_keeps_key_fingerprint(
    service: str,
) -> None:
    values = _ephemeral_values()
    variables = _env_file_variables(values)
    env = _resolved_service_environment(service, variables)
    assert env["APP_ENV"] == "pilot"
    assert env["LICENSE_PUBLIC_KEY"] == values["license_pub"]

    proc = _run_probe(env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    probe = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    assert probe["APP_ENV"] == "pilot"
    assert probe["LICENSE_PUBLIC_KEY_LEN"] == "44"
    expected_sha = hashlib.sha256(values["license_pub"].encode()).hexdigest()
    assert probe["LICENSE_PUBLIC_KEY_SHA256"] == expected_sha
    assert probe["ENV_HAS_HRM_RAW"] == "False"
    if service != "backup":
        assert probe["ENV_HAS_BACKUP_ENC_KEY"] == "False"
    # Nothing but fingerprints/flags leaves the probe.
    assert values["license_pub"] not in proc.stdout


@pytest.mark.parametrize("service", ["backend", "worker", "backup"])
def test_pilot_overlay_refuses_to_render_without_license_key(service: str) -> None:
    values = _ephemeral_values()
    variables = _env_file_variables(values)
    for broken in ("", None):
        vars_broken = dict(variables)
        if broken is None:
            vars_broken.pop("HRM_LICENSE_PUBLIC_KEY")
        else:
            vars_broken["HRM_LICENSE_PUBLIC_KEY"] = broken
        with pytest.raises(KeyError, match="HRM_LICENSE_PUBLIC_KEY is required for the pilot"):
            _resolved_service_environment(service, vars_broken)


def test_settings_reject_pilot_environment_without_license_key() -> None:
    """Even if Compose were bypassed, Settings itself is fail-closed."""
    values = _ephemeral_values()
    env = _resolved_service_environment("backend", _env_file_variables(values))
    env.pop("LICENSE_PUBLIC_KEY")
    proc = _run_probe(env)
    assert proc.returncode != 0
    assert "LICENSE_PUBLIC_KEY must be set in pilot" in proc.stderr
    env["LICENSE_PUBLIC_KEY"] = "not-base64!"
    proc = _run_probe(env)
    assert proc.returncode != 0
    assert "LICENSE_PUBLIC_KEY must be valid base64" in proc.stderr


def test_chain_script_static_steps_pass_without_docker(tmp_path: Path) -> None:
    """The script itself: key -> owner file -> both env encodings pass, and
    without docker compose it reports PARTIAL (exit 2), never PASS."""
    env = {k: v for k, v in os.environ.items() if k not in ("HRM_LICENSE_PUBLIC_KEY",)}
    env["PATH"] = str(tmp_path / "empty-bin")  # no docker on PATH
    (tmp_path / "empty-bin").mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(tmp_path / "out")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    report = json.loads((tmp_path / "out" / "compose-license-chain.json").read_text())
    statuses = {s["id"]: s["status"] for s in report["steps"]}
    assert statuses["ephemeral_public_key"] == "pass"
    assert statuses["public_key_file"] == "pass"
    assert statuses["pilot_env_utf8-lf"] == "pass"
    assert statuses["pilot_env_utf8bom-crlf"] == "pass"
    assert statuses["compose_available"] == "skipped"
    assert report["verdict"].startswith("PARTIAL")
    fps = {s["evidence"].get("fingerprint") for s in report["steps"] if s["evidence"]}
    assert len(fps - {None}) == 1  # one and the same fingerprint at every hop
    proc_required = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(tmp_path / "out2"), "--require-runtime"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc_required.returncode == 1
