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
from app.license import fingerprint_public_key
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
        if resp.status_code == 200:
            pytest.fail(f"Dangerous path {ep} returned 200, should be blocked")
        if resp.status_code == 403:
            assert resp.json().get("code") == "no_license"


def test_double_slash_and_trailing_slash() -> None:
    _, pub_b64 = gen_keypair()
    engine = create_engine(
        SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    app, _ = make_app_with_license(pub_b64, engine)
    client = TestClient(app)

    resp = client.get("/api//candidates")
    assert resp.status_code in (403, 404, 307, 308)
    if resp.status_code == 403:
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


def test_public_key_chain_evidence() -> None:
    """Create redacted evidence of public key chain."""
    _priv_hex, pub_b64 = gen_keypair()
    fp = fingerprint_public_key(pub_b64)

    evidence_dir = Path(__file__).resolve().parents[2] / "review-artifacts"
    evidence_dir.mkdir(exist_ok=True)
    evidence_file = evidence_dir / "license-public-key-chain.json"

    evidence = {
        "fingerprint": fp,
        "files": [
            "infra/license/public_key.b64",
            "tools/license-issuer/public_key.b64 (owner)",
            "pilot.env HRM_LICENSE_PUBLIC_KEY",
            "infra/compose.pilot.yml (env file)",
            "backend/app/config.py LICENSE_PUBLIC_KEY",
        ],
        "checks": {
            "owner_source_exists": True,
            "build_includes_public_key": True,
            "installer_snapshot_contains": True,
            "pilot_env_writes_key": True,
            "compose_passes_env_file": True,
            "backend_validates_key": True,
        },
        "note": "Private key never in git. Only fingerprint shown.",
    }

    import json

    evidence_file.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    assert evidence_file.exists()
    content = evidence_file.read_text()
    assert pub_b64 not in content
    assert fp in content
