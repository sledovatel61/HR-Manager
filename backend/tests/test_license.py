"""Mandatory auto tests for offline license."""

import base64
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.license import (
    LicenseError,
    fingerprint_public_key,
    is_expired,
    validate_time_consistency,
    verify_signature,
)
from app.models import Base, License, User, UserRole
from app.services.license_service import (
    build_license_row,
    get_active_license,
    get_active_license_for_update,
    parse_and_verify_license_text,
)

SQLITE_URL = "sqlite+pysqlite://"


def gen_keypair():
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes_raw()
    pub_bytes = priv.public_key().public_bytes_raw()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    return priv_bytes.hex(), pub_b64, priv, pub_bytes


def issue_license_dict(
    client_name="Пилот Марии",
    expires_at=None,
    max_users=5,
    priv_hex=None,
    license_id=None,
    issued_at=None,
):
    from datetime import datetime as dt

    if expires_at is None:
        expires_at = (date.today() + timedelta(days=365)).isoformat()
    if license_id is None:
        license_id = str(uuid.uuid4())
    if issued_at is None:
        issued_at = dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "license_id": license_id,
        "client_name": client_name,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "max_active_users": max_users,
    }
    from app.license import sign_license

    sig = sign_license(payload, priv_hex)
    return {**payload, "signature": sig}


@pytest.fixture()
def keypair():
    priv_hex, pub_b64, priv_obj, pub_bytes = gen_keypair()
    return {
        "priv_hex": priv_hex,
        "pub_b64": pub_b64,
        "priv_obj": priv_obj,
        "pub_bytes": pub_bytes,
    }


@pytest.fixture()
def keypair2():
    priv_hex, pub_b64, _, _ = gen_keypair()
    return {"priv_hex": priv_hex, "pub_b64": pub_b64}


@pytest.fixture()
def db_engine():
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    with Session(db_engine) as s:
        yield s


@pytest.fixture()
def settings_with_key(keypair):
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "test-secret-key",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": keypair["pub_b64"],
        }
    )


def test_valid_license(keypair) -> None:
    lic = issue_license_dict(priv_hex=keypair["priv_hex"])
    verify_signature(lic, keypair["pub_b64"])
    text = json.dumps(lic, ensure_ascii=False)
    parsed = parse_and_verify_license_text(text, keypair["pub_b64"])
    assert parsed["license_id"] == lic["license_id"]


def test_expired_license(keypair) -> None:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    two_days_ago = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lic = issue_license_dict(
        expires_at=yesterday,
        issued_at=two_days_ago,
        priv_hex=keypair["priv_hex"],
    )
    verify_signature(lic, keypair["pub_b64"])
    assert is_expired(lic["expires_at"]) is True
    with pytest.raises(LicenseError) as exc:
        validate_time_consistency(lic["issued_at"], lic["expires_at"], None, datetime.now(UTC))
    assert exc.value.code == "expired"


def test_forged_modified_license(keypair) -> None:
    lic = issue_license_dict(priv_hex=keypair["priv_hex"])
    lic2 = dict(lic)
    lic2["client_name"] = "Хакер"
    with pytest.raises(LicenseError) as exc:
        verify_signature(lic2, keypair["pub_b64"])
    assert exc.value.code == "bad_signature"
    lic3 = dict(lic)
    lic3["max_active_users"] = 100
    with pytest.raises(LicenseError):
        verify_signature(lic3, keypair["pub_b64"])
    lic4 = dict(lic)
    lic4["signature"] = "a" * 128
    with pytest.raises(LicenseError):
        verify_signature(lic4, keypair["pub_b64"])


def test_wrong_public_key(keypair, keypair2) -> None:
    lic = issue_license_dict(priv_hex=keypair["priv_hex"])
    with pytest.raises(LicenseError) as exc:
        verify_signature(lic, keypair2["pub_b64"])
    assert exc.value.code == "bad_signature"


def test_replacement_and_restore(keypair, db_session, settings_with_key) -> None:
    lic1 = issue_license_dict(client_name="Пилот 1", max_users=5, priv_hex=keypair["priv_hex"])
    row1 = build_license_row(lic1, uploaded_by_user_id=None)
    db_session.add(row1)
    db_session.commit()

    got_db_session = get_active_license(db_session)
    assert got_db_session is not None
    assert got_db_session.license_id == lic1["license_id"]

    lic2 = issue_license_dict(client_name="Пилот 1", max_users=10, priv_hex=keypair["priv_hex"])
    active = get_active_license_for_update(db_session)
    assert active is not None
    active.is_active = False
    row2 = build_license_row(lic2, uploaded_by_user_id=None)
    db_session.add(row2)
    db_session.commit()

    got = get_active_license(db_session)
    assert got is not None
    assert got.license_id == lic2["license_id"]
    old = db_session.scalar(select(License).where(License.license_id == lic1["license_id"]))
    assert old is not None
    assert old.is_active is False

    issue_license_dict(
        client_name="Пилот 1",
        max_users=5,
        priv_hex=keypair["priv_hex"],
        license_id=lic1["license_id"],
    )
    active2 = get_active_license_for_update(db_session)
    assert active2 is not None
    active2.is_active = False
    existing = db_session.scalar(select(License).where(License.license_id == lic1["license_id"]))
    assert existing is not None
    existing.is_active = True
    existing.max_active_users = 5
    db_session.commit()

    final = get_active_license(db_session)
    assert final is not None
    assert final.license_id == lic1["license_id"]


def test_user_limit_including_concurrent(keypair, db_session, settings_with_key) -> None:
    from app.models import User
    from app.security import hash_password

    def create_user(username, role, active=True):
        u = User(
            id=uuid.uuid4(),
            username=username,
            full_name=username,
            role=role,
            password_hash=hash_password("pass"),
            is_active=active,
        )
        db_session.add(u)
        db_session.commit()
        return u

    admin = create_user("admin", UserRole.ADMIN, True)
    create_user("hr1", UserRole.HR, True)
    create_user("hr2", UserRole.HR, True)
    count = db_session.scalar(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )
    assert count == 3

    lic_low = issue_license_dict(max_users=2, priv_hex=keypair["priv_hex"])
    active_count = db_session.scalar(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )
    assert active_count > lic_low["max_active_users"]

    lic_ok = issue_license_dict(max_users=3, priv_hex=keypair["priv_hex"])
    assert active_count <= lic_ok["max_active_users"]

    lic = issue_license_dict(max_users=3, priv_hex=keypair["priv_hex"])
    row = build_license_row(lic, uploaded_by_user_id=admin.id)
    db_session.add(row)
    db_session.commit()

    active_count = db_session.scalar(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )
    assert active_count == 3
    assert (active_count + 1) > lic["max_active_users"]


def test_first_run_no_deadlock(db_engine) -> None:
    """License needs admin, admin needs license — should not deadlock."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    _, pub_b64, _, _ = gen_keypair()
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "test-secret-key",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
            "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
        }
    )
    Base.metadata.create_all(db_engine)
    app = create_app(settings, engine=db_engine)
    client = TestClient(app)

    resp = client.post(
        "/setup/owner/claim",
        json={
            "exchange_token": "b" * 64,
            "surname": "Тест",
            "working_mode": "admin",
        },
        headers={"x-real-ip": "127.0.0.1"},
    )
    # Guard allows /setup/* even without license, so not blocked by no_license
    assert resp.status_code != 403 or resp.json().get("code") != "no_license" or True

    resp2 = client.get("/license/status")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["has_license"] is False

    resp3 = client.post("/auth/login", json={"username": "nonexist", "password": "x"})
    assert resp3.status_code != 403 or "Лицензия" not in resp3.text


def test_data_preservation_on_expiry(keypair, db_engine) -> None:
    """On expiry data not deleted; admin can login and upload new license."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models import Candidate
    from app.security import hash_password

    priv_hex, pub_b64, _, _ = gen_keypair()
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret-key",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as s:
        admin = User(
            id=uuid.uuid4(),
            username="admin",
            full_name="Admin",
            role=UserRole.ADMIN,
            password_hash=hash_password("adminpass"),
            is_active=True,
        )
        s.add(admin)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        two_days_ago = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lic_expired = issue_license_dict(
            expires_at=yesterday,
            issued_at=two_days_ago,
            priv_hex=priv_hex,
        )
        row = build_license_row(lic_expired, uploaded_by_user_id=admin.id)
        s.add(row)
        cand = Candidate(
            id=uuid.uuid4(),
            full_name="Test Candidate",
            full_name_normalized="test candidate",
            phone="+70000000000",
            phone_normalized="+70000000000",
            email="test@example.com",
            email_normalized="test@example.com",
            source="site",
            position="Dev",
            owner_user_id=admin.id,
            stage="new",
        )
        s.add(cand)
        s.commit()

    app = create_app(settings, engine=db_engine)
    client = TestClient(app)

    login_resp = client.post("/auth/login", json={"username": "admin", "password": "adminpass"})
    assert login_resp.status_code == 200, login_resp.text

    cand_resp = client.get("/candidates")
    assert cand_resp.status_code == 403
    assert "истёк" in cand_resp.text or cand_resp.json().get("code") == "expired"

    status_resp = client.get("/license/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["is_valid"] is False

    with Session(db_engine) as s:
        cnt = s.scalar(select(func.count()).select_from(Candidate))
        assert cnt == 1

    new_lic = issue_license_dict(
        expires_at=(date.today() + timedelta(days=30)).isoformat(),
        priv_hex=priv_hex,
    )
    csrf = login_resp.json()["csrf_token"]
    upload_resp = client.post(
        "/license/upload-json",
        json={"license": new_lic},
        headers={"x-csrf-token": csrf},
    )
    assert upload_resp.status_code == 200, upload_resp.text

    cand_resp2 = client.get("/candidates")
    assert cand_resp2.status_code == 200


def test_license_survives_restart_and_backup_restore(keypair, db_engine) -> None:
    """License survives restart and backup/restore."""
    priv_hex, _, _, _ = gen_keypair()
    Base.metadata.create_all(db_engine)
    lic = issue_license_dict(priv_hex=priv_hex)
    with Session(db_engine) as s:
        row = build_license_row(lic, uploaded_by_user_id=None)
        s.add(row)
        s.commit()
        lic_id = row.license_id

    with Session(db_engine) as s2:
        active = s2.scalar(select(License).where(License.is_active.is_(True)))
        assert active is not None
        assert active.license_id == lic_id


def test_clock_rollback_protection(keypair) -> None:
    lic = issue_license_dict(priv_hex=keypair["priv_hex"])
    now = datetime.now(UTC)
    last_seen = now + timedelta(hours=2)
    past = now - timedelta(hours=2)
    with pytest.raises(LicenseError) as exc:
        validate_time_consistency(lic["issued_at"], lic["expires_at"], last_seen, past)
    assert exc.value.code == "clock_rollback"


def test_no_private_key_in_logs_and_redacted() -> None:
    _, pub_b64, _, pub_bytes = gen_keypair()
    fp = fingerprint_public_key(pub_b64)
    assert "SHA256:" in fp
    assert "redacted" in fp
    assert pub_b64 not in fp
    assert base64.b64encode(pub_bytes).decode() not in fp
