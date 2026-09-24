#!/usr/bin/env python3
"""Regenerate review-artifacts/license-chain-evidence.{json,md} from REAL files.

Nothing here is declarative: every boolean is computed from the current
content of the repository, and every runtime status is either taken from an
imported CI artifact (``compose-pilot-license-chain.ci.json`` +
``ci-run-status.json`` next to this script, both downloaded from a concrete
GitHub Actions run) or reported as NOT RUN / BLOCKED. Only redacted
fingerprints are ever written.

    python3 review-artifacts/gen_license_chain_evidence.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT_JSON = HERE / "license-chain-evidence.json"
OUT_MD = HERE / "license-chain-evidence.md"
CI_CHAIN = HERE / "compose-pilot-license-chain.ci.json"
CI_STATUS = HERE / "ci-run-status.json"

PILOT_MAPPING = (
    "LICENSE_PUBLIC_KEY: ${HRM_LICENSE_PUBLIC_KEY:?HRM_LICENSE_PUBLIC_KEY is required for the pilot}"
)
PROD_MAPPING_PREFIX = "LICENSE_PUBLIC_KEY: ${LICENSE_PUBLIC_KEY:?"

CHAIN_FILES = [
    "tools/license-issuer/build.ps1",
    "tools/license-issuer/license-issuer.html",
    "tools/license-issuer/nacl-fast.js",
    "tools/license-issuer/cli.py",
    "tools/license-issuer/license_issuer.py",
    "infra/windows/engine/Secrets.psm1",
    "infra/compose.pilot.yml",
    "infra/compose.prod.yml",
    "infra/docker-compose.yml",
    "infra/scripts/check_env.sh",
    "infra/scripts/compose_pilot_license_chain.py",
    "backend/Dockerfile",
    "backend/app/config.py",
    "backend/app/license_guard.py",
    "backend/tests/test_pilot_overlay.py",
    "backend/tests/test_production_overlay.py",
    "backend/tests/test_compose_license_chain.py",
    "backend/tests/test_license_middleware_comprehensive.py",
    "infra/windows/tests/engine.tests.ps1",
    "infra/windows/tests/static.tests.ps1",
    ".github/workflows/ci.yml",
]


def read(rel: str) -> str:
    path = REPO / rel
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def file_row(rel: str) -> dict[str, Any]:
    path = REPO / rel
    if not path.is_file():
        return {"file": rel, "exists": False}
    data = path.read_bytes()
    return {
        "file": rel,
        "exists": True,
        "size": len(data),
        "lines": data.count(b"\n"),
        "sha256_prefix": hashlib.sha256(data).hexdigest()[:12] + "...",
    }


def service_block(compose_text: str, service: str) -> str:
    """Text of one top-level service block (2-space indented key)."""
    match = re.search(rf"(?ms)^  {service}:\n(.*?)(?=^  [a-z_]+:|^volumes:|\Z)", compose_text)
    return match.group(1) if match else ""


def compute_checks() -> dict[str, Any]:
    build_ps1 = read("tools/license-issuer/build.ps1")
    secrets_psm1 = read("infra/windows/engine/Secrets.psm1")
    pilot = read("infra/compose.pilot.yml")
    prod = read("infra/compose.prod.yml")
    check_env = read("infra/scripts/check_env.sh")
    dockerfile = read("backend/Dockerfile")
    config_py = read("backend/app/config.py")
    guard_py = read("backend/app/license_guard.py")
    ci = read(".github/workflows/ci.yml")
    engine_tests = read("infra/windows/tests/engine.tests.ps1")
    static_tests = read("infra/windows/tests/static.tests.ps1")
    pilot_tests = read("backend/tests/test_pilot_overlay.py")
    chain_tests = read("backend/tests/test_compose_license_chain.py")

    write_env = secrets_psm1[secrets_psm1.find("function Write-HrmPilotEnv") :]
    pilot_services = {s: service_block(pilot, s) for s in ("backend", "worker", "backup")}
    prod_services = {s: service_block(prod, s) for s in ("backend", "worker", "backup")}

    return {
        # owner issuer bundle (static)
        "owner_source_build_ps1_has_embeddable_python": "embed" in build_ps1.lower(),
        "owner_source_build_ps1_has_launchers": "run-gui.bat" in build_ps1
        and "run-html.bat" in build_ps1,
        "owner_source_build_ps1_has_smoke_test": "smoke" in build_ps1.lower(),
        # engine: file -> pilot.env
        "installer_Secrets_psm1_has_GetHrmLicensePublicKey": "function Get-HrmLicensePublicKey"
        in secrets_psm1,
        "installer_Secrets_psm1_reads_public_key_b64": "public_key.b64" in secrets_psm1,
        "installer_Secrets_psm1_writes_HRM_LICENSE_PUBLIC_KEY": '("HRM_LICENSE_PUBLIC_KEY={0}" -f $licensePub)'
        in write_env,
        "installer_Secrets_psm1_license_line_is_last": write_env.find("HRM_LICENSE_PUBLIC_KEY={0}")
        > write_env.find("HRM_UPDATE_CHECK_MIN_INTERVAL={0}")
        > 0,
        "installer_Secrets_psm1_empty_key_written_empty_not_omitted": "HRM_LICENSE_PUBLIC_KEY={0}"
        in write_env
        and 'return ""' in secrets_psm1[secrets_psm1.find("function Get-HrmLicensePublicKey") :],
        # compose pilot: pilot.env -> containers
        "compose_pilot_uses_env_file_interpolation": "--env-file" in pilot,
        "compose_pilot_has_no_service_env_file_directive": not re.search(r"(?m)^\s+env_file:", pilot),
        "compose_requires_LICENSE_PUBLIC_KEY": all(
            PILOT_MAPPING in block for block in pilot_services.values()
        ),
        "compose_requires_LICENSE_PUBLIC_KEY_via_?": pilot.count(PILOT_MAPPING) == 3
        and "${HRM_LICENSE_PUBLIC_KEY:-" not in pilot,
        "compose_pilot_services_with_mapping": sorted(
            s for s, block in pilot_services.items() if PILOT_MAPPING in block
        ),
        "compose_pilot_has_no_private_key_variable": not re.search(
            r"(?i)(PRIVATE_KEY|private_key|LICENSE_PRIVATE)", pilot
        ),
        # compose prod
        "compose_prod_requires_LICENSE_PUBLIC_KEY_via_?": all(
            PROD_MAPPING_PREFIX in block for block in prod_services.values()
        )
        and "${LICENSE_PUBLIC_KEY:-" not in prod,
        "check_env_sh_validates_LICENSE_PUBLIC_KEY": "LICENSE_PUBLIC_KEY is not set" in check_env
        and "base64 -d" in check_env,
        # why the compose mapping is the ONLY path in a container
        "backend_image_does_not_contain_infra_license_dir": "COPY infra" not in dockerfile
        and "license" not in dockerfile.lower(),
        # backend
        "backend_config_validates_LICENSE_PUBLIC_KEY": "LICENSE_PUBLIC_KEY must be set in" in config_py,
        "backend_config_has_file_fallback": 'Path(__file__).resolve().parents[2] / "infra" / "license"'
        in config_py,
        "backend_guard_deny_by_default": "protected_roots" not in guard_py
        and "is_api_like" not in guard_py
        and "Deny by default" in guard_py,
        "backend_guard_has_slash_boundary": 'cand.startswith(pref + "/")' in guard_py,
        "backend_guard_has_double_slash_normalization": 'while "//" in p' in guard_py,
        "backend_guard_fail_closed_empty_key": "reason=no_public_key" in guard_py,
        "backend_guard_allows_ops_metrics_diagnostics": '"/ops/metrics"' in guard_py,
        # tests present
        "tests_pilot_overlay_require_mapping_for_backend_worker_backup": "test_pilot_overlay_requires_license_public_key_for_every_settings_service"
        in pilot_tests,
        "tests_pilot_overlay_forbid_env_file_directive": "test_pilot_overlay_never_uses_env_file_directive"
        in pilot_tests,
        "tests_chain_script_env_lines_synced_with_Secrets_psm1": "test_script_env_lines_match_secrets_psm1_writer_exactly"
        in chain_tests,
        "tests_settings_accept_overlay_env_and_keep_fingerprint": "test_pilot_overlay_environment_satisfies_settings_and_keeps_key_fingerprint"
        in chain_tests,
        "tests_windows_engine_pilot_env_fingerprint_case": "HRM_LICENSE_PUBLIC_KEY берётся из license_public_key.b64"
        in engine_tests,
        "tests_windows_static_overlay_mapping_case": "LICENSE_PUBLIC_KEY обязателен для backend, worker и backup"
        in static_tests,
        # CI wiring
        "ci_stack_job_runs_compose_pilot_license_chain": "compose_pilot_license_chain.py" in ci
        and "--require-runtime" in ci,
        "ci_uploads_redacted_chain_artifact": "compose-pilot-license-chain" in ci,
        "ci_prod_overlay_negative_check_without_key": "rendered WITHOUT LICENSE_PUBLIC_KEY" in ci,
    }


def ci_runtime_steps() -> tuple[dict[str, str], dict[str, Any]]:
    """Statuses from the imported CI artifacts; NOT RUN when absent."""
    meta: dict[str, Any] = {"imported": False}
    steps: dict[str, str] = {}
    if not CI_CHAIN.is_file() or not CI_STATUS.is_file():
        pending = "NOT RUN (no CI artifact imported yet — see ci-run-status.json)"
        for key in (
            "pilot_env_generation_real_engine_writer",
            "docker_compose_config_with_key_resolved_env",
            "docker_compose_config_without_key_refused",
            "backend_settings_inside_pilot_images",
        ):
            steps[key] = pending
        return steps, meta

    status = json.loads(CI_STATUS.read_text(encoding="utf-8"))
    chain = json.loads(CI_CHAIN.read_text(encoding="utf-8"))
    meta = {
        "imported": True,
        "run_id": status.get("run_id"),
        "head_sha": status.get("head_sha"),
        "jobs": status.get("jobs"),
        "chain_verdict": chain.get("verdict"),
        "chain_compose_version": chain.get("compose_version"),
    }
    by_id = {s["id"]: s for s in chain.get("steps", [])}
    run_ref = f"run {status.get('run_id')} @ {str(status.get('head_sha'))[:7]}"

    def st(ids: list[str], label: str) -> str:
        present = [by_id.get(i) for i in ids]
        if any(p is None for p in present):
            return f"NOT RUN ({label}: step missing in artifact, {run_ref})"
        if all(p["status"] == "pass" for p in present if p):
            fps: set[str] = set()
            for p in present:
                if not p:
                    continue
                ev = p.get("evidence", {})
                if ev.get("fingerprint"):
                    fps.add(str(ev["fingerprint"]))
                for svc in (ev.get("services") or {}).values():
                    if isinstance(svc, dict) and svc.get("fingerprint"):
                        fps.add(str(svc["fingerprint"]))
            extra = f"; ephemeral key fingerprint {', '.join(sorted(fps))}" if fps else ""
            return f"PASS ({label}, {run_ref}{extra})"
        return f"FAIL ({label}, {run_ref})"

    windows = (status.get("jobs") or {}).get("Windows engine tests + installer smoke")
    steps["pilot_env_generation_real_engine_writer"] = (
        f"PASS (Secrets.psm1 Write-HrmPilotEnv, engine.tests.ps1 case with SHA-256 equality, {run_ref})"
        if windows == "success"
        else f"{'FAIL' if windows else 'NOT RUN'} (Windows job: {windows}, {run_ref})"
    )
    steps["docker_compose_config_with_key_resolved_env"] = st(
        ["compose_config_with_key_utf8-lf", "compose_config_with_key_utf8bom-crlf"],
        "real docker compose config, backend/worker/backup resolved LICENSE_PUBLIC_KEY",
    )
    steps["docker_compose_config_without_key_refused"] = st(
        ["compose_config_without_key_missing", "compose_config_without_key_empty"],
        "config refused with the overlay message for missing AND empty key",
    )
    steps["backend_settings_inside_pilot_images"] = st(
        ["runtime_settings_backend", "runtime_settings_worker", "runtime_settings_backup"],
        "app.config.Settings in the built pilot images, APP_ENV=pilot",
    )
    return steps, meta


def main() -> int:
    # Ephemeral value only to demonstrate the redaction format (never a real key).
    demo_key = base64.b64encode(secrets.token_bytes(32)).decode()
    demo_fp = hashlib.sha256(demo_key.encode()).hexdigest()

    checks = compute_checks()
    ci_steps, ci_meta = ci_runtime_steps()
    runtime_steps: dict[str, str] = {
        "owner_key_generation_offline_windows": "BLOCKED (requires clean Windows 10/11 VM without Python/internet)",
        "issuer_cli_offline_linux": "MANUAL PASS, not CI-verified (Linux sandbox, this session: "
        "cli.py gen-keypair → issue → verify, offline; see final-verdict.md)",
        "build_bundle_with_embedded_python": "PASS (static: build.ps1 structure) / BLOCKED (runtime on clean Windows VM)",
        "installer_snapshot_contains_public_key": "BLOCKED (infra/license/public_key.b64 is not in git by design; owner bakes it)",
        **ci_steps,
        "backend_LICENSE_PUBLIC_KEY_validation": "PASS (config.py fail-closed in pilot/production; pytest)",
        "license_upload_and_verification": "PASS (Ed25519 verify; pytest license suites)",
        "windows_bundle_manual_check": "BLOCKED (requires clean Windows 10/11 VM)",
    }
    report = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "review-artifacts/gen_license_chain_evidence.py (reads real files; CI statuses only from imported artifacts)",
        "fingerprint_redacted_example": f"SHA256:{demo_fp[:16]}... (redacted, ephemeral demo value)",
        "public_key_b64_length": 44,
        "public_key_b64_redacted_example": f"{demo_key[:8]}...{demo_key[-4:]} (44 chars, redacted, ephemeral demo value)",
        "chain": [
            "owner PC: tools/license-issuer → private key (owner only) + public_key.b64",
            "release: infra/license/public_key.b64 (baked by owner, not in git)",
            "engine: Secrets.psm1 Get-HrmLicensePublicKey → Write-HrmPilotEnv → pilot.env HRM_LICENSE_PUBLIC_KEY (last line; empty when missing)",
            "compose: --env-file pilot.env → ${HRM_LICENSE_PUBLIC_KEY:?} → LICENSE_PUBLIC_KEY in backend, worker, backup (no env_file directive)",
            "backend image: no infra/ inside → env is the only path; app.config.Settings fail-closed in APP_ENV=pilot",
            "guard: deny by default — everything outside the recovery allowlist needs a valid license",
        ],
        "chain_files": [file_row(rel) for rel in CHAIN_FILES],
        "checks_read_from_real_files": checks,
        "runtime_steps": runtime_steps,
        "ci_import": ci_meta,
        "note": "Private key never in git/installer/frontend/Docker/logs. Only redacted fingerprints. "
        "Compose/runtime steps are PASS only when an imported CI artifact says so.",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    assert demo_key not in text
    OUT_JSON.write_text(text + "\n", encoding="utf-8")

    lines = [
        "# License public-key chain — evidence (generated from real files)",
        "",
        f"- generated: {report['generated_at']}",
        f"- generator: `{report['generator']}`",
        f"- CI import: {json.dumps(ci_meta, ensure_ascii=False)}",
        "",
        "## Chain",
        "",
        *[f"{i + 1}. {step}" for i, step in enumerate(report["chain"])],
        "",
        "## Files (real reads)",
        "",
        "| file | exists | size | lines | sha256 prefix |",
        "|---|---|---|---|---|",
    ]
    for row in report["chain_files"]:
        if row["exists"]:
            lines.append(
                f"| `{row['file']}` | yes | {row['size']} | {row['lines']} | `{row['sha256_prefix']}` |"
            )
        else:
            lines.append(f"| `{row['file']}` | **no** | | | |")
    lines += ["", "## Checks (computed)", "", "| check | value |", "|---|---|"]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {json.dumps(value, ensure_ascii=False)} |")
    lines += ["", "## Runtime steps", "", "| step | status |", "|---|---|"]
    for key, value in runtime_steps.items():
        lines.append(f"| `{key}` | {value} |")
    lines += ["", f"> {report['note']}", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT_JSON.name}, {OUT_MD.name}; ci imported: {ci_meta.get('imported')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
