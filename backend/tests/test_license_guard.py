"""License guard — API access restrictions, allowed endpoints without license."""

import base64
import uuid
from datetime import UTC, date, datetime, timedelta

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


def test_guard_allows_auth_and_license_without_license() -> None:
    _, pub_b64 = gen_keypair()
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    client = TestClient(app)

    assert client.get("/license/status").status_code == 200
    assert client.get("/health").status_code == 200
    resp = client.get("/auth/me")
    assert resp.status_code in (401, 403)
    if resp.status_code == 403:
        assert resp.json().get("code") != "no_license"

    resp2 = client.get("/candidates")
    assert resp2.status_code == 403
    assert resp2.json().get("code") == "no_license"


def test_guard_blocks_when_expired_but_allows_license_upload() -> None:
    priv_hex, pub_b64 = gen_keypair()
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        admin = User(
            id=uuid.uuid4(),
            username="admin",
            full_name="Admin",
            role=UserRole.ADMIN,
            password_hash=hash_password("pass1234"),
            is_active=True,
        )
        s.add(admin)
        two_days_ago = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        expired = issue_license(
            priv_hex,
            expires_at=(date.today() - timedelta(days=1)).isoformat(),
            issued_at=two_days_ago,
        )
        row = build_license_row(expired, uploaded_by_user_id=admin.id)
        s.add(row)
        s.commit()

    app = create_app(settings, engine=engine)
    client = TestClient(app)

    login = client.post("/auth/login", json={"username": "admin", "password": "pass1234"})
    assert login.status_code == 200

    resp = client.get("/candidates")
    assert resp.status_code == 403
    assert resp.json().get("code") == "expired"

    assert client.get("/license/status").status_code == 200

    csrf = login.json()["csrf_token"]
    new_lic = issue_license(
        priv_hex,
        expires_at=(date.today() + timedelta(days=10)).isoformat(),
    )
    up = client.post(
        "/license/upload-json",
        json={"license": new_lic},
        headers={"x-csrf-token": csrf},
    )
    assert up.status_code == 200

    assert client.get("/candidates").status_code == 200


def test_guard_disabled_when_no_public_key() -> None:
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": "",
        }
    )
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    client = TestClient(app)

    resp = client.get("/candidates")
    assert resp.status_code != 403 or resp.json().get("code") != "no_license"
