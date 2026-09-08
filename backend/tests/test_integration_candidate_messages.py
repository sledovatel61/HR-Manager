"""Integration tests for candidate communications (real PostgreSQL).

End-to-end coverage of phase 10 against a real database and real stub
servers (same wire format as the phase-9 suite: an HTTP Telegram stub and
a plaintext TCP SMTP stub). No real chats, credentials or personal data.

* the full manual flow: consent -> preview -> send -> worker delivery via
  SMTP and Telegram (accepted, provider id, exact text preserved);
* the event-driven flow: creating/rescheduling/cancelling an interview
  plans/cancels candidate rows in the same transaction;
* concurrency: two parallel workers deliver one row exactly once
  (advisory single-flight); a consent revoke racing the worker stops the
  send;
* honest statuses: accepted is never delivered; skipped rows carry the
  fail-closed reason.
"""

import hashlib
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import (
    Candidate,
    CandidateChannelConsent,
    CandidateTelegramLink,
    DeliveryStatus,
    NotificationOutbox,
    NotificationType,
    User,
    UserRole,
)
from app.utils import utc_now
from app.worker import claim_batch, process_external_row
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user
from tests.test_integration_external import SmtpStub, TelegramStub

RUN_INTEGRATION = os.environ.get("TEST_DATABASE_URL") is not None
NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.integration


@pytest.fixture()
def telegram_stub() -> Iterator[TelegramStub]:
    stub = TelegramStub()
    yield stub
    stub.close()


@pytest.fixture()
def smtp_stub() -> Iterator[SmtpStub]:
    stub = SmtpStub()
    yield stub
    stub.close()


@pytest.fixture()
def settings(integration_url: str, telegram_stub: TelegramStub, smtp_stub: SmtpStub) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "integration-test-secret-key",
            "DATABASE_URL": integration_url,
            "WORKER_MAX_ATTEMPTS": "3",
            "WORKER_BACKOFF_BASE_S": "60",
            "WORKER_BACKOFF_CAP_S": "3600",
            "WORKER_LEASE_SECONDS": "120",
            "TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "stub-token",
            "TELEGRAM_BOT_USERNAME": "hr_test_bot",
            "TELEGRAM_API_BASE_URL": telegram_stub.base_url,
            "SMTP_ENABLED": "true",
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": str(smtp_stub.port),
            "SMTP_ENCRYPTION": "none",
            "SMTP_FROM_ADDRESS": "noreply@example.com",
            "SMTP_FROM_NAME": "HR Manager",
            "CANDIDATE_MESSAGE_RATE_LIMIT": "1000",
            "CANDIDATE_EMAIL_CONFIRM_BASE_URL": "https://hr.example.test",
            "PUBLIC_CONFIRM_RATE_LIMIT": "1000",
        }
    )


@pytest.fixture()
def client(pg_engine: Engine, settings: Settings) -> Iterator[TestClient]:
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE audit_log, event_history, events, candidate_transfers, "
                "candidate_interactions, candidates, user_sessions, users, "
                "candidate_telegram_links, candidate_telegram_link_tokens, "
                "candidate_channel_consents, candidate_email_confirm_tokens, "
                "candidate_message_requests, telegram_start_events, telegram_poll_state, "
                "telegram_link_tokens, telegram_links, user_emails, "
                "notification_delivery_attempts, notification_outbox, notifications, "
                "notification_preferences, reminders, access_grants RESTART IDENTITY CASCADE"
            )
        )
    app = create_app(settings, engine=pg_engine)
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _seed_candidate_channels(
    db: Session, hr: User, *, email: str = "cand@example.com", chat_id: int = 88001122
) -> Candidate:
    from app.utils import normalize_email

    candidate = make_candidate(db, owner=hr, email=email, full_name="Интеграция Тестова")
    db.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="email",
            granted=True,
            granted_at=NOW,
            source="email_confirm",
            policy_version="phase10-v1",
            email_normalized=normalize_email(email),
        )
    )
    db.add(CandidateTelegramLink(candidate_id=candidate.id, chat_id=chat_id, linked_at=NOW))
    db.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="telegram",
            granted=True,
            granted_at=NOW,
            source="telegram_start",
            policy_version="phase10-v1",
        )
    )
    db.commit()
    return candidate


def _candidate_rows(db: Session, candidate_id: UUID) -> list[NotificationOutbox]:
    return list(
        db.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.recipient_candidate_id == candidate_id)
            .order_by(NotificationOutbox.queued_at, NotificationOutbox.id)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )


def test_manual_send_end_to_end_both_channels(
    client: TestClient,
    pg_db: Session,
    smtp_stub: SmtpStub,
    telegram_stub: TelegramStub,
    settings: Settings,
) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = _seed_candidate_channels(pg_db, hr)
    csrf = _login(client, "hr1")

    # Channel states are honest.
    body = client.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == "allowed"
    assert body["telegram"]["state"] == "allowed"
    assert set(body["allowed_channels"]) == {"email", "telegram"}

    # Preview shows the exact text without queueing.
    preview = client.post(
        f"/candidates/{candidate.id}/messages/preview",
        json={"message_type": "document_request", "documents": ["Паспорт РФ", "СНИЛС"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert preview.status_code == 200
    assert "— Паспорт РФ" in preview.json()["body"]
    assert _candidate_rows(pg_db, candidate.id) == []

    # Send: one row per allowed channel.
    send = client.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["Паспорт РФ", "СНИЛС"],
            "idempotency_key": "integ-manual-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201, send.text
    assert sorted(send.json()["channels"]) == ["email", "telegram"]
    rows = _candidate_rows(pg_db, candidate.id)
    assert len(rows) == 2
    # The stub Bot API returns a message id for accepted sends.
    telegram_stub.script("sendMessage", [(200, {"ok": True, "result": {"message_id": 555777}})])

    # The worker delivers both rows through the real stub servers.
    claimed = claim_batch(pg_db, now=utc_now(), batch_size=10, lease_seconds=120)
    assert len(claimed) == 2
    for row in rows:
        status = process_external_row(pg_db, row.id, settings=settings, now=utc_now())
        assert status == "accepted", row.id
        pg_db.refresh(row)
        assert row.status == DeliveryStatus.ACCEPTED
        # The provider id is stored only when the provider returned one:
        # the Telegram stub does, plain SMTP honestly does not.
        if row.channel.value == "telegram":
            assert row.provider_message_id is not None
        else:
            assert row.provider_message_id is None
        assert row.delivered_at is None  # accepted is never delivered/read
        assert row.error_class is None

    # The exact text reached the stubs (the Russian subject travels
    # RFC 2047-encoded; the body is MIME — parse like any mail client).
    import email
    import email.policy

    assert len(smtp_stub.data_blocks) == 1
    parsed = email.message_from_bytes(smtp_stub.data_blocks[0], policy=email.policy.default)
    assert "Запрос документов" in str(parsed["Subject"])
    assert parsed["To"] == "cand@example.com"
    part = parsed.get_body(preferencelist=("plain",))
    assert part is not None
    mail_body = part.get_content()
    assert "Паспорт РФ" in mail_body
    assert "Интеграция Тестова" in mail_body
    assert "СНИЛС" in mail_body
    assert any(m == "sendMessage" for m, _ in telegram_stub.requests)
    tg_payload = next(p for m, p in telegram_stub.requests if m == "sendMessage")
    assert "СНИЛС" in str(tg_payload)

    # History shows the immutable text and the provider verdicts.
    history = client.get(f"/candidates/{candidate.id}/messages").json()
    assert history["total"] == 2
    assert all(item["status"] == "accepted" for item in history["items"])
    by_channel = {item["channel"]: item for item in history["items"]}
    assert by_channel["telegram"]["provider_message_id"]
    assert by_channel["email"]["provider_message_id"] is None


def test_interview_lifecycle_plans_cancels_and_delivers(
    client: TestClient,
    pg_db: Session,
    smtp_stub: SmtpStub,
    settings: Settings,
) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = _seed_candidate_channels(pg_db, hr, email="iv@example.com")
    csrf = _login(client, "hr1")
    starts_at = datetime.now(UTC) + timedelta(days=5)

    created = client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "type": "interview",
            "title": "Собеседование",
            "starts_at": starts_at.isoformat(),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    rows = _candidate_rows(pg_db, candidate.id)
    kinds = {(r.channel.value, r.notification_type.value) for r in rows}
    assert ("email", "candidate_interview_scheduled") in kinds
    assert ("telegram", "candidate_interview_scheduled") in kinds
    assert ("email", "candidate_interview_reminder") in kinds

    # Reschedule: stale plan cancelled, «перенесено» + fresh reminder queued.
    new_start = starts_at + timedelta(days=2)
    patched = client.patch(
        f"/events/{created.json()['id']}",
        json={"expected_version": 1, "starts_at": new_start.isoformat()},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched.status_code == 200, patched.text
    rows = _candidate_rows(pg_db, candidate.id)
    by = {(r.notification_type.value, r.status.value) for r in rows}
    assert ("candidate_interview_scheduled", "cancelled") in by
    assert ("candidate_interview_rescheduled", "queued") in by
    assert ("candidate_interview_reminder", "queued") in by

    # Cancel: reminder cancelled, «отменено» queued and delivered.
    cancelled = client.patch(
        f"/events/{created.json()['id']}",
        json={"expected_version": 2, "status": "cancelled"},
        headers={"X-CSRF-Token": csrf},
    )
    assert cancelled.status_code == 200, cancelled.text
    rows = _candidate_rows(pg_db, candidate.id)
    cancelled_letter = [
        r
        for r in rows
        if r.notification_type == NotificationType.CANDIDATE_INTERVIEW_CANCELLED
        and r.status == DeliveryStatus.QUEUED
    ]
    assert len(cancelled_letter) == 2  # both channels

    # Deliver one cancelled-letter row end to end.
    target = cancelled_letter[0]
    target.status = DeliveryStatus.SENDING
    target.started_at = utc_now()
    target.lease_expires_at = utc_now() + timedelta(minutes=2)
    pg_db.commit()
    assert process_external_row(pg_db, target.id, settings=settings, now=utc_now()) == "accepted"
    pg_db.refresh(target)
    assert target.status == DeliveryStatus.ACCEPTED


def test_parallel_workers_deliver_once(
    client: TestClient,
    pg_db: Session,
    smtp_stub: SmtpStub,
    settings: Settings,
) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = _seed_candidate_channels(pg_db, hr, email="race@example.com")
    csrf = _login(client, "hr1")
    send = client.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "integ-race-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201
    row_id = send.json()["messages"][0]["id"]

    # Claim once (SKIP LOCKED semantics), then race two finalize threads.
    row = pg_db.get(NotificationOutbox, UUID(row_id))
    assert row is not None
    row.status = DeliveryStatus.SENDING
    row.started_at = utc_now()
    row.lease_expires_at = utc_now() + timedelta(minutes=2)
    pg_db.commit()

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def deliver() -> None:
        barrier.wait()
        with Session(pg_db.get_bind()) as session:
            outcomes.append(process_external_row(session, row_id, settings=settings, now=utc_now()))

    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Exactly one accepted; the loser backs off (sending) without a second
    # provider call.
    assert outcomes.count("accepted") == 1
    assert len(smtp_stub.data_blocks) == 1


def test_consent_revoke_before_send_stops_message(
    client: TestClient,
    pg_db: Session,
    smtp_stub: SmtpStub,
    settings: Settings,
) -> None:
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = _seed_candidate_channels(pg_db, hr, email="rev@example.com")
    csrf = _login(client, "hr1")
    send = client.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "integ-revoke-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201
    row_id = send.json()["messages"][0]["id"]

    # Revoke BEFORE the worker touches the row: the pending send must be
    # cancelled outright (not merely skipped at send time).
    response = client.post(
        f"/candidates/{candidate.id}/channels/email/consent",
        json={"granted": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    row = pg_db.get(NotificationOutbox, UUID(row_id))
    assert row is not None
    pg_db.refresh(row)
    assert row.status == DeliveryStatus.CANCELLED

    # Even a forged 'sending' state skips without any network call.
    row.status = DeliveryStatus.SENDING
    row.started_at = utc_now()
    row.lease_expires_at = utc_now() + timedelta(minutes=2)
    pg_db.commit()
    assert process_external_row(pg_db, row_id, settings=settings, now=utc_now()) == "skipped"
    assert smtp_stub.data_blocks == []


def test_telegram_invite_flow_against_real_poll(
    client: TestClient,
    pg_db: Session,
    telegram_stub: TelegramStub,
) -> None:
    """The candidate presses Start (stubbed Bot API inbox) and the HR
    confirms: the binding and the telegram_start consent appear."""
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr, full_name="Приглашённый Кандидат")
    csrf = _login(client, "hr1")

    invite = client.post(
        f"/candidates/{candidate.id}/channels/telegram/invite",
        headers={"X-CSRF-Token": csrf},
    )
    assert invite.status_code == 201, invite.text
    token_raw = invite.json()["deep_link"].rsplit("=", 1)[-1]

    # The stub inbox now holds the /start from the candidate's chat.
    telegram_stub.script(
        "getUpdates",
        [
            (
                200,
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 101,
                            "message": {
                                "text": f"/start {token_raw}",
                                "chat": {"id": 99112233},
                            },
                        }
                    ],
                },
            )
        ],
    )
    confirm = client.post(
        f"/candidates/{candidate.id}/channels/telegram/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json() == {"linked": True, "state": "allowed"}

    link = pg_db.get(CandidateTelegramLink, candidate.id)
    assert link is not None and link.chat_id == 99112233
    consent = pg_db.get(CandidateChannelConsent, (candidate.id, "telegram"))
    assert consent is not None and consent.granted and consent.source == "telegram_start"
    # Only the SHA-256 hash of the token is stored.
    assert link.chat_id is not None
    stored_hashes = (
        pg_db.execute(text("SELECT token_hash FROM candidate_telegram_link_tokens")).scalars().all()
    )
    assert stored_hashes == [hashlib.sha256(token_raw.encode()).hexdigest()]


# --- Phase 10 rework: email double opt-in end-to-end, parallel idempotency --------


def test_email_double_opt_in_end_to_end(
    client: TestClient,
    pg_db: Session,
    settings: Settings,
    smtp_stub: SmtpStub,
) -> None:
    """Initiation -> the letter through the real worker/SMTP stub -> the
    candidate's click on the public link -> regular messages allowed."""
    import email as email_lib
    import email.policy
    import re as re_lib

    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = make_candidate(
        pg_db, owner=hr, email="optin@example.com", full_name="Оптин Подтверждаев"
    )
    csrf = _login(client, "hr1")

    # Before any consent: the channel is pending, sends are refused.
    body = client.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == "pending"
    refused = client.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "optin-refused-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert refused.status_code == 409

    # HR initiates; the worker delivers the letter through the SMTP stub.
    initiated = client.post(
        f"/candidates/{candidate.id}/channels/email/confirmation",
        headers={"X-CSRF-Token": csrf},
    )
    assert initiated.status_code == 201, initiated.text
    assert initiated.json()["queued"] is True
    rows = [
        row
        for row in _candidate_rows(pg_db, candidate.id)
        if row.notification_type == NotificationType.CANDIDATE_EMAIL_CONFIRM
    ]
    assert len(rows) == 1
    claimed = claim_batch(pg_db, now=utc_now(), batch_size=5, lease_seconds=120)
    assert claimed
    for row in claimed:
        assert process_external_row(pg_db, row.id, settings=settings, now=utc_now()) == "accepted"
    assert len(smtp_stub.data_blocks) == 1
    parsed = email_lib.message_from_bytes(smtp_stub.data_blocks[0], policy=email_lib.policy.default)
    assert parsed["To"] == "optin@example.com"
    mail_body = parsed.get_body(preferencelist=("plain",))
    assert mail_body is not None
    link_match = re_lib.search(r"https://hr\.example\.test/[^\s]+", mail_body.get_content())
    assert link_match is not None
    confirm_link = link_match.group(0)
    assert "token=" in confirm_link
    # Only the hash lives in the DB.
    from app.models import CandidateEmailConfirmToken as Token

    tokens = pg_db.execute(select(Token)).scalars().all()
    assert len(tokens) == 1
    assert tokens[0].token_hash not in confirm_link

    # The candidate clicks: the consent activates.
    page = client.get(confirm_link)
    assert page.status_code == 200
    assert "подтверждено" in page.text
    body = client.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == "allowed"

    # A regular message now queues and delivers.
    send = client.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "optin-send-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201, send.text
    claimed = claim_batch(pg_db, now=utc_now(), batch_size=5, lease_seconds=120)
    for row in claimed:
        process_external_row(pg_db, row.id, settings=settings, now=utc_now())
    assert len(smtp_stub.data_blocks) == 2  # the letter + the regular message

    # The link is one-shot.
    assert client.get(confirm_link).status_code == 410


def test_parallel_identical_sends_produce_one_logical_send(
    client: TestClient, pg_db: Session, settings: Settings
) -> None:
    """Two truly concurrent identical requests (real threads, real PG unique
    index): exactly one outbox row per channel, identical responses."""
    from concurrent.futures import ThreadPoolExecutor

    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    candidate = _seed_candidate_channels(pg_db, hr, email="par@example.com")
    csrf = _login(client, "hr1")
    payload = {
        "message_type": "document_request",
        "documents": ["Паспорт"],
        "idempotency_key": "parallel-key-1",
    }
    url = f"/candidates/{candidate.id}/messages/send"
    headers = {"X-CSRF-Token": csrf}

    barrier_start = __import__("threading").Barrier(2)

    def fire() -> Any:
        barrier_start.wait()
        return client.post(url, json=payload, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fire) for _ in range(2)]
        responses = [future.result() for future in futures]

    for response in responses:
        assert response.status_code in (200, 201), response.text
    ids = {message["id"] for r in responses for message in r.json()["messages"]}
    assert len(ids) == 2  # one row per channel (email + telegram), not four
    rows = _candidate_rows(pg_db, candidate.id)
    assert len(rows) == 2
