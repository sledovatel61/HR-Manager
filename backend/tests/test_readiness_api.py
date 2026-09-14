"""Тесты предпусковой проверки готовности пилота (Phase 14).

Проверяется read-only readiness API поверх существующей диагностики:
RBAC (unauthenticated/HR/manager denied; admin без scope denied; admin +
``update_channel_manage`` получает redacted server-owned результат), вердикты
``ready | ready_with_warnings | blocked``, честные warning/fail для stale
backup/проваленного restore drill/открытого порта БД/нехватки места,
необязательность SMTP/Telegram и offline-канала (никогда не блокируют
основную работу), закрытая схема host facts от движка и отсутствие секретов
в ответах.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import AccessGrant, AccessGrantScope, AuditAction, Base, User, UserRole
from app.security import hash_password
from app.worker import heartbeat

FIXTURE_PASSWORD = "Str0ng-Pass-2026"
TESTDATA = Path(__file__).resolve().parents[2] / "infra" / "release" / "testdata"
INSTALLED_SHA = "3" * 40
ENGINE_TOKEN = "engine-token-0123456789abcdef"
SECRET_KEY = "unit-test-secret-key"

GOOD_FACTS = {
    "windows_version": "windows_11",
    "windows_supported": True,
    "docker_state": "ok",
    "compose_version": "v2.29.7",
    "compose_ok": True,
    "published_ports": [{"host_ip": "127.0.0.1", "host_port": 8080, "container": "frontend"}],
    "disk_free_mb": 40960,
    "state_dir_acl_ok": True,
    "staging_writable": True,
    "staging_outside_state": True,
    "previous_images_present": True,
    "watcher_running": True,
}


def readiness_settings(tmp_path: Path, **overrides: str) -> Settings:
    trusted = json.loads((TESTDATA / "trusted_keys.json").read_text(encoding="utf-8"))
    values = {
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "SECRET_KEY": SECRET_KEY,
        "DATABASE_URL": "sqlite+pysqlite://",
        "LOGIN_MAX_FAILURES": "5",
        "LOGIN_LOCK_MINUTES": "15",
        "UPDATE_CHANNEL_URL": "https://updates.example.com/hrm/manifest.json",
        "UPDATE_CHANNEL_PUBLIC_KEYS": json.dumps(trusted),
        "UPDATE_ENGINE_TOKEN": ENGINE_TOKEN,
        "UPDATE_INSTALLED_VERSION": "0.13.0",
        "UPDATE_INSTALLED_SHA": INSTALLED_SHA,
        "UPDATE_CHECK_MIN_INTERVAL_SECONDS": "300",
        "UPDATE_STAGING_DIR": str(tmp_path / "staging"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "BACKUP_STATE_FILE": str(tmp_path / "backups" / "state.json"),
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = readiness_settings(tmp_path)
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app(settings, engine=engine)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


@pytest.fixture()
def db_session(client: TestClient) -> Iterator[Session]:
    engine = cast(FastAPI, client.app).state.engine
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
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def post_facts(client: TestClient, facts: dict | None = None) -> httpx.Response:
    payload = dict(GOOD_FACTS) if facts is None else facts
    return client.post(
        "/updates/engine-facts",
        json=payload,
        headers={"X-Engine-Token": ENGINE_TOKEN},
    )


# --- Авторизация ---------------------------------------------------------------


def test_readiness_requires_authentication(client: TestClient) -> None:
    assert client.get("/updates/readiness").status_code == 401


def test_readiness_denied_for_hr_and_manager(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "hr", UserRole.HR, scope="update_channel_manage")
    make_user(db_session, "manager", UserRole.MANAGER, scope="update_channel_manage")
    login(client, "hr")
    assert client.get("/updates/readiness").status_code == 403
    client.post("/auth/logout")
    login(client, "manager")
    assert client.get("/updates/readiness").status_code == 403


def test_readiness_denied_for_admin_without_scope(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin_noscope", UserRole.ADMIN)
    login(client, "admin_noscope")
    assert client.get("/updates/readiness").status_code == 403


def test_readiness_allowed_for_admin_with_scope(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    response = client.get("/updates/readiness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] in ("ready", "ready_with_warnings", "blocked")
    assert {check["state"] for check in body["checks"]}
    assert body["release_version"]
    assert body["release_sha"] == INSTALLED_SHA


def test_readiness_is_audited_without_secrets(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    response = client.get("/updates/readiness")
    assert response.status_code == 200
    # Действие различимо в аудите; детали не содержат секретов/PII.
    from app.models import AuditEvent

    records = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == AuditAction.PILOT_READINESS_CHECKED)
        .all()
    )
    assert records, "pilot_readiness_checked должен попадать в аудит"
    details = " ".join(record.details or "" for record in records)
    assert "verdict=" in details
    assert SECRET_KEY not in details
    assert FIXTURE_PASSWORD not in details
    assert ENGINE_TOKEN not in details


def test_readiness_rate_limited(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    codes = {client.get("/updates/readiness").status_code for _ in range(35)}
    assert 429 in codes


# --- Host facts: закрытая схема + машинный токен --------------------------------


def test_engine_facts_requires_token(client: TestClient) -> None:
    assert client.post("/updates/engine-facts", json=GOOD_FACTS).status_code in (401, 404)
    response = client.post(
        "/updates/engine-facts", json=GOOD_FACTS, headers={"X-Engine-Token": "wrong"}
    )
    assert response.status_code in (401, 404)


def test_engine_facts_rejects_unknown_fields(client: TestClient) -> None:
    payload = dict(GOOD_FACTS)
    payload["secret_hint"] = "hunter2"
    response = post_facts(client, payload)
    assert response.status_code == 422


def test_engine_facts_rejects_free_text_and_bad_ports(client: TestClient) -> None:
    bad = dict(GOOD_FACTS)
    bad["compose_version"] = "version; rm -rf /"
    assert post_facts(client, bad).status_code == 422
    bad_ports = dict(GOOD_FACTS)
    bad_ports["published_ports"] = [
        {"host_ip": "evil.example.com", "host_port": 1, "container": "x"}
    ]
    assert post_facts(client, bad_ports).status_code == 422
    bad_kind = dict(GOOD_FACTS)
    bad_kind["docker_state"] = "everything_is_fine"
    assert post_facts(client, bad_kind).status_code == 422


def test_engine_facts_accepted_and_used(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    response = post_facts(client)
    assert response.status_code == 200
    login(client, "admin")
    body = client.get("/updates/readiness").json()
    checks = {check["code"]: check for check in body["checks"]}
    assert checks["os_supported"]["state"] == "pass"
    assert checks["docker_daemon"]["state"] == "pass"
    assert checks["compose_version"]["state"] == "pass"
    assert checks["loopback_binding"]["state"] == "pass"
    assert checks["disk_free"]["state"] == "pass"
    assert checks["state_dir_secured"]["state"] == "pass"
    assert checks["rollback_ready"]["state"] == "pass"
    assert checks["engine_watcher"]["state"] == "pass"
    # Ответ не содержит исходный отчёт и секреты.
    assert "engine-token" not in json.dumps(body)


# --- Вердикты и честные warning/fail --------------------------------------------


def _checks_by_code(client: TestClient) -> dict[str, dict]:
    body = client.get("/updates/readiness").json()
    return {check["code"]: check for check in body["checks"]}


def test_open_db_port_fails_loopback_check(client: TestClient, db_session: Session) -> None:
    exposed = dict(GOOD_FACTS)
    exposed["published_ports"] = [
        {"host_ip": "127.0.0.1", "host_port": 8080, "container": "frontend"},
        {"host_ip": "0.0.0.0", "host_port": 5432, "container": "db"},
    ]
    assert post_facts(client, exposed).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    assert checks["loopback_binding"]["state"] == "fail"
    assert client.get("/updates/readiness").json()["verdict"] == "blocked"


def test_low_disk_space_fails(client: TestClient, db_session: Session) -> None:
    low = dict(GOOD_FACTS)
    low["disk_free_mb"] = 100
    assert post_facts(client, low).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    assert checks["disk_free"]["state"] == "fail"
    assert client.get("/updates/readiness").json()["verdict"] == "blocked"


def test_unsupported_windows_fails(client: TestClient, db_session: Session) -> None:
    bad_os = dict(GOOD_FACTS)
    bad_os["windows_supported"] = False
    bad_os["windows_version"] = "other"
    assert post_facts(client, bad_os).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    assert _checks_by_code(client)["os_supported"]["state"] == "fail"


def test_docker_daemon_down_fails(client: TestClient, db_session: Session) -> None:
    down = dict(GOOD_FACTS)
    down["docker_state"] = "daemon_down"
    assert post_facts(client, down).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    assert _checks_by_code(client)["docker_daemon"]["state"] == "fail"


def test_missing_backup_fails_and_no_restore_drill_warns(
    client: TestClient, db_session: Session
) -> None:
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    assert checks["backup_fresh"]["state"] == "fail"
    assert checks["restore_drill"]["state"] == "warning"
    assert client.get("/updates/readiness").json()["verdict"] == "blocked"


def _write_backup_state(tmp_path: Path, payload: dict) -> None:
    state_file = tmp_path / "backups" / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(payload), encoding="utf-8")


def _backup_record(at: str, status: str = "ok") -> dict:
    return {
        "file": "backup-2026.pgdump.enc",
        "at": at,
        "size": 1024,
        "enc_sha256": "0" * 64,
        "status": status,
        "reason": "test",
        "request_id": "req-test",
    }


def test_stale_backup_warns(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    _write_backup_state(
        tmp_path,
        {"schema": 1, "last_backup": _backup_record(old), "recent": []},
    )
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    assert checks["backup_fresh"]["state"] == "warning"


def test_failed_restore_drill_fails(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    _write_backup_state(
        tmp_path,
        {
            "schema": 1,
            "last_backup": _backup_record(recent),
            "last_drill": {"at": recent, "ok": False},
            "recent": [],
        },
    )
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    assert checks["restore_drill"]["state"] == "fail"
    assert client.get("/updates/readiness").json()["verdict"] == "blocked"


def test_fresh_backup_and_drill_pass(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    _write_backup_state(
        tmp_path,
        {
            "schema": 1,
            "last_backup": _backup_record(recent),
            "last_check": {"at": recent, "ok": True},
            "last_drill": {"at": recent, "ok": True},
            "recent": [],
        },
    )
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    assert checks["backup_fresh"]["state"] == "pass"
    assert checks["restore_drill"]["state"] == "pass"


def test_optional_integrations_are_warnings_not_blockers(
    client: TestClient, db_session: Session
) -> None:
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    # Отключённые SMTP/Telegram — warning, не fail.
    assert checks["smtp_optional"]["state"] == "warning"
    assert checks["telegram_optional"]["state"] == "warning"


def test_offline_channel_is_warning_not_blocker(client: TestClient, db_session: Session) -> None:
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    # Канал недоступен (нет сети в тесте) — warning; вердикт не «blocked»
    # из-за канала.
    checks = _checks_by_code(client)
    assert checks["channel_https"]["state"] == "warning"
    assert "не блокируется" in checks["smtp_optional"]["explanation_ru"]


def test_worker_alive_passes(client: TestClient, db_session: Session) -> None:
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    from app.utils import utc_now

    heartbeat(db_session, worker_id="test-worker", now=utc_now())
    checks = _checks_by_code(client)
    assert checks["worker_health"]["state"] == "pass"


def test_no_secrets_or_paths_in_readiness_response(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    body = client.get("/updates/readiness").json()
    rendered = json.dumps(body, ensure_ascii=False)
    for forbidden in (
        ENGINE_TOKEN,
        SECRET_KEY,
        FIXTURE_PASSWORD,
        str(tmp_path),
        "http://",
        "https://",
    ):
        assert forbidden not in rendered, f"readiness утекает {forbidden!r}"


def test_trust_store_check_reports_key_ids_only(client: TestClient, db_session: Session) -> None:
    assert post_facts(client).status_code == 200
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    trust = checks["trust_store"]
    assert trust["state"] == "pass"
    assert trust["details"], "key_id должен быть показан"
    assert "key_id=pilot-test-key" in trust["details"][0]
    rendered = json.dumps(checks)
    # Публичный ключ целиком не возвращается.
    assert (TESTDATA / "test_key.pub").read_text(encoding="utf-8").strip() not in rendered


def test_stale_facts_degrade_to_warning(client: TestClient, db_session: Session) -> None:
    from datetime import UTC, datetime, timedelta

    assert post_facts(client).status_code == 200
    # Имитируем устаревание фактов на сервере.
    store = cast(FastAPI, client.app).state.host_facts
    with store._lock:
        store._received_at = datetime.now(UTC) - timedelta(hours=1)
    make_user(db_session, "admin", UserRole.ADMIN, scope="update_channel_manage")
    login(client, "admin")
    checks = _checks_by_code(client)
    assert checks["engine_watcher"]["state"] == "warning"
    assert checks["os_supported"]["state"] == "warning"
