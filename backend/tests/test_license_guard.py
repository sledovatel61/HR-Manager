# -*- coding: utf-8 -*-
"""License guard — API access restrictions, allowed endpoints without license."""

import base64
import uuid
from datetime import date, timedelta, UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Base, User, UserRole
from app.security import hash_password
from app.services.license_service import build_license_row

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SQLITE_URL = "sqlite+pysqlite://"

def gen_keypair():
    priv = Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes_raw().hex()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    return priv_hex, pub_b64

def issue_license(priv_hex, expires_at=None, issued_at=None):
    from app.license import sign_license
    if expires_at is None:
        expires_at = (date.today() + timedelta(days=30)).isoformat()
    if issued_at is None:
        issued_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "license_id": str(uuid.uuid4()),
        "client_name": "Test",
        "issued_at": issued_at,
        "expires_at": expires_at,
        "max_active_users": 10,
    }
    sig = sign_license(payload, priv_hex)
    return {**payload, "signature": sig}

def test_guard_allows_auth_and_license_without_license():
    priv_hex, pub_b64 = gen_keypair()
    settings = Settings.model_validate({
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "SECRET_KEY": "test-secret",
        "DATABASE_URL": SQLITE_URL,
        "LICENSE_PUBLIC_KEY": pub_b64,
    })
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    client = TestClient(app)

    # These should be allowed even without license (guard allows)
    assert client.get("/license/status").status_code == 200
    assert client.get("/health").status_code == 200
    # /api/auth/me without auth -> 401, not 403 license
    resp = client.get("/auth/me")
    assert resp.status_code in (401, 403)
    if resp.status_code == 403:
        # Should not be license block
        assert resp.json().get("code") != "no_license"

    # Protected endpoint without license should be blocked 403 no_license
    resp2 = client.get("/candidates")
    assert resp2.status_code == 403
    assert resp2.json().get("code") == "no_license"

def test_guard_blocks_when_expired_but_allows_license_upload():
    priv_hex, pub_b64 = gen_keypair()
    settings = Settings.model_validate({
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "SECRET_KEY": "test-secret",
        "DATABASE_URL": SQLITE_URL,
        "LICENSE_PUBLIC_KEY": pub_b64,
    })
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Create admin and expired license (issued 2 days ago)
    with Session(engine) as s:
        admin = User(id=uuid.uuid4(), username="admin", full_name="Admin", role=UserRole.ADMIN, password_hash=hash_password("pass1234"), is_active=True)
        s.add(admin)
        two_days_ago = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        expired = issue_license(priv_hex, expires_at=(date.today() - timedelta(days=1)).isoformat(), issued_at=two_days_ago)
        row = build_license_row(expired, uploaded_by_user_id=admin.id)
        s.add(row)
        s.commit()

    app = create_app(settings, engine=engine)
    client = TestClient(app)

    # Login should succeed (auth allowed)
    login = client.post("/auth/login", json={"username": "admin", "password": "pass1234"})
    assert login.status_code == 200

    # Protected blocked with expired
    resp = client.get("/candidates")
    assert resp.status_code == 403
    assert resp.json().get("code") == "expired"

    # License status allowed
    assert client.get("/license/status").status_code == 200

    # Upload new valid license should be allowed (admin)
    csrf = login.json()["csrf_token"]
    new_lic = issue_license(priv_hex, expires_at=(date.today() + timedelta(days=10)).isoformat())
    up = client.post("/license/upload-json", json={"license": new_lic}, headers={"x-csrf-token": csrf})
    assert up.status_code == 200

    # Now candidates should be accessible
    assert client.get("/candidates").status_code == 200

def test_guard_disabled_when_no_public_key():
    settings = Settings.model_validate({
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "SECRET_KEY": "test-secret",
        "DATABASE_URL": SQLITE_URL,
        "LICENSE_PUBLIC_KEY": "",  # disabled
    })
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    client = TestClient(app)

    # Without public key, enforcement disabled, protected endpoints should not be blocked by license
    # They may still require auth, but not license code
    resp = client.get("/candidates")
    # Should be 401 (no auth) not 403 no_license
    assert resp.status_code != 403 or resp.json().get("code") != "no_license"
