"""Comprehensive license middleware tests — points 2,3,4 from acceptance."""

import base64
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import Base, User, UserRole
from app.security import hash_password
from app.services.license_service import build_license_row

SQLITE_URL = "sqlite+pysqlite://"


def gen_keypair() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes_raw().hex()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    return priv_hex, pub_b64


def issue_license(
    priv_hex: str,
    expires_at: str | None = None,
    issued_at: str | None = None,
    max_users: int = 10,
    client_name: str = "Test",
) -> dict[str, Any]:
    from app.license import sign_license

    if expires_at is None:
        expires_at = (date.today() + timedelta(days=30)).isoformat()
    if issued_at is None:
        issued_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "license_id": str(uuid.uuid4()),
        "client_name": client_name,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "max_active_users": max_users,
    }
    sig = sign_license(payload, priv_hex)
    return {**payload, "signature": sig}


def make_app_with_license(pub_b64: str, engine: Any) -> tuple[Any, Settings]:
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "test-secret-key-for-comprehensive",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    return app, settings


def create_admin_and_license(
    engine: Any, priv_hex: str, expired: bool = False, max_users: int = 10
) -> uuid.UUID:
    with Session(engine) as s:
        admin = User(
            id=uuid.uuid4(),
            username="admin",
            full_name="Admin",
            role=UserRole.ADMIN,
            password_hash=hash_password("adminpass123"),
            is_active=True,
        )
        s.add(admin)
        if expired:
            exp = (date.today() - timedelta(days=1)).isoformat()
            issued = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            exp = (date.today() + timedelta(days=10)).isoformat()
            issued = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lic = issue_license(priv_hex, expires_at=exp, issued_at=issued, max_users=max_users)
        row = build_license_row(lic, uploaded_by_user_id=admin.id)
        s.add(row)
        s.commit()
        return admin.id


PROTECTED_ENDPOINTS = [
    "/candidates",
    "/api/candidates",
    "/events",
    "/api/events",
    "/admin",
    "/api/admin",
    "/users",
    "/api/users",
    "/documents",
    "/api/documents",
    "/analytics",
    "/api/analytics",
]

ALLOWED_ENDPOINTS = [
    "/license/status",
    "/api/license/status",
    "/auth/login",
    "/api/auth/login",
    "/setup/owner/status",
    "/api/setup/owner/status",
    "/health",
    "/api/health",
    "/docs",
    "/openapi.json",
]


def test_protected_endpoints_block_without_license() -> None:
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    for ep in PROTECTED_ENDPOINTS:
        resp = client.get(ep)
        assert resp.status_code == 403, f"{ep} should be 403, got {resp.status_code}"
        assert resp.json().get("code") == "no_license"

    for ep in ALLOWED_ENDPOINTS:
        resp = client.get(ep)
        if resp.status_code == 403:
            assert resp.json().get("code") != "no_license"


def test_allowed_recovery_endpoints_accessible_without_license() -> None:
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    assert client.get("/license/status").status_code == 200
    assert client.get("/api/license/status").status_code == 200
    resp = client.post("/auth/login", json={"username": "x", "password": "y"})
    assert resp.status_code != 403 or resp.json().get("code") != "no_license"
    assert client.get("/health").status_code == 200


def test_expired_blocks_normal_but_allows_upload_for_admin() -> None:
    priv_hex, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    create_admin_and_license(engine, priv_hex, expired=True)

    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    app = create_app(settings, engine=engine)
    client = TestClient(app)

    login = client.post("/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    for ep in PROTECTED_ENDPOINTS:
        resp = client.get(ep)
        assert resp.status_code == 403
        assert resp.json().get("code") == "expired"

    assert client.get("/license/status").status_code == 200

    new_lic = issue_license(priv_hex, expires_at=(date.today() + timedelta(days=10)).isoformat())
    up = client.post(
        "/license/upload-json",
        json={"license": new_lic},
        headers={"x-csrf-token": csrf},
    )
    assert up.status_code == 200

    assert client.get("/candidates").status_code == 200


DANGEROUS_PATHS = [
    "/api/licensee",
    "/api/license-extra",
    "/api/authentication",
    "/api/setup-evil",
    "/administer",
    "/documents-evil",
    "/api/licensee/status",
    "/api/setup-evil/owner",
    "/api/authentication/login",
]


def test_dangerous_prefix_bypass_blocked() -> None:
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    for ep in DANGEROUS_PATHS:
        resp = client.get(ep)
        # Deny by default: a look-alike prefix is not on the allowlist, so it
        # is strictly 403 no_license — never 200 and never a 404 leak.
        assert resp.status_code == 403, f"{ep}: expected 403, got {resp.status_code}"
        assert resp.json().get("code") == "no_license", ep


def test_double_slash_and_trailing_slash() -> None:
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    resp = client.get("/api//candidates")
    assert resp.status_code == 403, resp.status_code
    assert resp.json().get("code") == "no_license"

    resp2 = client.get("/api/license/status/")
    assert resp2.status_code in (200, 307, 308, 404)
    if resp2.status_code == 403:
        assert resp2.json().get("code") != "no_license"

    resp3 = client.get("/api//license/status")
    assert resp3.status_code in (200, 307, 308, 404)
    if resp3.status_code == 403:
        assert resp3.json().get("code") != "no_license"


def test_query_string_does_not_bypass() -> None:
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    resp = client.get("/candidates?foo=bar")
    assert resp.status_code == 403
    assert resp.json().get("code") == "no_license"

    resp2 = client.get("/api/license/status?foo=bar")
    assert resp2.status_code == 200


def test_fail_closed_pilot_missing_key() -> None:
    with pytest.raises(Exception) as exc:
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db",
                "LICENSE_PUBLIC_KEY": "",
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 32,
            }
        )
    assert "LICENSE_PUBLIC_KEY" in str(exc.value)


def test_fail_closed_pilot_empty_key() -> None:
    with pytest.raises(Exception):  # noqa: B017
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db",
                "LICENSE_PUBLIC_KEY": "   ",
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 32,
            }
        )


def test_fail_closed_corrupted_public_key() -> None:
    with pytest.raises(Exception) as exc:
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef1234567890",
                "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db",
                "LICENSE_PUBLIC_KEY": "!!!invalid-base64!!!",
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 32,
            }
        )
    assert "LICENSE_PUBLIC_KEY" in str(exc.value)


def test_fail_closed_corrupted_license_and_forged_signature() -> None:
    _priv_hex, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        admin = User(
            id=uuid.uuid4(),
            username="admin",
            full_name="Admin",
            role=UserRole.ADMIN,
            password_hash=hash_password("adminpass123"),
            is_active=True,
        )
        s.add(admin)
        s.commit()

    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    app = create_app(settings, engine=engine)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    corrupted = {
        "license_id": str(uuid.uuid4()),
        "client_name": "Test",
        "issued_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (date.today() + timedelta(days=10)).isoformat(),
        "max_active_users": 5,
    }
    resp = client.post(
        "/license/upload-json",
        json={"license": corrupted},
        headers={"x-csrf-token": csrf},
    )
    assert resp.status_code in (400, 422)

    other_priv = Ed25519PrivateKey.generate().private_bytes_raw().hex()
    forged = issue_license(other_priv, client_name="Hacker")
    resp2 = client.post(
        "/license/upload-json",
        json={"license": forged},
        headers={"x-csrf-token": csrf},
    )
    assert resp2.status_code in (400, 403, 422)


def test_fail_closed_db_error_returns_403_not_bypass() -> None:
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    app = create_app(settings, engine=engine)
    client = TestClient(app)
    resp = client.get("/candidates")
    assert resp.status_code == 403
    assert resp.json().get("code") in ("check_failed", "no_license")


def test_public_key_chain_evidence_is_computed_not_declared() -> None:
    """The chain evidence must be COMPUTED from the real files (generator in
    review-artifacts/), never written by a test with hard-coded ``True``
    values. This test is read-only: it re-runs the generator's pure check
    function and verifies the committed evidence is redacted."""
    import importlib.util
    import json
    import re
    import sys

    repo = Path(__file__).resolve().parents[2]
    gen_path = repo / "review-artifacts" / "gen_license_chain_evidence.py"
    spec = importlib.util.spec_from_file_location("gen_license_chain_evidence", gen_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    checks = module.compute_checks()
    false_checks = sorted(k for k, v in checks.items() if v is False)
    assert not false_checks, false_checks
    assert checks["compose_pilot_services_with_mapping"] == ["backend", "backup", "worker"]

    # Without an imported CI artifact the Compose/runtime steps are NOT RUN —
    # never PASS by declaration.
    steps, meta = module.ci_runtime_steps()
    if not meta.get("imported"):
        assert all(v.startswith("NOT RUN") for v in steps.values()), steps
    else:
        assert meta.get("run_id") and meta.get("head_sha")

    evidence = repo / "review-artifacts" / "license-chain-evidence.json"
    text = evidence.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z0-9+/]{43}=", text), "unredacted 32-byte base64 in evidence"
    data = json.loads(text)
    assert data["checks_read_from_real_files"]["compose_requires_LICENSE_PUBLIC_KEY"] is True
    assert data["checks_read_from_real_files"]["compose_requires_LICENSE_PUBLIC_KEY_via_?"] is True
    # A stale declarative artifact must not come back.
    assert not (repo / "review-artifacts" / "license-public-key-chain.json").exists()


def test_fail_closed_empty_public_key_middleware() -> None:
    """Direct middleware test: empty LICENSE_PUBLIC_KEY in pilot must block."""
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Use pilot env with empty key - Settings validation would normally fail,
    # so we bypass validation by creating Settings in test mode but then
    # manually setting is_pilot flag via env var override in guard?
    # Instead, we test guard logic directly: create settings with pilot=True
    # but with empty key via model_construct (bypass validation) to simulate
    # runtime unavailable key.
    from app.config import Settings as S

    # Create settings that bypasses validation for test purpose
    settings = S.model_construct(
        _env_file=None,
        environment="pilot",
        secret_key="strong-secret-key-1234567890abcdef1234567890",
        database_url=SQLITE_URL,
        license_public_key="",
        pilot_bootstrap_exchange_token="a" * 32,
    )
    # Ensure is_pilot property returns True
    assert settings.is_pilot is True

    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    client = TestClient(app)

    # Protected endpoint should be blocked with check_failed, not bypass
    resp = client.get("/candidates")
    assert resp.status_code == 403, f"empty key should block, got {resp.status_code}"
    assert resp.json().get("code") == "check_failed"

    # Even /api/unknown should be blocked as check_failed (fail-closed)
    resp2 = client.get("/api/unknown")
    assert resp2.status_code == 403
    assert resp2.json().get("code") == "check_failed"


def test_unknown_paths_blocked_without_license() -> None:
    """Deny by default: an unknown path is strictly 403 ``no_license``.

    ``/unknown`` (as nginx would deliver it, prefix already stripped),
    ``/api/unknown`` (as the TestClient/dev proxy delivers it) and a nested
    child must all be refused by the guard itself — not answered with a 404
    by the router, because a 404 would prove the request got past the
    license check. A route added tomorrow under a new root is therefore
    protected automatically.
    """
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    for ep in ["/unknown", "/api/unknown", "/api/unknown/child", "/reports", "/api/reports"]:
        for method in ("get", "post", "put", "patch", "delete"):
            resp = getattr(client, method)(ep)
            assert resp.status_code == 403, f"{method.upper()} {ep}: got {resp.status_code}"
            assert resp.json().get("code") == "no_license", f"{method.upper()} {ep}"
            assert resp.headers.get("X-License-Status") == "no_license"

    # Existing protected path keeps the same strict answer.
    resp = client.get("/api/candidates")
    assert resp.status_code == 403
    assert resp.json().get("code") == "no_license"


def test_new_router_under_unknown_root_is_protected_automatically() -> None:
    """A real route mounted under a root the guard has never heard of must
    still require a license (deny by default, no path heuristics)."""
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)

    @app.get("/reports/summary")
    def _reports_summary() -> dict[str, str]:  # pragma: no cover - must not run
        return {"leak": "yes"}

    client = TestClient(app)
    for ep in ("/reports/summary", "/api/reports/summary"):
        resp = client.get(ep)
        assert resp.status_code == 403, f"{ep}: got {resp.status_code}"
        assert resp.json().get("code") == "no_license"
        assert "leak" not in resp.text


def test_ops_metrics_allowed_without_license_but_never_pii() -> None:
    """Aggregate diagnostics stay reachable without a license (recovery
    contour); they contain counters only."""
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)
    for ep in ("/ops/metrics", "/api/ops/metrics"):
        resp = client.get(ep)
        assert resp.status_code == 200, f"{ep}: got {resp.status_code}"
        assert "no_license" not in resp.text
    # ...but a look-alike sibling is not exempt.
    resp = client.get("/api/ops/metrics-extra")
    assert resp.status_code == 403
    assert resp.json().get("code") == "no_license"


def test_api_unknown_strictly_403_no_license() -> None:
    """Task 2: /api/unknown without license must be strictly 403 code=no_license."""
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    resp = client.get("/api/unknown")
    assert resp.status_code == 403, f"/api/unknown should be 403, got {resp.status_code}"
    assert resp.json().get("code") == "no_license", f"code should be no_license, got {resp.json()}"

    # Also test with original path before stripping participates in decision
    # If guard stripped first, /api/unknown would become /unknown and might be 404 bypass.
    # Our fix ensures LicenseGuard sees original /api/unknown before strip.
    resp2 = client.get("/api/unknown/child")
    assert resp2.status_code == 403
    assert resp2.json().get("code") == "no_license"
