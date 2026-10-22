#!/usr/bin/env python3
"""Read-only proof of pilot port merging with the REAL Docker Compose CLI.

No up/down, containers or volumes. All env values are ephemeral. Only ports,
input file hashes and verdicts are printed/saved; never rendered environment.
This is a configuration check, NOT Windows acceptance on an existing volume.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def check(install_dir: Path) -> dict[str, Any]:
    base = install_dir / "infra/docker-compose.yml"
    pilot = install_dir / "infra/compose.pilot.yml"
    text = pilot.read_text(encoding="utf-8-sig")
    # Negative control: both frontend and backend must inherit dev publications
    # without !override; the positive cases must replace both sequences.
    before = text.replace("ports: !override", "ports:")
    if before == text:
        raise RuntimeError("pilot !override is missing")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HRM_", "COMPOSE_"))
    }
    cases = []
    with tempfile.TemporaryDirectory(prefix="hrm-port-config-") as temp:
        directory = Path(temp)
        previous = directory / "pilot-before.yml"
        previous.write_text(before, encoding="utf-8")
        env_file = directory / "pilot.env"
        required = set(re.findall(r"\$\{(HRM_[A-Z_]+):\?", text))
        values = {key: secrets.token_hex(32) for key in required}
        values["HRM_LICENSE_PUBLIC_KEY"] = base64.b64encode(
            secrets.token_bytes(32)
        ).decode()
        values["HRM_BACKUP_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
        values["HRM_STAGING_DIR"] = directory.as_posix()
        values["HRM_PILOT_CLAIM_PORT"] = "18001"
        values["HRM_PILOT_TRUST_PORT"] = "18002"
        for name, overlay, host, port in (
            ("before_plain_ports", previous, "127.0.0.1", 8081),
            ("after_default", pilot, "127.0.0.1", 8080),
            ("after_alternate", pilot, "127.0.0.1", 8081),
            ("after_lan", pilot, "0.0.0.0", 8091),
        ):
            values["HRM_PILOT_BIND"] = host
            values["HRM_PILOT_PORT"] = str(port)
            env_file.write_text(
                "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    "hrm-port-config-check",
                    "--env-file",
                    str(env_file),
                    "-f",
                    str(base),
                    "-f",
                    str(overlay),
                    "config",
                    "--format",
                    "json",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode:
                # CLI stderr can echo interpolated values; never publish it.
                raise RuntimeError(f"{name}: compose config exited {result.returncode}")
            services = json.loads(result.stdout)["services"]
            expected = {
                "frontend": [
                    {
                        "host_ip": host,
                        "published": values["HRM_PILOT_PORT"],
                        "target": 8080,
                    },
                    {
                        "host_ip": "127.0.0.1",
                        "published": values["HRM_PILOT_TRUST_PORT"],
                        "target": 8081,
                    },
                ],
                "backend": [
                    {
                        "host_ip": "127.0.0.1",
                        "published": values["HRM_PILOT_CLAIM_PORT"],
                        "target": 8000,
                    },
                ],
            }
            if name == "before_plain_ports":
                expected["frontend"].insert(
                    0, {"host_ip": "127.0.0.1", "published": "8080", "target": 8080}
                )
                expected["backend"].insert(
                    0, {"host_ip": "127.0.0.1", "published": "8000", "target": 8000}
                )
            publications = {}
            for service in ("frontend", "backend"):
                actual = [
                    {
                        "host_ip": item.get("host_ip"),
                        "published": str(item.get("published")),
                        "target": item.get("target"),
                    }
                    for item in services[service].get("ports", [])
                ]
                # Ordering is not part of the Compose port contract.
                sort_key = lambda item: (
                    item["host_ip"],
                    item["published"],
                    item["target"],
                )
                if sorted(actual, key=sort_key) != sorted(
                    expected[service], key=sort_key
                ):
                    raise RuntimeError(
                        f"{name}: unexpected {service} port publications"
                    )
                publications[f"{service}_ports"] = actual
            for service in ("db", "worker", "backup"):
                if services[service].get("ports"):
                    raise RuntimeError(f"{name}: {service} inherited dev ports")
            cases.append({"name": name, **publications, "ok": True})
    return {
        "scope": "compose config only; no containers or volumes modified",
        "files": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (base, pilot)
        },
        "cases": cases,
        "ok": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-dir", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = check(args.install_dir.resolve())
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.TimeoutExpired,
    ) as exc:
        # Only controlled RuntimeError text is safe to print. Other errors can
        # contain raw subprocess output (for example JSONDecodeError.doc).
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps({"ok": False, "error": detail}))
        return 1
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
