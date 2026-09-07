"""Ops contour endpoints: monitoring signals and admin backup/deploy controls.

Public (no PII, no secrets, safe for the internal monitoring network):

* ``GET /ops/status``        — service/database/migrations/backup/release state
* ``GET /ops/metrics``       — Prometheus text metrics (counters + latency)
* ``GET /ops/backup-health`` — HTTP 503 when the newest backup is stale or
                               fails integrity checks (never a fake 200)

Admin only (server-side role check, CSRF-protected, fully audited):

* ``POST /admin/ops/backup``          — trigger a manual backup (reason/request id)
* ``POST /admin/ops/restore-drill``   — trigger a restore drill into a separate DB
* ``POST /admin/ops/releases``        — record a deploy/rollback release event
* ``GET  /admin/ops/backups``         — recent backup records from the state file

The manual backup/drill triggers run the SAME code as the backup CLI
(``python -m app.cli backup-now``) in a background thread; they require the
pg_dump/pg_restore tooling to be present in the container and answer 503 with
an explicit message otherwise. Nothing here returns the backup contents,
connection strings, key material or personal data.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__, metrics
from app.audit import record_event
from app.backup import freshness_ok, load_state
from app.backup_runner import RunnerConfig, run_backup, run_restore_drill
from app.db import SessionLocal, get_db, probe_database
from app.deps import require_roles
from app.models import AuditAction, NotificationOutbox, User, UserRole
from app.schemas import (
    DatabaseHealth,
    OpsBackupHealthResponse,
    OpsBackupSignal,
    OpsBackupTriggerRequest,
    OpsBackupTriggerResponse,
    OpsDrillTriggerRequest,
    OpsMigrationSignal,
    OpsReleaseRecordRequest,
    OpsStatusResponse,
    OutboxActionOut,
    OutboxRetryRequest,
    QueueDiagnosticsOut,
)
from app.utils import utc_now

router = APIRouter(tags=["ops"])

_admin_only = require_roles(UserRole.ADMIN)

# Thread registry: request_id -> thread; prevents duplicate triggers while a
# manual operation is running.
_active_operations: dict[str, threading.Thread] = {}
_active_operations_lock = threading.Lock()


def _runner_config(settings: object) -> RunnerConfig:
    from app.config import Settings

    assert isinstance(settings, Settings)
    return RunnerConfig.from_settings(settings)


def _backup_signals(settings: object) -> OpsBackupSignal:
    from app.config import Settings

    assert isinstance(settings, Settings)
    try:
        state = load_state(Path(settings.backup_state_file))
    except Exception:
        state = None
    if state is None or state.last_backup is None:
        return OpsBackupSignal(available=False)

    record = state.last_backup
    _, age = freshness_ok(state, now=utc_now(), max_age_hours=settings.backup_max_age_hours)
    drill = state.last_drill or {}
    check = state.last_check or {}
    return OpsBackupSignal(
        available=True,
        last_backup_at=record.at,
        age_seconds=round(age, 1) if age is not None else None,
        ok=record.status == "ok",
        size_bytes=record.size,
        last_check_at=check.get("at"),
        last_check_ok=check.get("ok"),
        last_drill_at=drill.get("at"),
        last_drill_ok=drill.get("ok"),
    )


def _migration_signals(engine: object) -> OpsMigrationSignal:
    current: str | None = None
    try:
        with engine.connect() as connection:  # type: ignore[attr-defined]
            row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
            current = str(row[0]) if row is not None else None
    except Exception:
        current = None

    expected: str | None = None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        repo_root = Path(__file__).resolve().parent.parent.parent
        alembic_cfg = Config(str(repo_root / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(repo_root / "alembic"))
        expected = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    except Exception:
        expected = None

    return OpsMigrationSignal(
        current_revision=current,
        expected_revision=expected,
        ok=(current == expected) if current is not None and expected is not None else None,
    )


@router.get("/ops/status", response_model=OpsStatusResponse, summary="Ops status (no PII)")
def ops_status(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> OpsStatusResponse:
    """Operational status: database, migrations, backup freshness, release,
    and the phase-8 notification queue/worker signal."""
    settings = request.app.state.settings
    engine = request.app.state.engine
    probe = probe_database(engine)
    database_ok = probe.ok
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return OpsStatusResponse(
        status="ok" if database_ok else "degraded",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        release_sha=settings.release_sha or "",
        uptime_seconds=metrics.uptime_seconds(),
        time=utc_now().isoformat(),
        database=DatabaseHealth(
            status="ok" if database_ok else "error", latency_ms=probe.latency_ms
        ),
        migrations=_migration_signals(engine),
        backup=_backup_signals(settings),
        notifications=_notifications_signal(db, utc_now()),
    )


@router.get(
    "/ops/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus text metrics (counters and latency, no PII)",
)
def ops_metrics() -> PlainTextResponse:
    """Expose aggregate metrics only — never query strings or personal data."""
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@router.get(
    "/ops/backup-health",
    response_model=OpsBackupHealthResponse,
    summary="Backup freshness/integrity health (503 when unhealthy)",
    responses={503: {"model": OpsBackupHealthResponse}},
)
def ops_backup_health(request: Request, response: Response) -> OpsBackupHealthResponse:
    """Return 503 when the newest backup is missing, stale or not 'ok'.

    This endpoint never fakes success: the result is computed from the real
    backup state file, and stale/missing backups degrade the response.
    """
    settings = request.app.state.settings
    signal = _backup_signals(settings)
    fresh = (
        signal.ok is True
        and signal.age_seconds is not None
        and signal.age_seconds <= settings.backup_max_age_hours * 3600.0
    )
    body = OpsBackupHealthResponse(
        status="ok" if fresh else "degraded",
        fresh=fresh,
        age_seconds=signal.age_seconds,
        last_backup_at=signal.last_backup_at,
        message="backup is fresh" if fresh else "backup is missing, stale or failed",
    )
    if not fresh:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body


def _pgdump_available(settings: object) -> bool:
    from app.config import Settings

    assert isinstance(settings, Settings)
    binary = settings.backup_pgdump_bin
    if os.path.isabs(binary):
        return os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


def _spawn(request_id: str, target: Callable[[], None]) -> None:
    with _active_operations_lock:
        if request_id in _active_operations:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="operation already running")
        thread = threading.Thread(target=target, name=f"ops-{request_id}", daemon=True)
        _active_operations[request_id] = thread
    thread.start()


def _finish(request_id: str) -> None:
    with _active_operations_lock:
        _active_operations.pop(request_id, None)


@router.post(
    "/admin/ops/backup",
    response_model=OpsBackupTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a manual backup (admin only)",
)
def trigger_backup(
    payload: OpsBackupTriggerRequest,
    request: Request,
    current_user: User = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> OpsBackupTriggerResponse:
    """Queue an encrypted backup with a reason; the result is audited."""
    if not _pgdump_available(request.app.state.settings):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="backup tooling is not available in this container; "
            "run 'python -m app.cli backup-now' in the backup service",
        )
    request_id = payload.request_id or uuid.uuid4().hex[:16]
    settings = request.app.state.settings
    cfg = _runner_config(settings)
    del db  # session only needed for dependency/auth side effects

    def work() -> None:
        try:
            outcome = run_backup(
                cfg,
                actor=current_user,
                actor_name=current_user.username,
                reason=payload.reason,
                request_id=request_id,
            )
            action = AuditAction.BACKUP_SUCCEEDED if outcome.ok else AuditAction.BACKUP_FAILED
            with SessionLocal() as session:
                record_event(
                    session,
                    action,
                    actor=current_user,
                    details=f"via=admin-api {outcome.message}"[:500],
                    commit=True,
                )
        finally:
            _finish(request_id)

    _spawn(request_id, work)
    return OpsBackupTriggerResponse(
        request_id=request_id,
        message="manual backup accepted; result will appear in the backup state and audit log",
    )


@router.post(
    "/admin/ops/restore-drill",
    response_model=OpsBackupTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a restore drill into a separate database (admin only)",
)
def trigger_restore_drill(
    payload: OpsDrillTriggerRequest,
    request: Request,
    current_user: User = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> OpsBackupTriggerResponse:
    """Queue a restore drill; the production database is never touched."""
    if not _pgdump_available(request.app.state.settings):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pg_restore tooling is not available in this container; "
            "run 'python -m app.cli backup-drill' in the backup service",
        )
    request_id = uuid.uuid4().hex[:16]
    settings = request.app.state.settings
    cfg = _runner_config(settings)
    del db

    def work() -> None:
        try:
            outcome = run_restore_drill(
                cfg,
                actor=current_user,
                actor_name=current_user.username,
                file=payload.file,
            )
            action = (
                AuditAction.BACKUP_RESTORE_DRILL_SUCCEEDED
                if outcome.ok
                else AuditAction.BACKUP_RESTORE_DRILL_FAILED
            )
            with SessionLocal() as session:
                record_event(
                    session,
                    action,
                    actor=current_user,
                    details=f"via=admin-api {outcome.message}"[:500],
                    commit=True,
                )
        finally:
            _finish(request_id)

    _spawn(request_id, work)
    return OpsBackupTriggerResponse(
        request_id=request_id,
        message="restore drill accepted; result will appear in the backup state and audit log",
    )


@router.post(
    "/admin/ops/releases",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Record a deploy/rollback release event (admin only)",
)
def record_release(
    payload: OpsReleaseRecordRequest,
    request: Request,
    current_user: User = Depends(_admin_only),
    db: Session = Depends(get_db),
) -> Response:
    """Persist a release event (sha + status) in the audit trail."""
    del request
    action = (
        AuditAction.DEPLOY_RECORDED if payload.status != "failed" else AuditAction.RELEASE_RECORDED
    )
    record_event(
        db,
        action,
        actor=current_user,
        details=f"sha={payload.sha[:64]} status={payload.status} details={payload.details or '-'}"[
            :500
        ],
        commit=True,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/admin/ops/backups",
    summary="Recent backup records from the state file (admin only)",
)
def list_backups(
    request: Request,
    current_user: User = Depends(_admin_only),
) -> dict:
    """Return the backup state (file names, sizes, hashes — no PII)."""
    del current_user
    settings = request.app.state.settings
    state = load_state(Path(settings.backup_state_file))
    return {
        "last_backup": state.last_backup.__dict__ if state.last_backup else None,
        "last_check": state.last_check,
        "last_drill": state.last_drill,
        "recent": [record.__dict__ for record in state.recent],
    }


# --- Phase 8: notification queue administration -------------------------------


def _notifications_signal(db: Session, now: datetime) -> dict | None:
    """Queue/worker signal for /ops/status. Degrades to None when the
    phase-8 tables are unavailable; never returns PII."""
    try:
        from app.worker import queue_counts

        return queue_counts(db, now=now)
    except Exception:
        return None


def _get_outbox_row(db: Session, outbox_id: str) -> NotificationOutbox:
    """Resolve an outbox row by id (404 for junk/foreign ids)."""

    try:
        uid = uuid.UUID(outbox_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Задание очереди не найдено."
        ) from None
    row = db.get(NotificationOutbox, uid)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Задание очереди не найдено."
        )
    return row


@router.get(
    "/admin/ops/notifications/queue",
    response_model=QueueDiagnosticsOut,
    summary="Notification queue diagnostics (admin, no PII)",
)
def notification_queue_diagnostics(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> QueueDiagnosticsOut:
    """Counts and statuses only: no titles, bodies, recipients or payloads."""
    from app.worker import queue_counts

    record_event(
        db,
        AuditAction.QUEUE_DIAGNOSTICS_VIEWED,
        actor=admin,
        details="notification queue diagnostics",
        commit=False,
    )
    data = queue_counts(db, now=utc_now())
    db.commit()
    return QueueDiagnosticsOut(
        counts=data["counts"],
        oldest_queued_at=data["oldest_queued_at"],
        stuck_sending=data["stuck_sending"],
        worker=data["worker"],
    )


@router.post(
    "/admin/ops/notifications/{outbox_id}/retry",
    response_model=OutboxActionOut,
    summary="Retry a failed/skipped/cancelled job (admin, audited)",
)
def retry_notification(
    outbox_id: str,
    payload: OutboxRetryRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> OutboxActionOut:
    """A retry never edits the old record: it creates a NEW outbox row with
    the same snapshot (the immutable attempt history stays intact)."""
    from app.models import DeliveryStatus

    old = _get_outbox_row(db, outbox_id)
    if old.status not in (
        DeliveryStatus.FAILED,
        DeliveryStatus.SKIPPED,
        DeliveryStatus.CANCELLED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Повторить можно только неудачное, пропущенное или отменённое задание.",
        )
    new_row = NotificationOutbox(
        recipient_user_id=old.recipient_user_id,
        external_recipient=old.external_recipient,
        channel=old.channel,
        notification_type=old.notification_type,
        source=old.source,
        title=old.title,
        body=old.body,
        template=old.template,
        template_version=old.template_version,
        initiator_user_id=admin.id,
        object_type=old.object_type,
        object_id=old.object_id,
        scheduled_at=utc_now(),
        queued_at=utc_now(),
        status=DeliveryStatus.QUEUED,
        idempotency_key=f"{old.idempotency_key}:retry:{uuid.uuid4().hex[:12]}",
        consent_snapshot=old.consent_snapshot,
        quiet_hours_bypassed=payload.bypass_quiet_hours,
    )
    db.add(new_row)
    record_event(
        db,
        AuditAction.NOTIFICATION_RETRY,
        actor=admin,
        details=f"outbox={old.id} bypass_quiet_hours={payload.bypass_quiet_hours}",
        commit=False,
    )
    if payload.bypass_quiet_hours:
        record_event(
            db,
            AuditAction.NOTIFICATION_URGENT_OVERRIDE,
            actor=admin,
            details=f"outbox={old.id} manual send confirmed outside quiet hours",
            commit=False,
        )
    db.commit()
    db.refresh(new_row)
    return OutboxActionOut(id=new_row.id, status=new_row.status.value)


@router.post(
    "/admin/ops/notifications/{outbox_id}/cancel",
    response_model=OutboxActionOut,
    summary="Cancel a queued job (admin, audited)",
)
def cancel_notification(
    outbox_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(_admin_only),
) -> OutboxActionOut:
    """Best-effort cancel of a queued/sending job. Delivered jobs cannot be
    un-delivered — 409, no history is edited."""
    from app.models import DeliveryStatus

    row = _get_outbox_row(db, outbox_id)
    if row.status in (DeliveryStatus.DELIVERED, DeliveryStatus.ACCEPTED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Доставленное задание отменить нельзя (история неизменяема).",
        )
    if row.status in (DeliveryStatus.FAILED, DeliveryStatus.SKIPPED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Задание уже завершено; для повторной отправки используйте retry.",
        )
    row.status = DeliveryStatus.CANCELLED
    row.cancelled_at = utc_now()
    row.next_attempt_at = None
    row.lease_expires_at = None
    record_event(
        db,
        AuditAction.NOTIFICATION_CANCEL,
        actor=admin,
        details=f"outbox={row.id}",
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return OutboxActionOut(id=row.id, status=row.status.value)
