"""Unit tests for the ops contour endpoints (SQLite, no subprocesses).

The API wiring is covered here (status signals, metrics without PII, backup
health degradation, admin RBAC + audit). Real pg_dump/pg_restore executions
are covered by the PostgreSQL integration tests — the trigger test below only
asserts that the endpoint delegates to the runner and never fabricates a
successful backup.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.backup import BackupRecord, BackupState, save_state
from app.backup_runner import BackupOutcome, RunnerConfig
from app.config import Settings
from app.models import AuditAction, User, UserRole
from tests.conftest import FIXTURE_PASSWORD, make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _fresh_state_file(tmp_path: Path, *, status: str = "ok", age_hours: int = 2) -> str:
    state_path = tmp_path / "state.json"
    state = BackupState(
        last_backup=BackupRecord(
            file="hr-manager-20260904T100000Z-abcdef12.pgdump.enc",
            at=(NOW - timedelta(hours=age_hours)).isoformat(),
            size=4096,
            enc_sha256="a" * 64,
            status=status,
        ),
        last_drill={
            "at": (NOW - timedelta(days=1)).isoformat(),
            "ok": True,
            "file": "hr-manager-20260904T100000Z-abcdef12.pgdump.enc",
        },
        last_check={"at": NOW.isoformat(), "ok": True, "file": "x"},
    )
    save_state(state_path, state)
    return str(state_path)


# --- public signals ------------------------------------------------------------


def test_ops_status_shape_and_no_backup_dir(client: TestClient, unit_settings: Settings) -> None:
    response = client.get("/ops/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "hr-manager"
    assert body["environment"] == "test"
    assert body["release_sha"] == ""
    assert body["database"]["status"] == "ok"
    assert body["migrations"]["ok"] is None  # SQLite has no alembic_version table
    assert body["backup"]["available"] is False
    assert body["backup"]["ok"] is None
    assert "password" not in json.dumps(body).lower()


def test_ops_status_reflects_release_sha_and_backup_state(
    client: TestClient, unit_settings: Settings, tmp_path: Path
) -> None:
    unit_settings.release_sha = "0123456789abcdef0123456789abcdef01234567"
    unit_settings.backup_state_file = _fresh_state_file(tmp_path)
    body = client.get("/ops/status").json()
    assert body["release_sha"] == unit_settings.release_sha
    backup = body["backup"]
    assert backup["available"] is True
    assert backup["ok"] is True
    assert backup["size_bytes"] == 4096
    assert backup["age_seconds"] is not None and backup["age_seconds"] < 3 * 3600
    assert backup["last_drill_ok"] is True


def test_ops_status_migrations_signal_matches_head(
    client: TestClient, unit_settings: Settings
) -> None:
    # Expected head resolves from the repo's alembic scripts even on SQLite.
    body = client.get("/ops/status").json()
    assert body["migrations"]["expected_revision"] is not None
    assert body["migrations"]["current_revision"] is None


def test_ops_metrics_renders_counters_without_query_strings(client: TestClient) -> None:
    client.get("/health")
    client.get("/ops/status?secret=should-not-appear")
    client.get("/ops/status")
    response = client.get("/ops/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert "hr_manager_http_requests_total" in text
    assert 'path="/ops/status"' in text
    assert 'path="/health"' in text
    assert "should-not-appear" not in text
    assert "secret=" not in text
    assert "hr_manager_http_duration_seconds_bucket" in text
    assert "hr_manager_uptime_seconds" in text


def test_ops_backup_health_degrades_without_backups(client: TestClient) -> None:
    response = client.get("/ops/backup-health")
    assert response.status_code == 503
    assert response.json()["fresh"] is False
    assert response.json()["status"] == "degraded"


def test_ops_backup_health_ok_with_fresh_backup(
    client: TestClient, unit_settings: Settings, tmp_path: Path
) -> None:
    unit_settings.backup_state_file = _fresh_state_file(tmp_path, age_hours=1)
    response = client.get("/ops/backup-health")
    assert response.status_code == 200
    assert response.json()["fresh"] is True


def test_ops_backup_health_503_for_stale_backup(
    client: TestClient, unit_settings: Settings, tmp_path: Path
) -> None:
    unit_settings.backup_state_file = _fresh_state_file(tmp_path, age_hours=30)
    response = client.get("/ops/backup-health")
    assert response.status_code == 503
    body = response.json()
    assert body["fresh"] is False
    assert body["age_seconds"] > 26 * 3600


# --- admin RBAC and audit ------------------------------------------------------


def test_admin_ops_backups_requires_admin(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="boss", role=UserRole.MANAGER)
    make_user(db_session, username="admin1", role=UserRole.ADMIN)

    assert client.get("/admin/ops/backups").status_code == 401  # anonymous
    _login(client, "hr1")
    assert client.get("/admin/ops/backups").status_code == 403  # HR
    _login(client, "boss")
    assert client.get("/admin/ops/backups").status_code == 403  # manager
    csrf = _login(client, "admin1")
    response = client.get("/admin/ops/backups", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["last_backup"] is None


def test_record_release_is_audited(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="admin1", role=UserRole.ADMIN)
    csrf = _login(client, "admin1")
    response = client.post(
        "/admin/ops/releases",
        json={"sha": "0123456789abcdef", "status": "deployed", "details": "release notes"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 204

    from sqlalchemy import select

    from app.models import AuditEvent

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.DEPLOY_RECORDED)
    ).all()
    assert len(events) == 1
    assert events and "0123456789abcdef" in (events[0].details or "")
    assert "password" not in (events[0].details or "").lower()


def test_backup_trigger_rejects_non_admin_and_missing_tooling(
    client: TestClient, db_session: Session, unit_settings: Settings
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    make_user(db_session, username="admin1", role=UserRole.ADMIN)
    unit_settings.backup_pgdump_bin = "/definitely/missing/pg_dump"

    _login(client, "hr1")
    response = client.post(
        "/admin/ops/backup", json={"reason": "smoke"}, headers={"X-CSRF-Token": "x"}
    )
    assert response.status_code == 403

    csrf = _login(client, "admin1")
    response = client.post(
        "/admin/ops/backup", json={"reason": "smoke"}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 503
    assert "backup tooling" in response.json()["detail"]


def test_backup_trigger_delegates_to_runner_and_records_audit(
    client: TestClient,
    db_session: Session,
    unit_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Endpoint plumbing: 202 + background delegation. The runner itself is
    exercised for real in the PostgreSQL integration tests; here a recording
    stub proves the endpoint never fabricates a backup result of its own."""
    import app.routers.ops as ops_module

    make_user(db_session, username="admin1", role=UserRole.ADMIN)
    unit_settings.backup_pgdump_bin = "/bin/true"  # executable exists
    unit_settings.backup_dir = str(tmp_path)
    unit_settings.backup_state_file = str(tmp_path / "state.json")

    calls: list[dict] = []

    def fake_runner(
        cfg: RunnerConfig,
        *,
        actor: User | None,
        actor_name: str,
        reason: str,
        request_id: str,
        keys: dict[str, bytes] | None = None,
        key_id: str | None = None,
    ) -> BackupOutcome:
        calls.append(
            {
                "reason": reason,
                "request_id": request_id,
                "actor": actor.username if actor else None,
                "actor_name": actor_name,
            }
        )
        return BackupOutcome(ok=False, exit_code=1, message="stub failure", request_id=request_id)

    monkeypatch.setattr(ops_module, "run_backup", fake_runner)
    csrf = _login(client, "admin1")
    response = client.post(
        "/admin/ops/backup",
        json={"reason": "перед обновлением", "request_id": "req-123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202
    assert response.json()["request_id"] == "req-123"

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not calls:
        time.sleep(0.05)
    assert len(calls) == 1
    assert calls[0]["reason"] == "перед обновлением"
    assert calls[0]["actor"] == "admin1"

    # The failure was recorded in the audit trail (no fabricated success).
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        from sqlalchemy import select

        from app.models import AuditEvent

        rows = db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.BACKUP_FAILED)
        ).all()
        if rows:
            break
        time.sleep(0.05)
    assert len(rows) == 1
    assert rows and "stub failure" in (rows[0].details or "")


def test_backup_trigger_rejects_duplicate_request_id(
    client: TestClient,
    db_session: Session,
    unit_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app.routers.ops as ops_module

    make_user(db_session, username="admin1", role=UserRole.ADMIN)
    unit_settings.backup_pgdump_bin = "/bin/true"
    unit_settings.backup_dir = str(tmp_path)
    unit_settings.backup_state_file = str(tmp_path / "state.json")

    def slow_runner(
        cfg: RunnerConfig,
        *,
        actor: User | None,
        actor_name: str,
        reason: str,
        request_id: str,
        keys: dict[str, bytes] | None = None,
        key_id: str | None = None,
    ) -> BackupOutcome:
        time.sleep(2)
        return BackupOutcome(ok=True, exit_code=0, message="ok", request_id=request_id)

    monkeypatch.setattr(ops_module, "run_backup", slow_runner)
    csrf = _login(client, "admin1")
    first = client.post(
        "/admin/ops/backup",
        json={"reason": "первый", "request_id": "dup-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 202
    time.sleep(0.3)
    second = client.post(
        "/admin/ops/backup",
        json={"reason": "второй", "request_id": "dup-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 409
