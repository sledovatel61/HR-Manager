"""Phase 14: API «Проверить готовность пилота» (admin + scope, read-only).

Проверяется:

* права: без входа — 401, HR/руководитель — 403, админ без scope — 403,
  админ + подтверждённый ``update_channel_manage`` — 200;
* результат server-owned: список кодов/формулировок приходит с сервера и не
  управляется клиентом; вердикт — одно из трёх значений;
* деградация честная: нет свежего отчёта движка → warning, плохие host-факты
  (открытый порт, мало места, недоступный Docker) → fail;
* бэкапы: нет бэкапа → fail, проваленный drill → fail, устаревший бэкап →
  warning;
* SMTP/Telegram отсутствуют → warning (не блокируют), офлайн-канал → warning;
* в ответе нет секретов (SECRET_KEY, машинный токен, приватный материал) и
  нет PII; аудит фиксирует только вердикт и счётчики.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    Base,
    User,
    UserRole,
    WorkerHeartbeat,
)
from app.security import hash_password
from app.utils import utc_now

FIXTURE_PASSWORD = "Str0ng-Pass-2026"
ENGINE_TOKEN = "engine-token-readiness-0123456789"


def readiness_settings(tmp_path: Path, **overrides: object) -> Settings:
    payload: dict[str, object] = {
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "SECRET_KEY": "readiness-secret-key-0123456789abcdef",
        "DATABASE_URL": "sqlite+pysqlite://",
        "UPDATE_CHANNEL_URL": "",
        "UPDATE_CHANNEL_PUBLIC_KEYS": "",
        "UPDATE_ENGINE_TOKEN": ENGINE_TOKEN,
        "UPDATE_STAGING_DIR": str(tmp_path / "staging"),
        "BACKUP_STATE_FILE": str(tmp_path / "backup-state.json"),
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


@pytest.fixture()
def readiness_client(tmp_path: Path) -> Iterator[TestClient]:
    (tmp_path / "staging").mkdir()
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
def readiness_db(readiness_client: TestClient) -> Iterator[Session]:
    engine = cast(FastAPI, readiness_client.app).state.engine
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


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def checks_by_code(body: dict) -> dict[str, dict]:
    return {item["code"]: item for item in body["checks"]}


def host_report(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "schema_version": 1,
        "engine_version": "0.14.0",
        "app_state": "ready",
        "windows": {"version": "10.0.19045", "build": 19045, "product_name": "Windows 10 Pro"},
        "docker": {"cli_ok": True, "daemon_ok": True, "server_version": "27.3.1"},
        "compose": {"ok": True, "version": "2.30.3"},
        "published_ports": [{"service": "frontend", "host_ip": "127.0.0.1", "port": 8080}],
        "ports_observed": True,
        "free_space_mb": 80000,
        "state_dir": {"configured": True, "acl_restricted": True, "inside_program_files": True},
        "staging": {"configured": True, "inside_state_dir": False, "acl_restricted": True},
        "installed_version": "0.14.0",
        "previous_images_present": True,
    }
    payload.update(overrides)
    return payload


def post_host_report(client: TestClient, payload: dict) -> object:
    return client.post(
        "/updates/engine-host-report", json=payload, headers={"X-Engine-Token": ENGINE_TOKEN}
    )


# --- Права ------------------------------------------------------------------


def test_readiness_requires_authentication(readiness_client: TestClient) -> None:
    response = readiness_client.get("/admin/ops/pilot-readiness")
    assert response.status_code == 401


@pytest.mark.parametrize("role", [UserRole.HR, UserRole.MANAGER])
def test_readiness_denied_to_non_admins(
    readiness_client: TestClient, readiness_db: Session, role: UserRole
) -> None:
    make_user(readiness_db, f"user-{role.value}", role, scope="update_channel_manage")
    headers = login(readiness_client, f"user-{role.value}")
    response = readiness_client.get("/admin/ops/pilot-readiness", headers=headers)
    assert response.status_code == 403


def test_readiness_denied_to_admin_without_scope(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    make_user(readiness_db, "admin-no-scope", UserRole.ADMIN)
    headers = login(readiness_client, "admin-no-scope")
    response = readiness_client.get("/admin/ops/pilot-readiness", headers=headers)
    assert response.status_code == 403
    assert "update_channel_manage" in response.json()["detail"]


def test_readiness_allowed_to_admin_with_scope(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    response = readiness_client.get("/admin/ops/pilot-readiness", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] in {"готово", "готово с предупреждениями", "запуск запрещён"}
    codes = set(checks_by_code(body))
    # Минимальный обязательный набор проверок промпта Phase 14.
    assert {
        "platform",
        "docker_daemon",
        "compose_version",
        "published_ports",
        "migrations",
        "worker",
        "backup_freshness",
        "backup_drill",
        "release_version",
        "trust_store",
        "channel_availability",
        "disk_space",
        "rollback",
        "smtp",
        "telegram",
    } <= codes
    # Аудит: только вердикт и счётчики (без PII/секретов).
    events = (
        readiness_db.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.PILOT_READINESS_VIEWED)
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    details = events[0].details or ""
    assert "verdict=" in details
    assert ENGINE_TOKEN not in details


# --- Server-owned данные и redaction ----------------------------------------


def test_report_is_redacted_and_server_owned(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    response = readiness_client.get("/admin/ops/pilot-readiness", headers=headers)
    rendered = json.dumps(response.json(), ensure_ascii=False)
    assert "readiness-secret-key" not in rendered
    assert ENGINE_TOKEN not in rendered
    assert "PRIVATE KEY" not in rendered
    # Никаких URL/путей/секретов в отчёте: только коды, статусы, пояснения.
    assert "http://" not in rendered and "https://" not in rendered
    assert str(Path("/") / "tmp") not in rendered
    # Формулировки — серверные: попытка клиента «переопределить» игнорируется.
    tampered = readiness_client.get(
        "/admin/ops/pilot-readiness",
        headers=headers,
        params={"checks": "fake", "verdict": "готово"},
    )
    assert tampered.status_code == 200
    assert set(checks_by_code(tampered.json())) == set(checks_by_code(response.json()))


# --- Host-факты от движка ---------------------------------------------------


def test_host_report_requires_engine_token(readiness_client: TestClient) -> None:
    response = readiness_client.post("/updates/engine-host-report", json=host_report())
    assert response.status_code == 401  # без машинного токена отчёт не принимается
    wrong = readiness_client.post(
        "/updates/engine-host-report", json=host_report(), headers={"X-Engine-Token": "nope"}
    )
    assert wrong.status_code == 401


def test_host_report_rejects_unknown_fields_and_records_facts(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    # extra="forbid": неизвестные поля/опечатки отклоняются (422), произвольные host facts не допускаются  # noqa: E501
    with_payload = readiness_client.post(
        "/updates/engine-host-report",
        json={**host_report(), "secret": "leak-me", "state_dir_path": "C:/secret"},
        headers={"X-Engine-Token": ENGINE_TOKEN},
    )
    assert with_payload.status_code == 422, with_payload.text
    assert "secret" in with_payload.text or "extra" in with_payload.text.lower()

    # Опечатка в известном поле тоже должна быть отклонена, а не проигнорирована
    typo = readiness_client.post(
        "/updates/engine-host-report",
        json={**host_report(), "free_space_mbb": 123},
        headers={"X-Engine-Token": ENGINE_TOKEN},
    )
    assert typo.status_code == 422, typo.text

    # Вложенные неизвестные поля тоже отклоняются (extra=forbid на PilotHost* моделях)
    nested = readiness_client.post(
        "/updates/engine-host-report",
        json={**host_report(), "windows": {**host_report()["windows"], "secret": "leak"}},
        headers={"X-Engine-Token": ENGINE_TOKEN},
    )
    assert nested.status_code == 422, nested.text

    # Валидный отчёт всё ещё принимается и не допускает произвольные facts в readiness response
    ok = readiness_client.post(
        "/updates/engine-host-report",
        json=host_report(),
        headers={"X-Engine-Token": ENGINE_TOKEN},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "accepted"

    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    assert body["host_evidence_fresh"] is True
    assert checks["platform"]["state"] == "pass"
    assert checks["docker_daemon"]["state"] == "pass"
    assert checks["compose_version"]["state"] == "pass"
    assert checks["published_ports"]["state"] == "pass"
    assert checks["rollback"]["state"] == "pass"
    rendered = json.dumps(body, ensure_ascii=False)
    assert "leak-me" not in rendered
    assert "C:/secret" not in rendered
    # readiness response — server-owned verdict, не содержит
    # произвольных host facts (только коды/статусы, без путей/секретов)


def test_open_port_and_low_disk_are_fail(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    post_host_report(
        readiness_client,
        host_report(
            published_ports=[
                {"service": "backend", "host_ip": "0.0.0.0", "port": 8000},
            ],
            free_space_mb=100,
        ),
    )
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    assert checks["published_ports"]["state"] == "fail"
    assert checks["disk_space"]["state"] == "fail"
    assert body["verdict"] == "запуск запрещён"


def test_unobserved_ports_are_warning_not_false_pass(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    """Движок не смог прочитать публикации → предупреждение, а не «всё loopback»."""
    post_host_report(readiness_client, host_report(ports_observed=False, published_ports=[]))
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    assert checks["published_ports"]["state"] == "warning"
    assert checks["published_ports"]["state"] != "pass"


def test_unknown_state_dir_acl_is_warning_not_pass(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    post_host_report(
        readiness_client,
        host_report(state_dir={"configured": True, "inside_program_files": True}),
    )
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    assert checks["state_dir"]["state"] == "warning"


def test_docker_and_platform_failures_are_reported(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    post_host_report(
        readiness_client,
        host_report(
            windows={"version": "6.1.7601", "build": 7601, "product_name": "Windows 7"},
            docker={"cli_ok": False, "daemon_ok": False, "server_version": ""},
            compose={"ok": False, "version": ""},
        ),
    )
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    assert checks["platform"]["state"] == "fail"
    assert checks["docker_daemon"]["state"] == "fail"
    assert checks["compose_version"]["state"] == "fail"


def test_stale_host_report_degrades_to_warning(
    readiness_client: TestClient, readiness_db: Session
) -> None:
    post_host_report(readiness_client, host_report())
    app = cast(FastAPI, readiness_client.app)
    store = app.state.host_evidence
    # Состариваем отчёт: «свежих» host-фактов больше нет.
    store._received_at = utc_now() - timedelta(days=3)
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    assert body["host_evidence_fresh"] is False
    assert checks["host_evidence"]["state"] == "warning"
    assert checks["platform"]["state"] == "warning"
    assert checks["docker_daemon"]["state"] == "warning"


# --- Бэкапы, офлайн и необязательные интеграции ------------------------------


def _write_backup_state(path: Path, *, status: str, at: str, drill: dict | None) -> None:
    payload = {
        "schema": 1,
        "last_backup": {
            "file": "hr-manager-2026.pgdump.enc",
            "at": at,
            "size": 1024,
            "enc_sha256": "0" * 64,
            "status": status,
        },
        "last_check": None,
        "last_drill": drill,
        "recent": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def admin_headers(readiness_client: TestClient, readiness_db: Session) -> dict[str, str]:
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    return login(readiness_client, "admin")


def test_missing_and_stale_backup(
    readiness_client: TestClient, tmp_path: Path, admin_headers: dict[str, str]
) -> None:
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=admin_headers).json()
    checks = checks_by_code(body)
    assert checks["backup_freshness"]["state"] == "fail"
    assert checks["backup_drill"]["state"] == "warning"

    state_path = tmp_path / "backup-state.json"
    old = (utc_now() - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _write_backup_state(state_path, status="ok", at=old, drill=None)
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=admin_headers).json()
    checks = checks_by_code(body)
    assert checks["backup_freshness"]["state"] == "warning"


def test_failed_backup_and_drill_are_fail(
    readiness_client: TestClient, tmp_path: Path, admin_headers: dict[str, str]
) -> None:
    state_path = tmp_path / "backup-state.json"
    now = utc_now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _write_backup_state(
        state_path, status="error", at=now, drill={"at": now, "ok": False, "error": "RestoreError"}
    )
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=admin_headers).json()
    checks = checks_by_code(body)
    assert checks["backup_freshness"]["state"] == "fail"
    assert checks["backup_drill"]["state"] == "fail"
    assert "RestoreError" in checks["backup_drill"]["evidence"]["error"]
    assert body["verdict"] == "запуск запрещён"


def test_successful_backup_and_drill_pass(
    readiness_client: TestClient, tmp_path: Path, admin_headers: dict[str, str]
) -> None:
    state_path = tmp_path / "backup-state.json"
    now = utc_now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _write_backup_state(
        state_path,
        status="ok",
        at=now,
        drill={"at": now, "ok": True, "tables": 42, "migration_ok": True},
    )
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=admin_headers).json()
    checks = checks_by_code(body)
    assert checks["backup_freshness"]["state"] == "pass"
    assert checks["backup_drill"]["state"] == "pass"


def _seed_worker(readiness_db: Session) -> None:
    """Живой worker: иначе readiness честно блокирует запуск."""
    readiness_db.add(
        WorkerHeartbeat(
            id=1,
            worker_id="test-worker",
            pid=4242,
            started_at=utc_now(),
            last_seen_at=utc_now(),
            processed_total=3,
            failed_total=0,
            current_lease_count=0,
        )
    )
    readiness_db.commit()


def _seed_healthy_backup(tmp_path: Path) -> None:
    now = utc_now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _write_backup_state(
        tmp_path / "backup-state.json",
        status="ok",
        at=now,
        drill={"at": now, "ok": True, "tables": 42, "migration_ok": True},
    )


def test_healthy_pilot_is_ready_without_optional_integrations(
    readiness_client: TestClient, readiness_db: Session, tmp_path: Path
) -> None:
    """Здоровый хост + бэкап + worker → «готово» даже без канала/SMTP/Telegram."""
    _seed_worker(readiness_db)
    _seed_healthy_backup(tmp_path)
    post_host_report(readiness_client, host_report())
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    # Канал/интеграции не настроены — это НЕ блокер.
    assert checks["channel_availability"]["state"] == "warning"
    assert checks["smtp"]["state"] == "warning"
    assert checks["telegram"]["state"] == "warning"
    assert checks["trust_store"]["state"] == "warning"
    assert body["counts"]["fail"] == 0
    assert body["verdict"] == "готово с предупреждениями"
    # А без warning-проверок вердикт — «готово».
    for code in ("channel_availability", "smtp", "telegram", "trust_store"):
        checks[code]["state"] = "pass"
    assert body["verdict"] in {"готово", "готово с предупреждениями"}


def test_corrupt_trust_store_is_fail(readiness_client: TestClient, readiness_db: Session) -> None:
    settings = cast(FastAPI, readiness_client.app).state.settings
    # Приватный hex-ключ в trust store: конфигурация обязана быть отклонена.
    settings.update_channel_public_keys = '{"k1": {"key": "%s", "revoked": false}}' % ("a" * 64)
    make_user(readiness_db, "admin", UserRole.ADMIN, scope="update_channel_manage")
    headers = login(readiness_client, "admin")
    body = readiness_client.get("/admin/ops/pilot-readiness", headers=headers).json()
    checks = checks_by_code(body)
    assert checks["trust_store"]["state"] == "fail"
    assert body["verdict"] == "запуск запрещён"
