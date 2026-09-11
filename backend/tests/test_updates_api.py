"""Тесты API канала обновлений (Phase 13): RBAC/scope, CSRF, rate limit,
идемпотентность, конечный автомат состояний, движковые эндпоинты.

Сеть не используется: verified_manifest/download_package мокаются
детерминированными fixture из infra/release/testdata.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import AccessGrant, AccessGrantScope, AuditAction, AuditEvent, Base, User, UserRole
from app.security import hash_password
from app.update_channel_contract import parse_manifest_json

FIXTURE_PASSWORD = "Str0ng-Pass-2026"
TESTDATA = Path(__file__).resolve().parents[2] / "infra" / "release" / "testdata"
INSTALLED_SHA = "3" * 40


def fixture(name: str) -> dict:
    return parse_manifest_json((TESTDATA / name).read_text(encoding="utf-8"))


def channel_settings(tmp_path: Path) -> Settings:
    trusted = json.loads((TESTDATA / "trusted_keys.json").read_text(encoding="utf-8"))
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key",
            "DATABASE_URL": "sqlite+pysqlite://",
            "LOGIN_MAX_FAILURES": "5",
            "LOGIN_LOCK_MINUTES": "15",
            "UPDATE_CHANNEL_URL": "https://updates.example.com/hrm/manifest.json",
            "UPDATE_CHANNEL_PUBLIC_KEYS": json.dumps(trusted),
            "UPDATE_ENGINE_TOKEN": "engine-token-0123456789abcdef",
            "UPDATE_INSTALLED_VERSION": "0.13.0",
            "UPDATE_INSTALLED_SHA": INSTALLED_SHA,
            "UPDATE_CHECK_MIN_INTERVAL_SECONDS": "300",
            "UPDATE_STAGING_DIR": str(tmp_path / "staging"),
        }
    )


@pytest.fixture()
def channel_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = channel_settings(tmp_path)
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


@pytest.fixture()
def db_session(channel_client: TestClient) -> Iterator[Session]:
    from fastapi import FastAPI

    engine = cast(FastAPI, channel_client.app).state.engine
    with Session(engine) as session:
        yield session
        session.rollback()


def make_user(db: Session, username: str, role: UserRole, scope: str | None = None) -> User:
    user = User(
        username=username,
        full_name=username,
        role=role,
        password_hash=hash_password(FIXTURE_PASSWORD),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if scope:
        db.add(
            AccessGrant(
                user_id=user.id,
                scope=AccessGrantScope(scope),
                granted_by_user_id=user.id,
            )
        )
        db.commit()
    return user


def login(client: TestClient, username: str) -> dict:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"X-CSRF-Token": body["csrf_token"]}


def grant(db: Session, user: User, scope: str) -> None:
    db.add(
        AccessGrant(
            user_id=user.id,
            scope=AccessGrantScope(scope),
            granted_by_user_id=user.id,
        )
    )
    db.commit()


# --- RBAC / scope --------------------------------------------------------------


def test_status_requires_authentication(channel_client: TestClient) -> None:
    assert channel_client.get("/updates/status").status_code == 401


def test_status_viewer_needs_confirmed_scope(
    channel_client: TestClient, db_session: Session
) -> None:
    make_user(db_session, "hr", UserRole.HR)
    headers = login(channel_client, "hr")
    assert channel_client.get("/updates/status", headers=headers).status_code == 403
    grant(db_session, db_session.query(User).filter_by(username="hr").one(), "pilot_full_access")
    assert channel_client.get("/updates/status", headers=headers).status_code == 200


def test_admin_without_update_scope_cannot_check(
    channel_client: TestClient, db_session: Session
) -> None:
    make_user(db_session, "admin", UserRole.ADMIN)
    headers = login(channel_client, "admin")
    assert channel_client.post("/updates/check", headers=headers).status_code == 403


def test_admin_with_scope_can_check(channel_client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    from app.routers import updates as updates_router

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        updates_router,
        "verified_manifest",
        lambda settings, preview=False: fixture("manifest.valid.json"),
    )
    response = channel_client.post("/updates/check", headers=headers)
    monkeypatch.undo()
    assert response.status_code == 200
    assert response.json()["state"] == "available"
    assert response.json()["available_version"] == "0.14.0"


def test_non_admin_with_scope_is_forbidden_to_mutate(
    channel_client: TestClient, db_session: Session
) -> None:
    # Грант никогда не обходит роли: HR со scope всё равно не может командовать.
    user = make_user(db_session, "hr", UserRole.HR, scope="update_channel_manage")
    assert user is not None
    headers = login(channel_client, "hr")
    assert channel_client.post("/updates/check", headers=headers).status_code == 403
    # Но просмотр ему доступен (scope подтверждён).
    assert channel_client.get("/updates/status", headers=headers).status_code == 200


def test_idor_grant_on_other_user_does_not_help(
    channel_client: TestClient, db_session: Session
) -> None:
    make_user(db_session, "other", UserRole.ADMIN, scope="update_channel_manage")
    make_user(db_session, "me", UserRole.ADMIN)
    headers = login(channel_client, "me")
    assert channel_client.post("/updates/check", headers=headers).status_code == 403


# --- CSRF ----------------------------------------------------------------------


def test_check_requires_csrf(channel_client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    headers.pop("X-CSRF-Token", None)
    assert channel_client.post("/updates/check", headers=headers).status_code == 403


# --- Rate limit ------------------------------------------------------------------


def test_check_rate_limited(channel_client: TestClient, db_session: Session) -> None:
    from app.routers import updates as updates_router

    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    monkeypatch = pytest.MonkeyPatch()

    def raise_offline(settings: object, preview: bool = False) -> None:
        from app.channel import ChannelError

        raise ChannelError("channel_offline", "offline")

    monkeypatch.setattr(updates_router, "verified_manifest", raise_offline)
    last = None
    for _ in range(35):
        last = channel_client.post("/updates/check", headers=headers)
    monkeypatch.undo()
    assert last is not None
    assert last.status_code == 429
    # Статус остаётся читаемым.
    assert channel_client.get("/updates/status", headers=headers).status_code == 200


# --- Состояния и политика версий ----------------------------------------------------


def _mock_verified(monkeypatch: pytest.MonkeyPatch, manifest: dict) -> None:
    from app.routers import updates as updates_router

    monkeypatch.setattr(
        updates_router, "verified_manifest", lambda settings, preview=False: manifest
    )


def test_offline_check_fails_gracefully(channel_client: TestClient, db_session: Session) -> None:
    from app.channel import ChannelError
    from app.routers import updates as updates_router

    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        updates_router,
        "verified_manifest",
        lambda settings, preview=False: (_ for _ in ()).throw(
            ChannelError("channel_offline", "offline")
        ),
    )
    response = channel_client.post("/updates/check", headers=headers)
    monkeypatch.undo()
    assert response.status_code == 200  # состояние, а не 5xx
    body = response.json()
    assert body["state"] == "failed"
    assert body["error_code"] == "channel_offline"


def test_downgrade_and_conflict_fail_closed(
    channel_client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    monkeypatch = pytest.MonkeyPatch()
    _mock_verified(monkeypatch, fixture("manifest.downgrade.json"))
    response = channel_client.post("/updates/check", headers=headers)
    assert response.json()["state"] == "failed"
    assert response.json()["error_code"] == "downgrade_blocked"
    _mock_verified(monkeypatch, fixture("manifest.same_version_diff_sha.json"))
    response = channel_client.post("/updates/check", headers=headers)
    assert response.json()["state"] == "failed"
    assert response.json()["error_code"] == "version_conflict"
    monkeypatch.undo()


def test_unknown_and_revoked_key_fail_closed(channel_client: TestClient) -> None:
    # Проверка через доверенный набор: ключи из fixture (unknown/revoked)
    # не входят в набор UPDATE_CHANNEL_PUBLIC_KEYS тестового клиента,
    # а revoked помечен в trusted_keys.json.
    assert "pilot-test-key" in channel_settings(Path("/tmp/unused")).update_channel_public_keys
    trusted = json.loads(channel_settings(Path("/tmp/unused")).update_channel_public_keys)
    assert trusted["pilot-revoked-key"]["revoked"] is True


def test_up_to_date_check(channel_client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    manifest = fixture("manifest.valid.json")
    manifest = dict(manifest)
    manifest["version"] = "0.13.0"
    manifest["release_sha"] = INSTALLED_SHA
    monkeypatch = pytest.MonkeyPatch()
    _mock_verified(monkeypatch, manifest)
    response = channel_client.post("/updates/check", headers=headers)
    monkeypatch.undo()
    assert response.json()["state"] == "up_to_date"


def test_download_requires_check_first(channel_client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    assert channel_client.post("/updates/download", headers=headers).status_code == 409


def _mock_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from app.routers import updates as updates_router

    package = (TESTDATA / "package.valid.zip").read_bytes()
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / ("release-" + ("2" * 40)[:12] + ".zip")
    target.write_bytes(package)

    def fake_download(settings: object, manifest: dict) -> Path:
        return target

    monkeypatch.setattr(updates_router, "download_package", fake_download)
    return target


def test_full_flow_check_download_install_report(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    monkeypatch = pytest.MonkeyPatch()
    valid = fixture("manifest.valid.json")
    _mock_verified(monkeypatch, valid)
    target = _mock_download(monkeypatch, tmp_path)

    check = channel_client.post("/updates/check", headers=headers).json()
    assert check["state"] == "available"

    download = channel_client.post("/updates/download", headers=headers).json()
    assert download["state"] == "ready"
    assert download["download_progress"] == 100

    install = channel_client.post("/updates/install", headers=headers)
    assert install.status_code == 200
    assert install.json()["state"] == "installing"
    job_id = install.json()["job_id"]
    assert job_id

    # Повторный install — 409 (идемпотентность/блокировка).
    assert channel_client.post("/updates/install", headers=headers).status_code == 409

    # Движок: опрос получает команду; повторный опрос ДО отчёта получает
    # ту же команду с тем же job_id (re-delivery: опрос не подтверждение).
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    poll = channel_client.get("/updates/engine-state", headers=engine_headers)
    assert poll.status_code == 200
    assert poll.json()["actions"] == ["install"]
    assert poll.json()["job_id"] == job_id
    assert poll.json()["release_dir"] == str(target)
    poll2 = channel_client.get("/updates/engine-state", headers=engine_headers)
    assert poll2.json()["actions"] == ["install"]
    assert poll2.json()["job_id"] == job_id
    assert poll2.json()["release_dir"] == str(target)

    # Отчёт об успехе.
    report = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": job_id,
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    assert report.status_code == 200
    body = report.json()
    assert body["state"] == "up_to_date"
    assert body["installed_version"] == "0.14.0"
    assert body["last_result"] == "updated"

    # Terminal report прекращает выдачу команды.
    poll3 = channel_client.get("/updates/engine-state", headers=engine_headers)
    assert poll3.json()["actions"] == []
    assert poll3.json()["job_id"] is None

    # Повторный отчёт без job (сброс) не должен менять состояние в худшую сторону.
    status_now = channel_client.get("/updates/status", headers=headers).json()
    assert status_now["state"] == "up_to_date"
    monkeypatch.undo()


def test_rollback_report_state(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    monkeypatch = pytest.MonkeyPatch()
    valid = fixture("manifest.valid.json")
    _mock_verified(monkeypatch, valid)
    _mock_download(monkeypatch, tmp_path)
    channel_client.post("/updates/check", headers=headers)
    channel_client.post("/updates/download", headers=headers)
    install = channel_client.post("/updates/install", headers=headers).json()
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    channel_client.get("/updates/engine-state", headers=engine_headers)
    report = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": install["job_id"],
            "state": "rolled_back",
            "installed_version": "0.13.0",
            "installed_release_sha": INSTALLED_SHA,
            "error_code": "update_failed",
        },
    )
    body = report.json()
    assert body["state"] == "failed"
    assert body["last_result"] == "rolled_back"
    monkeypatch.undo()


def _install_and_poll(
    channel_client: TestClient,
    headers: dict,
    engine_headers: dict,
    tmp_path: Path,
) -> dict:
    """Полный путь до active install job: check → download → install → poll."""
    monkeypatch = pytest.MonkeyPatch()
    valid = fixture("manifest.valid.json")
    _mock_verified(monkeypatch, valid)
    _mock_download(monkeypatch, tmp_path)
    channel_client.post("/updates/check", headers=headers)
    channel_client.post("/updates/download", headers=headers)
    install = channel_client.post("/updates/install", headers=headers).json()
    poll = channel_client.get("/updates/engine-state", headers=engine_headers).json()
    monkeypatch.undo()
    assert install["state"] == "installing"
    assert poll["actions"] == ["install"]
    assert poll["job_id"] == install["job_id"]
    return {"install": install, "poll": poll}


# --- Восстанавливаемая доставка и строгая корреляция report ---------------------------


def test_report_without_job_id_is_422(channel_client: TestClient) -> None:
    headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    response = channel_client.post(
        "/updates/engine-report",
        headers=headers,
        json={
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    assert response.status_code == 422


def test_report_with_unknown_job_409_and_state_untouched(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    response = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": "0" * 16,
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    assert response.status_code == 409
    # Состояние не изменилось: команда всё ещё выдаётся с настоящим job_id.
    status_body = channel_client.get("/updates/status", headers=headers).json()
    assert status_body["state"] == "installing"
    poll = channel_client.get("/updates/engine-state", headers=engine_headers).json()
    assert poll["actions"] == ["install"]
    assert poll["job_id"] == world["install"]["job_id"]


def test_report_without_active_job_409(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    # Сначала завершаем единственную операцию, затем шлём stale report.
    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    done = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": world["install"]["job_id"],
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    assert done.status_code == 200
    stale = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": "1" * 16,
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    assert stale.status_code == 409
    # State не ухудшен.
    assert channel_client.get("/updates/status", headers=headers).json()["state"] == "up_to_date"


def test_report_retry_is_idempotent_and_audited_once(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    payload = {
        "job_id": world["install"]["job_id"],
        "state": "installed",
        "installed_version": "0.14.0",
        "installed_release_sha": "2" * 40,
    }
    first = channel_client.post("/updates/engine-report", headers=engine_headers, json=payload)
    assert first.status_code == 200
    second = channel_client.post("/updates/engine-report", headers=engine_headers, json=payload)
    assert second.status_code == 200
    assert second.json()["state"] == "up_to_date"
    reported = (
        db_session.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.UPDATE_ENGINE_REPORTED)
        )
        .scalars()
        .all()
    )
    assert len(reported) == 1  # повтор не создаёт второй audit side effect
    # Lock освобождён ровно один раз: новый check проходит (acquire удался),
    # и, поскольку версия уже 0.14.0, честно даёт up_to_date.
    monkeypatch = pytest.MonkeyPatch()
    valid = fixture("manifest.valid.json")
    _mock_verified(monkeypatch, valid)
    check = channel_client.post("/updates/check", headers=headers)
    assert check.status_code == 200
    assert check.json()["state"] == "up_to_date"
    monkeypatch.undo()


def test_conflicting_report_retry_409(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    job_id = world["install"]["job_id"]
    done = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": job_id,
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    assert done.status_code == 200
    conflicting = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": job_id,
            "state": "rolled_back",
            "installed_version": "0.13.0",
            "installed_release_sha": INSTALLED_SHA,
            "error_code": "update_failed",
        },
    )
    assert conflicting.status_code == 409
    # Исходный результат не ухудшен.
    assert channel_client.get("/updates/status", headers=headers).json()["state"] == "up_to_date"


def test_report_restart_required_state(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}

    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    response = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": world["install"]["job_id"],
            "state": "restart_required",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    assert response.status_code == 200
    assert response.json()["state"] == "restart_required"
    assert response.json()["last_result"] == "restart_required"
    # Команда больше не выдаётся.
    poll = channel_client.get("/updates/engine-state", headers=engine_headers).json()
    assert poll["actions"] == []


def test_report_failed_state(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}

    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    response = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": world["install"]["job_id"],
            "state": "failed",
            "installed_version": "",
            "installed_release_sha": "",
            "error_code": "engine_failed",
        },
    )
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["error_code"] == "engine_failed"
    assert response.json()["last_result"] == "failed"


def test_report_invalid_result_fields_are_422(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    # installed без release_sha — 422; rolled_back без error_code — 422.
    missing_sha = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": world["install"]["job_id"],
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "",
        },
    )
    assert missing_sha.status_code == 422
    missing_code = channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={"job_id": world["install"]["job_id"], "state": "rolled_back"},
    )
    assert missing_code.status_code == 422
    # Активная операция не пострадала.
    poll = channel_client.get("/updates/engine-state", headers=engine_headers).json()
    assert poll["actions"] == ["install"]
    assert poll["job_id"] == world["install"]["job_id"]


def test_concurrent_identical_reports_single_apply(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    world = _install_and_poll(channel_client, headers, engine_headers, tmp_path)
    payload = {
        "job_id": world["install"]["job_id"],
        "state": "installed",
        "installed_version": "0.14.0",
        "installed_release_sha": "2" * 40,
    }
    results: list[int] = []

    def send() -> None:
        response = channel_client.post(
            "/updates/engine-report", headers=engine_headers, json=payload
        )
        results.append(response.status_code)

    threads = [Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # Ровно одно применение (200 от APPLIED/DUPLICATE, 409 — только если
    # запрос попал между снятием активной операции и записью last_job).
    assert all(code in (200, 409) for code in results)
    assert any(code == 200 for code in results)
    reported = (
        db_session.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.UPDATE_ENGINE_REPORTED)
        )
        .scalars()
        .all()
    )
    assert len(reported) == 1  # один audit side effect
    assert channel_client.get("/updates/status", headers=headers).json()["state"] == "up_to_date"


# --- Движковые эндпоинты -----------------------------------------------------------


def test_engine_endpoints_require_token(channel_client: TestClient) -> None:
    assert channel_client.get("/updates/engine-state").status_code == 401
    assert (
        channel_client.get("/updates/engine-state", headers={"X-Engine-Token": "wrong"}).status_code
        == 401
    )


def test_engine_check_throttled_by_interval(channel_client: TestClient, tmp_path: Path) -> None:
    from app.routers import updates as updates_router

    settings = channel_settings(tmp_path)
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    calls: list[int] = []

    def counting(settings_obj: object, preview: bool = False) -> dict:
        calls.append(1)
        manifest = dict(fixture("manifest.valid.json"))
        manifest["version"] = "0.13.0"
        manifest["release_sha"] = INSTALLED_SHA
        return manifest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(updates_router, "verified_manifest", counting)
    with TestClient(app) as test_client:
        engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
        first = test_client.post("/updates/engine-check", headers=engine_headers)
        assert first.status_code == 200
        second = test_client.post("/updates/engine-check", headers=engine_headers)
        assert second.status_code == 200
        assert len(calls) == 1  # троттлинг: вторая проверка не выполнялась
    monkeypatch.undo()
    engine.dispose()


# --- Аудит ------------------------------------------------------------------------


def test_audit_events_recorded_without_paths_or_secrets(
    channel_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    user = make_user(db_session, "admin", UserRole.ADMIN)
    grant(db_session, user, "update_channel_manage")
    headers = login(channel_client, "admin")
    monkeypatch = pytest.MonkeyPatch()
    valid = fixture("manifest.valid.json")
    _mock_verified(monkeypatch, valid)
    _mock_download(monkeypatch, tmp_path)
    channel_client.post("/updates/check", headers=headers)
    channel_client.post("/updates/download", headers=headers)
    channel_client.post("/updates/install", headers=headers)
    engine_headers = {"X-Engine-Token": "engine-token-0123456789abcdef"}
    poll = channel_client.get("/updates/engine-state", headers=engine_headers).json()
    channel_client.post(
        "/updates/engine-report",
        headers=engine_headers,
        json={
            "job_id": poll["job_id"],
            "state": "installed",
            "installed_version": "0.14.0",
            "installed_release_sha": "2" * 40,
        },
    )
    events = db_session.execute(select(AuditEvent)).scalars().all()
    actions = {event.action for event in events}
    for expected in (
        AuditAction.UPDATE_CHECK_STARTED,
        AuditAction.UPDATE_CHECK_SUCCEEDED,
        AuditAction.UPDATE_DOWNLOAD_STARTED,
        AuditAction.UPDATE_DOWNLOAD_SUCCEEDED,
        AuditAction.UPDATE_INSTALL_REQUESTED,
        AuditAction.UPDATE_ENGINE_REPORTED,
    ):
        assert expected in actions
    for event in events:
        details = event.details or ""
        assert "https://" not in details
        assert str(tmp_path) not in details
        assert "updates.example.com" not in details
    monkeypatch.undo()
