"""API tests for candidate communications (phase 10): RBAC, CSRF, flows.

SQLite-backed TestClient. Providers are treated as configured via
``SMTP_ENABLED``/``TELEGRAM_ENABLED`` settings — the suite only queues rows
and never performs network I/O (delivery is exercised in the worker tests).
"""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateContactChannel,
    CandidateMessage,
    CandidateMessageSource,
    CandidateMessageType,
    EventType,
    UserRole,
)
from app.telegram import TelegramStartUpdate, TelegramUpdatesResult
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "test",
        "SECRET_KEY": "phase10-api-test-secret",
        "DATABASE_URL": "sqlite+pysqlite://",
        "SMTP_ENABLED": "true",
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": "1025",
        "SMTP_FROM_ADDRESS": "hr@example.test",
        "TELEGRAM_ENABLED": "true",
        "TELEGRAM_BOT_TOKEN": "0000000000:test",
        "TELEGRAM_BOT_USERNAME": "hr_manager_test_bot",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _enable_channels(client: TestClient, **overrides: object) -> None:
    _configure_app(client, **overrides)


def _configure_app(client: TestClient, **overrides: object) -> None:
    app = cast(FastAPI, client.app)
    app.state.settings = _settings(**overrides)


def _bind_email(
    db: Session, candidate: Candidate, *, address: str = "candidate@example.test"
) -> None:
    db.add(
        CandidateContactChannel(
            candidate_id=candidate.id,
            channel="email",
            email_address=address,
            consent_granted=True,
            consent_at=datetime(2026, 9, 1, tzinfo=UTC),
            consent_source="candidate_email_link",
            consent_policy_version="phase10-v1",
        )
    )
    db.commit()


def _bind_telegram(db: Session, candidate: Candidate, *, chat_id: int = 123456789) -> None:
    db.add(
        CandidateContactChannel(
            candidate_id=candidate.id,
            channel="telegram",
            chat_id=chat_id,
            consent_granted=True,
            consent_at=datetime(2026, 9, 1, tzinfo=UTC),
            consent_source="telegram_start",
            consent_policy_version="phase10-v1",
        )
    )
    db.commit()


def _single_message(db: Session) -> CandidateMessage:
    return db.execute(select(CandidateMessage)).scalars().one()


# --- RBAC ---------------------------------------------------------------------


def test_hr_cannot_see_foreign_candidate(client: TestClient, db_session: Session) -> None:
    hr1 = make_user(db_session, username="hr-own", role=UserRole.HR)
    hr2 = make_user(db_session, username="hr-foreign", role=UserRole.HR)
    mine = make_candidate(db_session, owner=hr1, email="mine@example.test")
    foreign = make_candidate(db_session, owner=hr2, email="foreign@example.test")

    _login(client, "hr-own")
    assert client.get(f"/candidates/{mine.id}/communications/channels").status_code == 200
    assert client.get(f"/candidates/{foreign.id}/communications/channels").status_code == 404
    assert client.get(f"/candidates/{foreign.id}/communications/history").status_code == 404


def test_manager_and_admin_can_see_candidate_channels(
    client: TestClient, db_session: Session
) -> None:
    hr = make_user(db_session, username="owned-by", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="c@example.test")
    make_user(db_session, username="manager-sees", role=UserRole.MANAGER)
    make_user(db_session, username="admin-sees", role=UserRole.ADMIN)

    _login(client, "manager-sees")
    assert client.get(f"/candidates/{candidate.id}/communications/channels").status_code == 200
    _login(client, "admin-sees")
    assert client.get(f"/candidates/{candidate.id}/communications/channels").status_code == 200


# --- CSRF ---------------------------------------------------------------------


def test_mutating_endpoints_require_csrf(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="csrf-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="c@example.test")
    _login(client, "csrf-hr")

    response = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={"message_type": "documents_request", "channel": "email", "documents": ["Паспорт"]},
    )
    assert response.status_code == 403
    assert (
        client.post(f"/candidates/{candidate.id}/communications/email/consent-request").status_code
        == 403
    )


# --- Manual send / preview / history / cancel ---------------------------------


def test_manual_send_preview_history_and_cancel(client: TestClient, db_session: Session) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="send-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    _bind_email(db_session, candidate)
    csrf = _login(client, "send-hr")

    preview = client.post(
        f"/candidates/{candidate.id}/communications/preview",
        json={
            "message_type": "documents_request",
            "channel": "email",
            "documents": ["Паспорт", "Диплом"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200
    assert preview.json()["title"] == "Запрос документов"
    assert "Паспорт" in preview.json()["body"]

    key = f"idem-{uuid4()}"
    sent = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={
            "message_type": "documents_request",
            "channel": "email",
            "documents": ["Паспорт", "Диплом"],
            "idempotency_key": key,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert sent.status_code == 201, sent.text
    message = sent.json()["message"]
    assert message["status"] == "queued"
    assert message["channel"] == "email"
    assert message["source"] == "manual"
    assert message["title"] == "Запрос документов"
    assert message["recipient_masked"] == "c***@example.test"

    # An HTTP retry with the same idempotency key never creates a duplicate.
    duplicate = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={
            "message_type": "documents_request",
            "channel": "email",
            "documents": ["Паспорт", "Диплом"],
            "idempotency_key": key,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["message"]["id"] == message["id"]
    assert len(db_session.execute(select(CandidateMessage)).scalars().all()) == 1

    history = client.get(f"/candidates/{candidate.id}/communications/history").json()
    assert history["total"] == 1
    assert history["items"][0]["body"].startswith("Здравствуйте")
    detail = client.get(f"/candidates/{candidate.id}/communications/history/{message['id']}")
    assert detail.status_code == 200

    # Cancel a queued row; a second cancel is a no-op.
    cancelled = client.post(
        f"/candidates/{candidate.id}/communications/{message['id']}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    again = client.post(
        f"/candidates/{candidate.id}/communications/{message['id']}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert again.json()["cancelled"] is False


def test_send_on_channel_without_consent_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="no-consent-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    csrf = _login(client, "no-consent-hr")
    response = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={"message_type": "documents_request", "channel": "email", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "согласие" in response.json()["detail"].lower()


def test_telegram_manual_send_uses_bound_chat(client: TestClient, db_session: Session) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="tg-send-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    _bind_telegram(db_session, candidate)
    csrf = _login(client, "tg-send-hr")
    sent = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={
            "message_type": "documents_request",
            "channel": "telegram",
            "documents": ["Паспорт"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert sent.status_code == 201, sent.text
    message = sent.json()["message"]
    assert message["channel"] == "telegram"
    row = db_session.execute(
        select(CandidateMessage).where(CandidateMessage.id == UUID(message["id"]))
    ).scalar_one()
    assert row.recipient_chat_id == 123456789
    assert row.recipient_email is None
    assert message["recipient_masked"] is not None
    assert "123456789" not in message["recipient_masked"]


# --- Interview-linked messages ------------------------------------------------


def test_interview_messages_require_owned_live_interview(
    client: TestClient, db_session: Session
) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="event-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    _bind_email(db_session, candidate)
    csrf = _login(client, "event-hr")
    future = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    event = make_event(
        db_session,
        candidate=candidate,
        author=hr,
        assignee=hr,
        type_=EventType.INTERVIEW,
        starts_at=future,
        title="Собеседование",
        note="Собеседование в офисе",
    )
    event.location = "Офис, каб. 12"
    db_session.commit()

    # Documents kind must not carry an event id.
    bad = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={
            "message_type": "documents_request",
            "channel": "email",
            "event_id": str(event.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 422

    # Interview kind without an event id is rejected.
    missing = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={"message_type": "interview_scheduled", "channel": "email"},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing.status_code == 422

    # A foreign event (another candidate) is invisible.
    other_hr = make_user(db_session, username="other-hr", role=UserRole.HR)
    other = make_candidate(db_session, owner=other_hr, email="other@example.test")
    foreign_event = make_event(
        db_session,
        candidate=other,
        author=other_hr,
        assignee=other_hr,
        type_=EventType.INTERVIEW,
        starts_at=future,
        title="Собеседование",
    )
    foreign = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={
            "message_type": "interview_scheduled",
            "channel": "email",
            "event_id": str(foreign_event.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert foreign.status_code == 404

    # A live interview schedules the letter with venue + event snapshot.
    scheduled = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={
            "message_type": "interview_scheduled",
            "channel": "email",
            "event_id": str(event.id),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert scheduled.status_code == 201, scheduled.text
    row = _single_message(db_session)
    assert row.event_id == event.id
    assert row.event_version == event.version
    assert "каб. 12" in row.body
    assert row.recipient_email == "candidate@example.test"


# --- Email double opt-in + public consent/revoke ------------------------------


def test_consent_request_confirm_and_revoke_flow(client: TestClient, db_session: Session) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="consent-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    csrf = _login(client, "consent-hr")

    requested = client.post(
        f"/candidates/{candidate.id}/communications/email/consent-request",
        headers={"X-CSRF-Token": csrf},
    )
    assert requested.status_code == 202, requested.text
    invite = db_session.execute(
        select(CandidateMessage).where(
            CandidateMessage.message_type == CandidateMessageType.CONSENT_INVITE
        )
    ).scalar_one()
    assert invite.source == CandidateMessageSource.SYSTEM

    channels = client.get(f"/candidates/{candidate.id}/communications/channels").json()
    email_state = next(c for c in channels["channels"] if c["channel"] == "email")
    assert email_state["state"] == "pending_confirmation"

    # The candidate clicks the one-shot link from the letter body.
    raw = invite.body.rsplit("consent/", 1)[1].split("\n", 1)[0].split()[0].strip()
    clicked = client.get(f"/public/candidates/consent/{raw}")
    assert clicked.status_code == 200
    assert "Согласие подтверждено" in clicked.text

    channels = client.get(f"/candidates/{candidate.id}/communications/channels").json()
    email_state = next(c for c in channels["channels"] if c["channel"] == "email")
    assert email_state["state"] == "allowed"
    assert email_state["recipient_masked"] == "c***@example.test"

    # The link is single-use.
    again = client.get(f"/public/candidates/consent/{raw}")
    assert again.status_code == 200
    assert ("использована" in again.text) or ("уже было подтверждено" in again.text)

    # Unsubscribe link with a forged signature is rejected.
    forged = client.get(f"/public/candidates/revoke/{candidate.id}/email?sig=deadbeef")
    assert forged.status_code == 404

    # A queued letter is cancelled by the opt-out.
    queued = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={"message_type": "documents_request", "channel": "email", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 201
    row = db_session.execute(
        select(CandidateMessage).where(
            CandidateMessage.message_type == CandidateMessageType.DOCUMENTS_REQUEST
        )
    ).scalar_one()
    assert row.status == "queued"

    signature = hmac.new(
        b"phase10-api-test-secret", f"{candidate.id}:email".encode(), hashlib.sha256
    ).hexdigest()
    revoked = client.get(f"/public/candidates/revoke/{candidate.id}/email?sig={signature}")
    assert revoked.status_code == 200
    assert "Отписка выполнена" in revoked.text
    db_session.refresh(row)
    assert row.status == "cancelled"

    channels = client.get(f"/candidates/{candidate.id}/communications/channels").json()
    email_state = next(c for c in channels["channels"] if c["channel"] == "email")
    assert email_state["state"] == "denied"


def test_consent_request_requires_smtp_configured(client: TestClient, db_session: Session) -> None:
    _configure_app(client, SMTP_ENABLED="false")
    hr = make_user(db_session, username="smtp-off-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    csrf = _login(client, "smtp-off-hr")
    response = client.post(
        f"/candidates/{candidate.id}/communications/email/consent-request",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 503


def test_consent_link_invalid_token_is_safe(client: TestClient, db_session: Session) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="safe-link-hr", role=UserRole.HR)
    make_candidate(db_session, owner=hr, email="candidate@example.test")
    response = client.get("/public/candidates/consent/not-a-real-token")
    assert response.status_code == 200
    assert "недействительна" in response.text


# --- Telegram linking ----------------------------------------------------------


def test_telegram_link_confirm_and_chat_conflict(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="tg-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    csrf = _login(client, "tg-hr")

    linked = client.post(
        f"/candidates/{candidate.id}/communications/telegram/link",
        headers={"X-CSRF-Token": csrf},
    )
    assert linked.status_code == 201, linked.text
    raw = linked.json()["deep_link"].rsplit("start=", 1)[1]

    from app.routers import candidate_communications as cc_router

    def fake_get_start_updates(
        config: object, *, offset: int | None = None
    ) -> TelegramUpdatesResult:
        return TelegramUpdatesResult(
            ok=True,
            updates=(TelegramStartUpdate(update_id=5, chat_id=987654321, token=raw),),
            max_update_id=5,
        )

    monkeypatch.setattr(cc_router, "get_start_updates", fake_get_start_updates)

    confirmed = client.post(
        f"/candidates/{candidate.id}/communications/telegram/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "allowed"
    assert (
        (client.get(f"/candidates/{candidate.id}/communications/channels").json()["channels"])[1][
            "state"
        ]
        == "allowed"
    )

    # A second candidate must never take over an already-bound chat.
    other_hr = make_user(db_session, username="tg-hr2", role=UserRole.HR)
    other = make_candidate(db_session, owner=other_hr, email="second@example.test")
    csrf2 = _login(client, "tg-hr2")
    linked2 = client.post(
        f"/candidates/{other.id}/communications/telegram/link",
        headers={"X-CSRF-Token": csrf2},
    )
    assert linked2.status_code == 201
    raw2 = linked2.json()["deep_link"].rsplit("start=", 1)[1]

    def conflicting_updates(config: object, *, offset: int | None = None) -> TelegramUpdatesResult:
        return TelegramUpdatesResult(
            ok=True,
            updates=(TelegramStartUpdate(update_id=9, chat_id=987654321, token=raw2),),
            max_update_id=9,
        )

    monkeypatch.setattr(cc_router, "get_start_updates", conflicting_updates)
    conflict = client.post(
        f"/candidates/{other.id}/communications/telegram/confirm",
        headers={"X-CSRF-Token": csrf2},
    )
    assert conflict.status_code == 409
    # Original binding is intact (checked in the DB; the HR owner of the
    # first candidate is no longer the active session).
    bound = db_session.execute(
        select(CandidateContactChannel).where(
            CandidateContactChannel.candidate_id == candidate.id,
            CandidateContactChannel.channel == "telegram",
        )
    ).scalar_one()
    assert bound.chat_id == 987654321
    assert bound.consent_granted is True
    # The conflicting candidate stays pending (its code was not consumed).
    other_state = client.get(f"/candidates/{other.id}/communications/channels").json()
    tg_state = next(c for c in other_state["channels"] if c["channel"] == "telegram")
    assert tg_state["state"] == "pending_confirmation"


# --- Quiet hours gating ---------------------------------------------------------


def test_manual_send_blocked_in_quiet_hours_unless_confirmed(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = datetime(2026, 9, 8, 22, 30, tzinfo=UTC)  # inside 21:00-08:00 UTC
    from app.routers import candidate_communications as cc_router

    monkeypatch.setattr(cc_router, "utc_now", lambda: frozen)
    _enable_channels(client)
    hr = make_user(db_session, username="quiet-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    _bind_email(db_session, candidate)
    csrf = _login(client, "quiet-hr")

    blocked = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={"message_type": "documents_request", "channel": "email", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 409
    assert "тихие часы" in blocked.json()["detail"]

    confirmed = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={
            "message_type": "documents_request",
            "channel": "email",
            "documents": ["Паспорт"],
            "confirm_quiet_hours": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 201, confirmed.text
    row = _single_message(db_session)
    assert row.quiet_hours_bypassed is True


# --- Audit (no message text / PII in details) -----------------------------------


def test_manual_send_is_audited_without_content_or_recipient(
    client: TestClient, db_session: Session
) -> None:
    _enable_channels(client)
    hr = make_user(db_session, username="audit-hr", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=hr, email="candidate@example.test")
    _bind_email(db_session, candidate)
    csrf = _login(client, "audit-hr")

    sent = client.post(
        f"/candidates/{candidate.id}/communications/send",
        json={"message_type": "documents_request", "channel": "email", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert sent.status_code == 201
    row = _single_message(db_session)

    audits = (
        db_session.execute(select(AuditEvent).where(AuditEvent.candidate_id == candidate.id))
        .scalars()
        .all()
    )
    sent_audits = [a for a in audits if a.action == AuditAction.CANDIDATE_MESSAGE_SENT]
    assert len(sent_audits) == 1
    details = sent_audits[0].details or ""
    assert row.body not in details
    assert "candidate@example.test" not in details
    assert row.id is not None
