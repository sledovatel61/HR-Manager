"""Integration tests for Telegram and SMTP integrations on PostgreSQL (Phase 9)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateSource,
    CandidateStage,
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationPreference,
    NotificationType,
    TelegramLinkToken,
    UserRole,
    WorkerHeartbeat,
)
from app.smtp_adapter import SmtpSendResult
from app.telegram_adapter import TelegramSendResult
from app.utils import utc_now
from app.worker import claim_batch, process_row
from tests.conftest import FIXTURE_PASSWORD, make_user

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)


def _clean_db(db: Session) -> None:
    db.execute(delete(NotificationDeliveryAttempt))
    db.execute(delete(NotificationOutbox))
    db.execute(delete(Notification))
    db.execute(delete(TelegramLinkToken))
    db.execute(delete(AuditEvent))
    db.execute(delete(WorkerHeartbeat))
    db.commit()


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_pg_telegram_linking_flow_e2e(pg_client: TestClient, pg_db: Session) -> None:
    _clean_db(pg_db)
    hr = make_user(pg_db, username="hr-tg-pg-e2e", role=UserRole.HR)
    csrf = _login(pg_client, "hr-tg-pg-e2e")

    pg_client.app.state.settings.telegram_bot_token = "123456:FAKE_TOKEN"
    pg_client.app.state.settings.telegram_bot_username = "HrIntegrationBot"

    # 1. Initiate linking
    with patch("app.telegram_adapter.get_me", return_value={"username": "HrIntegrationBot"}):
        initiate_res = pg_client.post(
            "/integrations/telegram/link/initiate",
            headers={"X-CSRF-Token": csrf},
        )
    assert initiate_res.status_code == 200, initiate_res.text
    init_data = initiate_res.json()
    token = init_data["token"]
    assert init_data["bot_username"] == "HrIntegrationBot"
    assert f"t.me/HrIntegrationBot?start={token}" in init_data["deep_link"]

    # Verify token row in PostgreSQL
    token_rows = (
        pg_db.execute(select(TelegramLinkToken).where(TelegramLinkToken.user_id == hr.id))
        .scalars()
        .all()
    )
    assert len(token_rows) == 1
    assert token_rows[0].used_at is None

    # 2. Confirm linking
    with patch("app.routers.integrations.telegram_send_message"):
        confirm_res = pg_client.post(
            "/integrations/telegram/link/confirm",
            json={"token": token, "chat_id": 987654321, "username": "tg_pg_user"},
            headers={"X-CSRF-Token": csrf},
        )
    assert confirm_res.status_code == 200, confirm_res.text
    c_data = confirm_res.json()
    assert c_data["linked"] is True
    assert c_data["details"]["username"] == "tg_pg_user"

    # Check preferences in PostgreSQL
    pref = pg_db.get(NotificationPreference, hr.id)
    assert pref is not None
    assert pref.telegram_chat_id == 987654321
    assert pref.telegram_username == "tg_pg_user"
    assert pref.telegram_opt_in is True
    assert pref.telegram_consent_at is not None
    assert "telegram" in pref.enabled_channels

    # Check audit log in PostgreSQL
    audit_events = (
        pg_db.execute(select(AuditEvent).where(AuditEvent.actor_user_id == hr.id)).scalars().all()
    )
    actions = {event.action for event in audit_events}
    assert AuditAction.TELEGRAM_LINK_INITIATED in actions
    assert AuditAction.TELEGRAM_LINK_CONFIRMED in actions

    # Check status endpoint
    status_res = pg_client.get("/integrations/status")
    assert status_res.status_code == 200
    st = status_res.json()
    assert st["telegram"]["linked"] is True
    assert st["telegram"]["details"]["username"] == "tg_pg_user"

    # 3. Unlink
    unlink_res = pg_client.post(
        "/integrations/telegram/unlink",
        headers={"X-CSRF-Token": csrf},
    )
    assert unlink_res.status_code == 200

    pg_db.refresh(pref)
    assert pref.telegram_chat_id is None
    assert pref.telegram_opt_in is False
    assert "telegram" not in pref.enabled_channels

    # Check audit log contains unlinked
    audit_events_2 = (
        pg_db.execute(select(AuditEvent).where(AuditEvent.actor_user_id == hr.id)).scalars().all()
    )
    actions_2 = {event.action for event in audit_events_2}
    assert AuditAction.TELEGRAM_UNLINKED in actions_2


def test_pg_multi_channel_outbox_routing_and_worker(pg_client: TestClient, pg_db: Session) -> None:
    _clean_db(pg_db)
    owner = make_user(pg_db, username="hr-multi-owner", role=UserRole.HR)
    assignee = make_user(pg_db, username="hr-multi-assignee", role=UserRole.HR)
    make_user(pg_db, username="mgr-multi", role=UserRole.MANAGER)

    # Configure assignee preferences for in_app, telegram, and email
    pref = NotificationPreference(
        user_id=assignee.id,
        timezone="Europe/Moscow",
        quiet_hours_start="23:00",
        quiet_hours_end="07:00",
        workdays=[1, 2, 3, 4, 5, 6, 7],
        enabled_types=[member.value for member in NotificationType],
        enabled_channels=["in_app", "telegram", "email"],
        telegram_chat_id=555666777,
        telegram_opt_in=True,
        telegram_consent_at=utc_now(),
        email_address="assignee@company.test",
        email_opt_in=True,
        email_consent_at=utc_now(),
    )
    pg_db.add(pref)

    candidate = Candidate(
        full_name="Кандидат Мульти",
        full_name_normalized="кандидат мульти",
        source=CandidateSource.REFERRAL,
        position="Backend Developer",
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    pg_db.add(candidate)
    pg_db.commit()

    csrf = _login(pg_client, "mgr-multi")
    future = utc_now() + timedelta(hours=3)
    created = pg_client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "assignee_user_id": str(assignee.id),
            "type": "interview",
            "title": "Техническое интервью",
            "starts_at": future.isoformat(),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text

    # Verify outbox rows created for in_app, telegram, and email
    outbox_rows = (
        pg_db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_user_id == assignee.id,
                NotificationOutbox.notification_type == NotificationType.EVENT_ASSIGNED,
            )
        )
        .scalars()
        .all()
    )

    channels = {row.channel for row in outbox_rows}
    assert DeliveryChannel.IN_APP in channels
    assert DeliveryChannel.TELEGRAM in channels
    assert DeliveryChannel.EMAIL in channels

    # Run worker processing on Postgres
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "x",
            "DATABASE_URL": "postgresql+psycopg://postgres@127.0.0.1:5432/hr_manager_test",
            "TELEGRAM_BOT_TOKEN": "123456:FAKE_TOKEN",
            "SMTP_HOST": "smtp.company.test",
            "WORKER_MAX_ATTEMPTS": "3",
            "WORKER_LEASE_SECONDS": "120",
        }
    )

    claimed = claim_batch(pg_db, now=utc_now(), batch_size=20, lease_seconds=120)
    assert len(claimed) >= 3

    with (
        patch(
            "app.worker.telegram_send_message",
            return_value=TelegramSendResult(accepted=True, provider_message_id="tg-msg-e2e-1"),
        ),
        patch(
            "app.worker.smtp_send_email",
            return_value=SmtpSendResult(
                accepted=True, provider_message_id="<email-msg-e2e-1@test>"
            ),
        ),
    ):
        for row in claimed:
            process_row(pg_db, row, settings=settings, now=utc_now())

    # Check final statuses in Postgres
    db_rows = (
        pg_db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_user_id == assignee.id,
                NotificationOutbox.notification_type == NotificationType.EVENT_ASSIGNED,
            )
        )
        .scalars()
        .all()
    )

    for r in db_rows:
        if r.channel == DeliveryChannel.IN_APP:
            assert r.status == DeliveryStatus.DELIVERED
        elif r.channel == DeliveryChannel.TELEGRAM:
            assert r.status == DeliveryStatus.ACCEPTED
            assert r.provider_message_id == "tg-msg-e2e-1"
        elif r.channel == DeliveryChannel.EMAIL:
            assert r.status == DeliveryStatus.ACCEPTED
            assert r.provider_message_id == "<email-msg-e2e-1@test>"

    # Check delivery attempts in Postgres
    attempts = pg_db.execute(select(NotificationDeliveryAttempt)).scalars().all()
    assert len(attempts) >= 3
    outcomes = {attempt.outcome for attempt in attempts}
    assert "delivered" in outcomes
    assert "accepted" in outcomes


def test_pg_admin_connection_and_test_send(pg_client: TestClient, pg_db: Session) -> None:
    _clean_db(pg_db)
    admin = make_user(pg_db, username="admin-pg-test", role=UserRole.ADMIN)
    csrf = _login(pg_client, "admin-pg-test")

    pg_client.app.state.settings.telegram_bot_token = "123456:BOT_TOKEN"
    pg_client.app.state.settings.smtp_host = "smtp.pg-test.com"

    # 1. Telegram test connection
    with patch(
        "app.routers.integrations.telegram_get_me",
        return_value={"id": 112233, "username": "AdminProbeBot", "first_name": "Admin Probe"},
    ):
        tg_conn_res = pg_client.post(
            "/admin/integrations/telegram/test-connection",
            headers={"X-CSRF-Token": csrf},
        )
        assert tg_conn_res.status_code == 200
        assert tg_conn_res.json()["ok"] is True
        assert tg_conn_res.json()["bot_username"] == "AdminProbeBot"

    # 2. SMTP test connection
    with patch(
        "app.routers.integrations.smtp_verify_connection",
        return_value={
            "ok": True,
            "host": "smtp.pg-test.com",
            "port": 587,
            "use_tls": False,
            "use_starttls": True,
            "authenticated": True,
        },
    ):
        smtp_conn_res = pg_client.post(
            "/admin/integrations/smtp/test-connection",
            headers={"X-CSRF-Token": csrf},
        )
        assert smtp_conn_res.status_code == 200
        assert smtp_conn_res.json()["ok"] is True
        assert smtp_conn_res.json()["host"] == "smtp.pg-test.com"

    # 3. Test send Telegram
    with patch(
        "app.routers.integrations.telegram_send_message",
        return_value=TelegramSendResult(accepted=True, provider_message_id="msg-pg-tg-1"),
    ):
        tg_send_res = pg_client.post(
            "/admin/integrations/test-send",
            json={"channel": "telegram", "recipient": "987654"},
            headers={"X-CSRF-Token": csrf},
        )
        assert tg_send_res.status_code == 200
        assert tg_send_res.json()["ok"] is True
        assert tg_send_res.json()["provider_message_id"] == "msg-pg-tg-1"

    # 4. Test send Email
    with patch(
        "app.routers.integrations.smtp_send_email",
        return_value=SmtpSendResult(accepted=True, provider_message_id="<msg-pg-em-1@test>"),
    ):
        em_send_res = pg_client.post(
            "/admin/integrations/test-send",
            json={"channel": "email", "recipient": "pg_admin@company.test"},
            headers={"X-CSRF-Token": csrf},
        )
        assert em_send_res.status_code == 200
        assert em_send_res.json()["ok"] is True
        assert em_send_res.json()["provider_message_id"] == "<msg-pg-em-1@test>"

    # 5. Verify audit events recorded in PostgreSQL
    audit_events = (
        pg_db.execute(select(AuditEvent).where(AuditEvent.actor_user_id == admin.id))
        .scalars()
        .all()
    )
    actions = [event.action for event in audit_events]
    assert actions.count(AuditAction.INTEGRATION_CONNECTION_TESTED) >= 2
    assert actions.count(AuditAction.INTEGRATION_TEST_SENT) >= 2
