"""Unit tests for one-way candidate messages (phase 10).

Coverage matrix:

* template rendering: all six types, safe substitution of name/time/place/
  document list, control-character sanitization;
* RBAC/IDOR: unauthenticated 401, foreign HR 404, manager allowed, admin
  denied without an explicit pilot grant, deleted candidate 404;
* CSRF on every mutating endpoint;
* per-channel consent: record/revoke/re-enable, email pinned to the exact
  address, revocation stops pending sends;
* Telegram invite/confirm/unlink flow (poll stubbed, no network);
* preview/send/cancel/history happy paths and refusals (no allowed
  channel, duplicate pending message, rate limit);
* audit rows without PII/message text; caplog carries no text/addresses.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.candidate_messages import (
    CHANNEL_STATE_ALLOWED,
    CHANNEL_STATE_FORBIDDEN,
    CHANNEL_STATE_NOT_CONNECTED,
    CHANNEL_STATE_PENDING,
    render_candidate_message,
    sanitize_inline,
)
from app.config import Settings
from app.main import create_app
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateChannelConsent,
    CandidateTelegramLink,
    DeliveryStatus,
    EventType,
    NotificationOutbox,
    User,
    UserRole,
)
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_event, make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.fixture()
def hr_user(db_session: Session) -> User:
    return make_user(db_session, username="hr1", role=UserRole.HR)


@pytest.fixture()
def channels_app(unit_engine: Any) -> Iterator[TestClient]:
    """App with BOTH external channels configured (dummy secrets, no
    network is ever touched: the poll and senders are stubbed in tests)."""
    settings = Settings.model_validate(
        {
            "APP_ENV": "test",
            "APP_DEBUG": "false",
            "SECRET_KEY": "unit-test-secret-key-0123456789abcdef",
            "SMTP_ENABLED": "true",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "2525",
            "SMTP_ENCRYPTION": "starttls",
            "SMTP_USERNAME": "mailer@example.test",
            "SMTP_PASSWORD": "mailer-secret",
            "SMTP_FROM_ADDRESS": "hr@example.test",
            "TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "123:test-token",
            "TELEGRAM_API_BASE_URL": "https://telegram.example.test",
            "TELEGRAM_BOT_USERNAME": "hr_test_bot",
        }
    )
    app = create_app(settings, engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


# --- Template rendering -------------------------------------------------------


def test_render_all_six_types() -> None:
    candidate = Candidate(
        full_name="Иванов Иван Иванович",
        full_name_normalized="иванов иван иванович",
        email="ivanov@example.com",
        position="Инженер по качеству",
    )
    scheduled = render_candidate_message(
        "interview_scheduled", candidate=candidate, starts_at_local="05.09.2026 10:00"
    )
    assert scheduled.title == "Собеседование назначено"
    assert "Здравствуйте, Иванов Иван Иванович!" in scheduled.body
    assert "05.09.2026 10:00" in scheduled.body
    assert "Инженер по качеству" in scheduled.body

    reminder = render_candidate_message(
        "interview_reminder", candidate=candidate, starts_at_local="05.09.2026 10:00"
    )
    assert reminder.title == "Напоминание о собеседовании"

    rescheduled = render_candidate_message(
        "interview_rescheduled",
        candidate=candidate,
        starts_at_local="07.09.2026 15:00",
        previous_starts_at_local="05.09.2026 10:00",
    )
    assert "05.09.2026 10:00" in rescheduled.body
    assert "07.09.2026 15:00" in rescheduled.body

    cancelled = render_candidate_message(
        "interview_cancelled", candidate=candidate, starts_at_local="05.09.2026 10:00"
    )
    assert cancelled.title == "Собеседование отменено"

    request_ = render_candidate_message(
        "document_request", candidate=candidate, documents=["Паспорт", "Диплом"]
    )
    assert "— Паспорт" in request_.body and "— Диплом" in request_.body

    doc_reminder = render_candidate_message(
        "document_reminder", candidate=candidate, documents=["Справка 2-НДФЛ"]
    )
    assert "Справка 2-НДФЛ" in doc_reminder.body


def test_render_sanitizes_control_characters_and_location() -> None:
    candidate = Candidate(
        full_name="Ольга\r\nПетрова",
        full_name_normalized="ольга петрова",
        position="Аналитик",
    )
    message = render_candidate_message(
        "interview_scheduled",
        candidate=candidate,
        starts_at_local="05.09.2026 10:00",
        location="Офис <script>alert('x')</script>\nпер. Ленина, 1",
    )
    # No raw control characters smuggled into the single-line fields.
    assert "\r" not in message.body
    assert "Ольга Петрова" in message.body  # CRLF collapsed inside the greeting
    assert "<script>" in message.body  # plain text: shown as-is, never executed
    # The location stays on one line (newline replaced by a space).
    assert "Офис <script>alert('x')</script> пер. Ленина, 1." in message.body


def test_sanitize_inline_caps_length() -> None:
    assert sanitize_inline("a" * 500, max_length=100) == "a" * 100
    assert sanitize_inline(" x \t y ", max_length=100) == "x y"
    assert sanitize_inline("a\x00b\x1fc\x7fd", max_length=100) == "a b c d"


# --- RBAC / IDOR ---------------------------------------------------------------


def test_channels_require_authentication(
    client: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="a@example.com")
    response = client.get(f"/candidates/{candidate.id}/channels")
    assert response.status_code == 401


def test_channel_access_matrix(client: TestClient, db_session: Session, hr_user: User) -> None:
    other_hr = make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    admin = make_user(db_session, username="adm", role=UserRole.ADMIN)
    own = make_candidate(db_session, owner=hr_user, email="own@example.com")
    foreign = make_candidate(db_session, owner=other_hr, email="foreign@example.com")

    _login(client, "hr1")
    assert client.get(f"/candidates/{own.id}/channels").status_code == 200
    assert client.get(f"/candidates/{foreign.id}/channels").status_code == 404

    _login(client, "mgr")
    assert client.get(f"/candidates/{foreign.id}/channels").status_code == 200

    # An admin does NOT get candidate messages by role alone (403), but
    # still sees the card itself (200).
    _login(client, "adm")
    assert client.get(f"/candidates/{foreign.id}").status_code == 200
    assert client.get(f"/candidates/{foreign.id}/channels").status_code == 403
    assert client.get(f"/candidates/{foreign.id}/messages").status_code == 403

    # The explicit pilot grant opens the scope (and is audited).
    db_session.add(
        AccessGrant(user_id=admin.id, scope=AccessGrantScope.PILOT_FULL_ACCESS, granted_at=NOW)
    )
    db_session.commit()
    assert client.get(f"/candidates/{foreign.id}/channels").status_code == 200

    # Deleted candidates are 404 for everyone.
    deleted = make_candidate(db_session, owner=hr_user, deleted=True)
    _login(client, "hr1")
    assert client.get(f"/candidates/{deleted.id}/channels").status_code == 404


def test_mutations_require_csrf(client: TestClient, db_session: Session, hr_user: User) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="a@example.com")
    _login(client, "hr1")
    response = client.post(
        f"/candidates/{candidate.id}/channels/email/consent", json={"granted": True}
    )
    assert response.status_code == 403


# --- Consent --------------------------------------------------------------------


def test_email_consent_lifecycle_and_pending_cancellation(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="cand@example.com")
    csrf = _login(channels_app, "hr1")

    # Without a recorded consent the channel is pending (address present).
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_PENDING
    assert body["allowed_channels"] == []

    # Granting without an address in the card is refused.
    no_email = make_candidate(db_session, owner=hr_user)
    response = channels_app.post(
        f"/candidates/{no_email.id}/channels/email/consent",
        json={"granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422

    # Grant consent for the current address.
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/email/consent",
        json={"granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["granted"] is True
    assert response.json()["policy_version"] == "phase10-v1"

    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_ALLOWED
    assert body["email"]["target_masked"].startswith("c")
    assert "@" in body["email"]["target_masked"]
    assert "cand@example.com" not in str(body["email"]["target_masked"])
    assert "email" in body["allowed_channels"]

    # Queue a message, then revoke: the pending row must be cancelled.
    send = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={"message_type": "document_request", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201, send.text
    row_id = send.json()["messages"][0]["id"]
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/email/consent",
        json={"granted": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    row = db_session.get(NotificationOutbox, UUID(row_id))
    assert row is not None
    db_session.refresh(row)
    assert row.status == DeliveryStatus.CANCELLED
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_FORBIDDEN
    assert body["allowed_channels"] == []

    # Re-enable: allowed again, a fresh send works.
    channels_app.post(
        f"/candidates/{candidate.id}/channels/email/consent",
        json={"granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    send = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={"message_type": "document_reminder", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201


def test_email_consent_is_pinned_to_the_address(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="first@example.com")
    csrf = _login(channels_app, "hr1")
    channels_app.post(
        f"/candidates/{candidate.id}/channels/email/consent",
        json={"granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    # The address changes in the card -> the recorded consent no longer
    # covers it: the channel re-opens the confirmation (fail-closed).
    candidate.email = "second@example.com"
    candidate.email_normalized = "second@example.com"
    db_session.commit()
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_PENDING
    assert body["allowed_channels"] == []


# --- Telegram invite / confirm / unlink ------------------------------------------


class _FakeUpdates:
    """Stub for the getUpdates poll result."""

    def __init__(self, *, ok: bool = True, updates: tuple = ()) -> None:
        self.ok = ok
        self.updates = updates
        self.max_update_id = max((u.update_id for u in updates), default=None)


class _FakeStart:
    def __init__(self, update_id: int, chat_id: int, token: str) -> None:
        self.update_id = update_id
        self.chat_id = chat_id
        self.token = token


@pytest.fixture()
def poll_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture invitation tokens from the DB and feed them back as /start."""
    holder: dict[str, Any] = {"updates": (), "ok": True}

    def _poll(config: object, *, offset: int | None) -> Any:  # pragma: no cover - holder-driven
        return _FakeUpdates(ok=holder["ok"], updates=tuple(holder["updates"]))

    from app.routers import candidate_messages as router_module

    monkeypatch.setattr(router_module, "_poll_starts_impl", _poll)
    return holder


def _seed_start(
    db_session: Session, candidate_id: UUID, token_raw: str, chat_id: int = 555001
) -> None:
    """Simulate the candidate pressing Start with the invitation token."""
    import hashlib

    from app.models import TelegramStartEvent

    db_session.add(
        TelegramStartEvent(
            token_hash=hashlib.sha256(token_raw.encode()).hexdigest(),
            chat_id=chat_id,
            seen_at=NOW,
        )
    )
    db_session.commit()


def test_telegram_invite_confirm_unlink(
    channels_app: TestClient,
    db_session: Session,
    hr_user: User,
    poll_stub: dict[str, Any],
) -> None:
    from app.models import TelegramPollState

    db_session.add(TelegramPollState(id=1, last_update_id=41))
    db_session.commit()
    candidate = make_candidate(db_session, owner=hr_user)
    csrf = _login(channels_app, "hr1")

    # Initial state: no binding, no invite -> not connected.
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["telegram"]["state"] == CHANNEL_STATE_NOT_CONNECTED

    # Create the invitation (raw token shown once).
    invite = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/invite",
        headers={"X-CSRF-Token": csrf},
    )
    assert invite.status_code == 201, invite.text
    deep_link = invite.json()["deep_link"]
    assert deep_link.startswith("https://t.me/hr_test_bot?start=")
    token_raw = deep_link.rsplit("=", 1)[-1]

    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["telegram"]["state"] == CHANNEL_STATE_PENDING
    assert body["telegram"]["invite_active"] is True

    # Confirm before the candidate pressed Start -> 409, nothing bound.
    poll_stub["updates"] = ()
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409

    # The candidate pressed Start (stubbed poll observes it).
    poll_stub["updates"] = (_FakeStart(update_id=42, chat_id=555001, token=token_raw),)
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"linked": True, "state": CHANNEL_STATE_ALLOWED}

    link = db_session.get(CandidateTelegramLink, candidate.id)
    assert link is not None and link.chat_id == 555001
    # The voluntary Start recorded the consent itself.
    consent = db_session.get(CandidateChannelConsent, (candidate.id, "telegram"))
    assert consent is not None and consent.granted is True
    assert consent.source == "telegram_start"
    assert consent.granted_by_user_id is None

    # The token is single-use: a second confirm with no new Start fails.
    poll_stub["updates"] = ()
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409

    # Queue a telegram message, then unlink: pending rows are cancelled.
    send = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={"message_type": "document_request", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201
    row_id = send.json()["messages"][0]["id"]
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/unlink",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    row = db_session.get(NotificationOutbox, UUID(row_id))
    assert row is not None
    db_session.refresh(row)
    assert row.status == DeliveryStatus.CANCELLED
    link = db_session.get(CandidateTelegramLink, candidate.id)
    assert link is not None
    db_session.refresh(link)
    assert link.revoked_at is not None


def test_telegram_confirm_refuses_chat_held_by_user(
    channels_app: TestClient, db_session: Session, hr_user: User, poll_stub: dict[str, Any]
) -> None:
    from app.models import TelegramLink, TelegramPollState

    db_session.add(TelegramPollState(id=1))
    db_session.add(TelegramLink(user_id=hr_user.id, chat_id=777001, linked_at=NOW))
    db_session.commit()
    candidate = make_candidate(db_session, owner=hr_user)
    csrf = _login(channels_app, "hr1")
    invite = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/invite",
        headers={"X-CSRF-Token": csrf},
    )
    token_raw = invite.json()["deep_link"].rsplit("=", 1)[-1]
    poll_stub["updates"] = (_FakeStart(update_id=7, chat_id=777001, token=token_raw),)
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert db_session.get(CandidateTelegramLink, candidate.id) is None


def test_telegram_hr_refusal_is_not_overwritten_by_start(
    channels_app: TestClient, db_session: Session, hr_user: User, poll_stub: dict[str, Any]
) -> None:
    from app.models import TelegramPollState

    db_session.add(TelegramPollState(id=1))
    candidate = make_candidate(db_session, owner=hr_user)
    csrf = _login(channels_app, "hr1")
    # HR records an explicit refusal first.
    channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/consent",
        json={"granted": False},
        headers={"X-CSRF-Token": csrf},
    )
    invite = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/invite",
        headers={"X-CSRF-Token": csrf},
    )
    token_raw = invite.json()["deep_link"].rsplit("=", 1)[-1]
    poll_stub["updates"] = (_FakeStart(update_id=9, chat_id=555002, token=token_raw),)
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/telegram/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    # The link is bound, but the explicit refusal wins: state forbidden.
    assert response.status_code == 200
    consent = db_session.get(CandidateChannelConsent, (candidate.id, "telegram"))
    assert consent is not None
    assert consent.granted is False
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["telegram"]["state"] == CHANNEL_STATE_FORBIDDEN


# --- Preview / send / cancel / history -------------------------------------------


def _allow_email(db_session: Session, candidate: Candidate) -> None:
    db_session.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="email",
            granted=True,
            granted_at=NOW,
            source="hr_recorded",
            policy_version="phase10-v1",
            email_normalized=candidate.email_normalized,
        )
    )
    db_session.commit()


def test_preview_renders_text_and_channels(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="pv@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    response = channels_app.post(
        f"/candidates/{candidate.id}/messages/preview",
        json={"message_type": "document_request", "documents": ["Паспорт", "ИНН"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Запрос документов"
    assert "— Паспорт" in body["body"] and "— ИНН" in body["body"]
    assert body["channels"] == ["email"]
    # Nothing was queued by a preview.
    assert (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
        == []
    )


def test_send_happy_path_and_duplicate_guard(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="sd@example.com")
    _allow_email(db_session, candidate)
    interview = make_event(
        db_session,
        candidate=candidate,
        author=hr_user,
        assignee=hr_user,
        type_=EventType.INTERVIEW,
        starts_at=NOW + timedelta(days=3),
    )
    csrf = _login(channels_app, "hr1")

    response = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "interview_scheduled",
            "event_id": str(interview.id),
            "location": "Офис, пер. Ленина 1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["channels"] == ["email"]
    message = body["messages"][0]
    assert message["status"] == "queued"
    assert message["message_type"] == "candidate_interview_scheduled"
    assert message["event_id"] == str(interview.id)
    assert "Офис, пер. Ленина 1" in message["body"]
    row = db_session.get(NotificationOutbox, UUID(message["id"]))
    assert row is not None
    assert row.recipient_candidate_id == candidate.id
    assert row.recipient_user_id is None and row.external_recipient is None
    assert row.consent_snapshot == {
        "channel": "email",
        "granted": True,
        "source": "hr_recorded",
        "policy_version": "phase10-v1",
    }

    # Duplicate while still pending -> 409, no second row.
    response = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={"message_type": "interview_scheduled", "event_id": str(interview.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert (
        len(
            db_session.execute(
                select(NotificationOutbox).where(
                    NotificationOutbox.recipient_candidate_id == candidate.id
                )
            )
            .scalars()
            .all()
        )
        == 1
    )


def test_send_refusals(channels_app: TestClient, db_session: Session, hr_user: User) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="rf@example.com")
    other = make_candidate(db_session, owner=hr_user, email="rf2@example.com")
    interview = make_event(
        db_session,
        candidate=other,
        author=hr_user,
        assignee=hr_user,
        type_=EventType.INTERVIEW,
        starts_at=NOW + timedelta(days=3),
    )
    csrf = _login(channels_app, "hr1")

    def send(payload: dict) -> Any:
        return channels_app.post(
            f"/candidates/{candidate.id}/messages/send",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )

    # No channels allowed yet.
    assert send({"message_type": "document_request", "documents": ["Паспорт"]}).status_code == 409
    _allow_email(db_session, candidate)

    # Interview types require an event of THIS candidate.
    assert send({"message_type": "interview_scheduled"}).status_code == 422
    assert (
        send({"message_type": "interview_scheduled", "event_id": str(interview.id)}).status_code
        == 422
    )

    # Reminder for a past interview is refused.
    past = make_event(
        db_session,
        candidate=candidate,
        author=hr_user,
        assignee=hr_user,
        type_=EventType.INTERVIEW,
        starts_at=NOW - timedelta(days=1),
    )
    assert send({"message_type": "interview_reminder", "event_id": str(past.id)}).status_code == 422

    # Document validation.
    assert send({"message_type": "document_request"}).status_code == 422
    assert send({"message_type": "document_request", "documents": []}).status_code == 422
    assert send({"message_type": "document_request", "documents": [" "]}).status_code == 422
    assert send({"message_type": "document_request", "documents": ["x"] * 21}).status_code == 422
    assert send({"message_type": "document_request", "documents": ["x" * 201]}).status_code == 422
    # Location only applies to interview messages.
    assert (
        send(
            {"message_type": "document_request", "documents": ["Паспорт"], "location": "Офис"}
        ).status_code
        == 422
    )
    # Unknown type / channel.
    assert send({"message_type": "spam"}).status_code == 422
    assert (
        send(
            {"message_type": "document_request", "documents": ["Паспорт"], "channel": "sms"}
        ).status_code
        == 422
    )
    # Telegram not allowed -> explicit channel choice refused.
    assert (
        send(
            {"message_type": "document_request", "documents": ["Паспорт"], "channel": "telegram"}
        ).status_code
        == 409
    )
    # Nothing was queued by any refused request.
    assert (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
        == []
    )


def test_send_rate_limited(channels_app: TestClient, db_session: Session, hr_user: User) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="rl@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    codes = []
    for i in range(22):
        response = channels_app.post(
            f"/candidates/{candidate.id}/messages/send",
            json={"message_type": "document_request", "documents": [f"Документ {i}"]},
            headers={"X-CSRF-Token": csrf},
        )
        codes.append(response.status_code)
    assert 429 in codes
    assert codes.count(429) >= 1
    assert "Retry-After" in response.headers or codes[-1] == 429


def test_cancel_and_history(channels_app: TestClient, db_session: Session, hr_user: User) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="ch@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    send = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={"message_type": "document_request", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    row_id = send.json()["messages"][0]["id"]

    # History shows the exact text and status.
    history = channels_app.get(f"/candidates/{candidate.id}/messages").json()
    assert history["total"] == 1
    item = history["items"][0]
    assert item["id"] == row_id
    assert item["title"] == "Запрос документов"
    assert "— Паспорт" in item["body"]

    # Cancel the queued message.
    response = channels_app.post(
        f"/candidates/{candidate.id}/messages/{row_id}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    # A terminal row cannot be cancelled again.
    response = channels_app.post(
        f"/candidates/{candidate.id}/messages/{row_id}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409

    # Foreign candidate / foreign message: 404 for another HR.
    other_hr = make_user(db_session, username="hr2", role=UserRole.HR)
    make_candidate(db_session, owner=other_hr, email="x@example.com")
    csrf2 = _login(channels_app, "hr2")
    assert channels_app.get(f"/candidates/{candidate.id}/messages").status_code == 404
    assert (
        channels_app.post(
            f"/candidates/{candidate.id}/messages/{row_id}/cancel",
            headers={"X-CSRF-Token": csrf2},
        ).status_code
        == 404
    )


# --- Audit and logs --------------------------------------------------------------


def test_audit_rows_have_no_pii_or_text(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(
        db_session,
        owner=hr_user,
        full_name="Секретная Фамилия",
        email="audit@example.com",
    )
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={"message_type": "document_request", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    events = (
        db_session.execute(select(AuditEvent).where(AuditEvent.candidate_id == candidate.id))
        .scalars()
        .all()
    )
    assert any(e.action == AuditAction.CANDIDATE_MESSAGE_QUEUED for e in events)
    for event in events:
        blob = f"{event.details or ''}"
        assert "Секретная" not in blob
        assert "audit@example.com" not in blob
        assert "Паспорт" not in blob


def test_no_message_text_or_targets_in_logs(
    channels_app: TestClient, db_session: Session, hr_user: User, caplog: pytest.LogCaptureFixture
) -> None:
    candidate = make_candidate(
        db_session, owner=hr_user, full_name="Логгинг Тестов", email="logs@example.com"
    )
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    with caplog.at_level(logging.DEBUG, logger="app"):
        channels_app.post(
            f"/candidates/{candidate.id}/messages/send",
            json={"message_type": "document_request", "documents": ["Секретный документ"]},
            headers={"X-CSRF-Token": csrf},
        )
        channels_app.get(f"/candidates/{candidate.id}/messages")
    for record in caplog.records:
        text = record.getMessage()
        assert "Секретный документ" not in text
        assert "logs@example.com" not in text
        assert "Логгинг" not in text
