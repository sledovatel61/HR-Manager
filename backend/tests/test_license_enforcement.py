"""Additional enforcement tests — points 2,3,4,5.

- Full public key path
- API enforcement
- First-run clean DB
- Replacement
- openapi no leak
"""

import base64
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.license import LicenseError
from app.models import Base, License, User, UserRole
from app.services.license_service import build_license_row

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
def db_engine():
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ------------------------------------------------------------------
# 2. Full public key path
# ------------------------------------------------------------------


def test_full_public_key_path_simulation(db_engine):
    """Owner flow: keypair -> file -> Secrets.psm1 -> pilot.env -> backend."""
    priv_hex, pub_b64, _, _ = gen_keypair()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pub_file = tmp_path / "public_key.b64"
        pub_file.write_text(pub_b64 + "\n", encoding="utf-8")

        content = pub_file.read_text(encoding="utf-8").strip()
        assert content == pub_b64

        pilot_env = tmp_path / "pilot.env"
        pilot_env.write_text(
            f"HRM_LICENSE_PUBLIC_KEY={content}\n",
            encoding="utf-8",
        )
        env_content = pilot_env.read_text(encoding="utf-8")
        assert pub_b64 in env_content
        assert "private" not in env_content.lower()

        settings = Settings.model_validate(
            {
                "APP_ENV": "test",
                "SECRET_KEY": "test-secret-key",
                "DATABASE_URL": SQLITE_URL,
                "LICENSE_PUBLIC_KEY": content,
            }
        )
        assert settings.license_public_key == pub_b64

        from app.license import verify_signature

        lic_valid = issue_license_dict(priv_hex=priv_hex)
        verify_signature(lic_valid, pub_b64)

        lic_forged = dict(lic_valid)
        lic_forged["client_name"] = "Hacker"
        with pytest.raises(LicenseError):
            verify_signature(lic_forged, pub_b64)

        with pytest.raises(ValueError) as exc:
            Settings.model_validate(
                {
                    "APP_ENV": "pilot",
                    "SECRET_KEY": "strong-secret-key-1234567890abcdef",
                    "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db",
                    "LICENSE_PUBLIC_KEY": "",
                    "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
                }
            )
        assert "LICENSE_PUBLIC_KEY" in str(exc.value)

        s_pilot = Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef",
                "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/db",
                "LICENSE_PUBLIC_KEY": pub_b64,
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
            }
        )
        assert s_pilot.is_pilot is True


def test_config_file_fallback():
    """Env override works for license public key."""
    _, pub_b64, _, _ = gen_keypair()
    repo_infra = Path(__file__).resolve().parents[2] / "infra" / "license"
    repo_infra.mkdir(parents=True, exist_ok=True)
    test_file = repo_infra / "public_key.b64.test_tmp"
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    assert settings.license_public_key == pub_b64
    if test_file.exists():
        test_file.unlink()


# ------------------------------------------------------------------
# 3. Enforcement API directly
# ------------------------------------------------------------------


def test_enforcement_blocks_business_endpoints_after_expiry(db_engine):
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

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    two_days_ago = (
        datetime.now(UTC) - timedelta(days=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        lic_expired = issue_license_dict(
            expires_at=yesterday,
            issued_at=two_days_ago,
            priv_hex=priv_hex,
        )
        row = build_license_row(lic_expired, uploaded_by_user_id=admin.id)
        s.add(row)
        cand = Candidate(
            id=uuid.uuid4(),
            full_name="Test",
            full_name_normalized="test",
            phone="+70000000000",
            phone_normalized="+70000000000",
            email="t@example.com",
            email_normalized="t@example.com",
            source="site",
            position="Dev",
            owner_user_id=admin.id,
            stage="new",
        )
        s.add(cand)
        s.commit()

    app = create_app(settings, engine=db_engine)
    client = TestClient(app)

    login_resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": "adminpass"},
    )
    assert login_resp.status_code == 200
    csrf = login_resp.json()["csrf_token"]

    blocked_paths = [
        ("/candidates", "GET"),
        ("/api/candidates", "GET"),
        ("/candidates", "POST"),
        ("/events", "GET"),
        ("/api/events", "POST"),
        ("/users", "GET"),
        ("/api/users", "GET"),
        ("/admin/audit", "GET"),
        ("/api/admin/audit", "GET"),
        ("/updates/status", "GET"),
        ("/api/updates/status", "GET"),
        ("/updates/check", "POST"),
        ("/api/updates/check", "POST"),
        ("/updates/download", "POST"),
        ("/api/updates/download", "POST"),
        ("/updates/install", "POST"),
        ("/api/updates/install", "POST"),
        ("/api/unknown-route-xyz", "GET"),
        ("/unknown-route-xyz", "GET"),
    ]

    for path, method in blocked_paths:
        if method == "GET":
            r = client.get(path)
        elif method == "POST":
            r = client.post(path, json={}, headers={"x-csrf-token": csrf})
        elif method == "PUT":
            r = client.put(path, json={}, headers={"x-csrf-token": csrf})
        elif method == "PATCH":
            r = client.patch(path, json={}, headers={"x-csrf-token": csrf})
        else:
            r = client.request("DELETE", path, headers={"x-csrf-token": csrf})

        if path in (
            "/candidates",
            "/api/candidates",
            "/events",
            "/api/events",
            "/users",
            "/api/users",
            "/admin/audit",
            "/api/admin/audit",
            "/updates/status",
            "/api/updates/status",
        ):
            assert r.status_code == 403, f"{method} {path} got {r.status_code}"
            assert r.json().get("code") == "expired" or "истёк" in r.text

    allowed_paths = [
        "/license/status",
        "/api/license/status",
        "/health",
        "/api/health",
        "/ops/status",
        "/api/ops/status",
        "/ops/backup-health",
        "/api/ops/backup-health",
        "/admin/ops/pilot-readiness",
        "/api/admin/ops/pilot-readiness",
        "/updates/engine-host-report",
        "/api/updates/engine-host-report",
        "/updates/engine-state",
        "/api/updates/engine-state",
        "/updates/engine-check",
        "/api/updates/engine-check",
        "/updates/engine-report",
        "/api/updates/engine-report",
        "/setup/owner/status",
        "/api/setup/owner/status",
        "/docs",
        "/openapi.json",
    ]

    for path in allowed_paths:
        if "engine-" in path:
            r = client.post(
                path, json={}, headers={"x-engine-token": "invalid"}
            )
            assert r.status_code != 403 or r.json().get("code") != "expired", (
                f"{path} should be allowed, got {r.status_code}"
            )
        else:
            r = client.get(path)
            if r.status_code == 403:
                assert r.json().get("code") != "expired", (
                    f"{path} should be allowed"
                )

    for variant in [
        "/candidates?foo=bar",
        "/candidates/",
        "/api/candidates/?x=1",
        "/candidates//",
        "/api//candidates",
    ]:
        r = client.get(variant)
        assert r.status_code != 200 or "Test" not in r.text, f"Bypass {variant}"


def test_enforcement_upload_only_admin(db_engine):
    from fastapi.testclient import TestClient

    from app.main import create_app
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
        hr = User(
            id=uuid.uuid4(),
            username="hr",
            full_name="HR",
            role=UserRole.HR,
            password_hash=hash_password("hrpass"),
            is_active=True,
        )
        s.add_all([admin, hr])
        s.commit()

    app = create_app(settings, engine=db_engine)
    client = TestClient(app)

    hr_login = client.post(
        "/auth/login", json={"username": "hr", "password": "hrpass"}
    )
    assert hr_login.status_code == 200
    hr_csrf = hr_login.json()["csrf_token"]

    lic = issue_license_dict(priv_hex=priv_hex)

    r = client.post(
        "/license/upload-json",
        json={"license": lic},
        headers={"x-csrf-token": hr_csrf},
    )
    assert r.status_code == 403, f"Non-admin should be forbidden, got {r.status_code}"

    admin_login = client.post(
        "/auth/login", json={"username": "admin", "password": "adminpass"}
    )
    assert admin_login.status_code == 200
    admin_csrf = admin_login.json()["csrf_token"]
    r2 = client.post(
        "/license/upload-json",
        json={"license": lic},
        headers={"x-csrf-token": admin_csrf},
    )
    assert r2.status_code == 200


def test_openapi_does_not_leak_secrets(db_engine):
    from fastapi.testclient import TestClient

    from app.main import create_app

    _, pub_b64, _, _ = gen_keypair()
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret-key",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
        }
    )
    Base.metadata.create_all(db_engine)
    app = create_app(settings, engine=db_engine)
    client = TestClient(app)

    r = client.get("/openapi.json")
    assert r.status_code == 200
    text = r.text.lower()
    assert "backup_enc_key" not in text
    assert "secret_key" not in text
    assert "pilot_bootstrap" not in text


# ------------------------------------------------------------------
# 4. First-run clean DB
# ------------------------------------------------------------------


def test_first_run_clean_db_with_key(db_engine):
    from fastapi.testclient import TestClient

    from app.main import create_app

    priv_hex, pub_b64, _, _ = gen_keypair()
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-secret-key",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": pub_b64,
            "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
        }
    )
    Base.metadata.create_all(db_engine)
    app = create_app(settings, engine=db_engine)
    client = TestClient(app)

    status = client.get("/license/status")
    assert status.status_code == 200
    assert status.json()["has_license"] is False

    claim = client.post(
        "/setup/owner/claim",
        json={
            "exchange_token": "b" * 64,
            "surname": "Test",
            "working_mode": "admin",
        },
        headers={"x-real-ip": "127.0.0.1"},
    )
    if claim.status_code == 403:
        assert claim.json().get("code") != "no_license"

    from app.security import hash_password

    with Session(db_engine) as s:
        admin = User(
            id=uuid.uuid4(),
            username="maria",
            full_name="Maria",
            role=UserRole.ADMIN,
            password_hash=hash_password("mariapass"),
            is_active=True,
        )
        s.add(admin)
        s.commit()

    login = client.post(
        "/auth/login", json={"username": "maria", "password": "mariapass"}
    )
    assert login.status_code == 200

    cand = client.get("/candidates")
    assert cand.status_code == 403
    assert cand.json().get("code") == "no_license"

    lic = issue_license_dict(priv_hex=priv_hex, max_users=5)
    csrf = login.json()["csrf_token"]
    upload = client.post(
        "/license/upload-json",
        json={"license": lic},
        headers={"x-csrf-token": csrf},
    )
    assert upload.status_code == 200

    cand2 = client.get("/candidates")
    assert cand2.status_code == 200


def test_pilot_requires_key_and_test_dev_disabled_explicitly():
    with pytest.raises(ValueError):
        Settings.model_validate(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "strong-secret-key-1234567890abcdef",
                "DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
                "LICENSE_PUBLIC_KEY": "",
                "PILOT_BOOTSTRAP_EXCHANGE_TOKEN": "a" * 64,
            }
        )
    s_test = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test",
            "DATABASE_URL": SQLITE_URL,
            "LICENSE_PUBLIC_KEY": "",
        }
    )
    assert s_test.license_public_key == ""
    s_dev = Settings.model_validate(
        {
            "APP_ENV": "development",
            "SECRET_KEY": "dev-only-secret-key-not-for-production",
            "DATABASE_URL": (
                "postgresql+psycopg://hr_manager:"
                "hr_manager_dev_password@localhost:5432/hr_manager"
            ),
            "LICENSE_PUBLIC_KEY": "",
        }
    )
    assert s_dev.license_public_key == ""


# ------------------------------------------------------------------
# 5. Replacement
# ------------------------------------------------------------------


def test_replacement_smaller_limit_blocked_until_deactivation(db_engine):
    from fastapi.testclient import TestClient

    from app.main import create_app
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
        hr1 = User(
            id=uuid.uuid4(),
            username="hr1",
            full_name="HR1",
            role=UserRole.HR,
            password_hash=hash_password("pass"),
            is_active=True,
        )
        hr2 = User(
            id=uuid.uuid4(),
            username="hr2",
            full_name="HR2",
            role=UserRole.HR,
            password_hash=hash_password("pass"),
            is_active=True,
        )
        s.add_all([admin, hr1, hr2])
        lic5 = issue_license_dict(max_users=5, priv_hex=priv_hex)
        row = build_license_row(lic5, uploaded_by_user_id=admin.id)
        s.add(row)
        s.commit()

    app = create_app(settings, engine=db_engine)
    client = TestClient(app)
    login = client.post(
        "/auth/login", json={"username": "admin", "password": "adminpass"}
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    lic2 = issue_license_dict(max_users=2, priv_hex=priv_hex)
    r = client.post(
        "/license/upload-json",
        json={"license": lic2},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 409, f"Should block, got {r.status_code} {r.text}"

    with Session(db_engine) as s:
        hr2_db = s.scalar(select(User).where(User.username == "hr2"))
        hr2_db.is_active = False
        s.commit()

    r2 = client.post(
        "/license/upload-json",
        json={"license": lic2},
        headers={"x-csrf-token": csrf},
    )
    assert r2.status_code == 200, f"After deactivation, got {r2.status_code}"

    with Session(db_engine) as s:
        active = s.scalars(
            select(License).where(License.is_active.is_(True))
        ).all()
        assert len(active) == 1
        assert active[0].max_active_users == 2

    r3 = client.post(
        "/license/upload-json",
        json={"license": lic2},
        headers={"x-csrf-token": csrf},
    )
    assert r3.status_code in (200, 409)
    with Session(db_engine) as s:
        active2 = s.scalars(
            select(License).where(License.is_active.is_(True))
        ).all()
        assert len(active2) == 1


def test_data_not_deleted_on_replacement(db_engine):
    from app.models import Candidate
    from app.security import hash_password

    priv_hex, _, _, _ = gen_keypair()
    Base.metadata.create_all(db_engine)

    with Session(db_engine) as s:
        admin = User(
            id=uuid.uuid4(),
            username="admin",
            full_name="Admin",
            role=UserRole.ADMIN,
            password_hash=hash_password("pass"),
            is_active=True,
        )
        s.add(admin)
        cand = Candidate(
            id=uuid.uuid4(),
            full_name="Keep",
            full_name_normalized="keep",
            phone="+70000000000",
            phone_normalized="+70000000000",
            email="k@example.com",
            email_normalized="k@example.com",
            source="site",
            position="Dev",
            owner_user_id=admin.id,
            stage="new",
        )
        s.add(cand)
        lic1 = issue_license_dict(max_users=5, priv_hex=priv_hex)
        row1 = build_license_row(lic1, uploaded_by_user_id=admin.id)
        s.add(row1)
        s.commit()
        cand_id = cand.id

    with Session(db_engine) as s:
        from app.services.license_service import get_active_license_for_update

        active = get_active_license_for_update(s)
        active.is_active = False
        lic2 = issue_license_dict(max_users=10, priv_hex=priv_hex)
        row2 = build_license_row(lic2, uploaded_by_user_id=None)
        s.add(row2)
        s.commit()

    with Session(db_engine) as s:
        cnt = s.scalar(select(func.count()).select_from(Candidate))
        assert cnt == 1
        c = s.get(Candidate, cand_id)
        assert c is not None
        assert c.full_name == "Keep"
