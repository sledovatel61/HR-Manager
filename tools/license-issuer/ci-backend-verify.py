# -*- coding: utf-8 -*-
"""CI: verify licenses issued on Windows (CLI, GUI, HTML/Edge) with the BACKEND code.

Uses the exact service-layer functions of the license upload endpoint
(backend/app/routers/license.py::_process_upload):
  app.services.license_service.parse_and_verify_license_text(text, public_b64)
  app.license.validate_time_consistency(issued_at, expires_at, last_seen_at=None, now=now)

This is NOT an HTTP upload into a running backend with a database (that path
is covered by the Linux backend integration job with the repository's own
test keys); it proves that the Windows-issued files are accepted/rejected by
the backend verification code with the matching public key.

usage: python ci-backend-verify.py <backend_dir> <handoff_dir>
The handoff dir holds manifest.json + licenses + public keys (never private keys).
ASCII-only; prints no key material (public keys are shown as fingerprints only).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

backend_dir, handoff = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(backend_dir))

from app.license import LicenseError, fingerprint_public_key, validate_time_consistency  # noqa: E402
from app.services.license_service import parse_and_verify_license_text  # noqa: E402

manifest = json.loads((handoff / "manifest.json").read_text(encoding="utf-8"))
failures = 0
for item in manifest:
    lic_bytes = (handoff / item["license"]).read_bytes()
    pub = (handoff / item["public_key"]).read_text(encoding="utf-8").strip()
    expect = item["expect"]  # "accept" or an error code such as "bad_signature"
    try:
        data = parse_and_verify_license_text(lic_bytes, pub)
        validate_time_consistency(
            issued_at_str=data["issued_at"],
            expires_at_str=data["expires_at"],
            last_seen_at=None,
            now=datetime.now(UTC),
        )
        outcome = "accept"
        detail = f"client={data['client_name']!r} expires={data['expires_at']} max={data['max_active_users']}"
    except LicenseError as exc:
        outcome = exc.code
        detail = str(exc)
    ok = outcome == expect
    failures += 0 if ok else 1
    print(
        f"[backend] {'PASS' if ok else 'FAIL'} {item['origin']}: {item['license']} with key "
        f"{fingerprint_public_key(pub)} -> {outcome} (expected {expect}); {detail}".encode("ascii", "backslashreplace").decode("ascii"),
        flush=True,
    )

print(f"[backend] {'PASS' if failures == 0 else 'FAIL'}: {len(manifest) - failures}/{len(manifest)} backend verification expectations met")
sys.exit(1 if failures else 0)
