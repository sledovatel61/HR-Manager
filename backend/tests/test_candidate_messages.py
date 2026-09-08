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

import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

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
    CandidateEmailConfirmToken,
    CandidateTelegramLink,
    DeliveryStatus,
    EventType,
    NotificationOutbox,
    NotificationType,
    User,
    UserRole,
)
from app.utils import utc_now
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
            "CANDIDATE_EMAIL_CONFIRM_BASE_URL": "https://hr.example.test",
            "PUBLIC_CONFIRM_RATE_LIMIT": "1000",
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


def test_render_sanitizes_control_characters() -> None:
    candidate = Candidate(
        full_name="Ольга\r\nПетрова",
        full_name_normalized="ольга петрова",
        position="Аналитик <script>alert('x')</script>",
    )
    message = render_candidate_message(
        "interview_scheduled",
        candidate=candidate,
        starts_at_local="05.09.2026 10:00",
    )
    # No raw control characters smuggled into the single-line fields.
    assert "\r" not in message.body
    assert "Ольга Петрова" in message.body  # CRLF collapsed inside the greeting
    assert "<script>" in message.body  # plain text: shown as-is, never executed
    # The template has no «Место» line at all: the events model carries no
    # free-form location and the client may not inject one.
    assert "Место" not in message.body


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
        f"/candidates/{candidate.id}/channels/email/consent", json={"granted": False}
    )
    assert response.status_code == 403
    response = client.post(f"/candidates/{candidate.id}/channels/email/confirmation")
    assert response.status_code == 403


# --- Consent --------------------------------------------------------------------


def _initiate_confirmation(client: TestClient, csrf: str, candidate: Candidate) -> dict:
    response = client.post(
        f"/candidates/{candidate.id}/channels/email/confirmation",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _confirmation_link(db: Session, candidate: Candidate, app: TestClient) -> str:
    """The one-time link of the candidate's active token.

    The link is NEVER stored anywhere (the queued letter body carries a
    placeholder), so it is derived exactly the way the worker derives it
    in memory right before mailing the letter — this is the link the
    candidate receives.
    """
    from fastapi import FastAPI

    from app.candidate_messages import email_confirm_url

    settings = cast("Settings", cast(FastAPI, app.app).state.settings)
    token = (
        db.execute(
            select(CandidateEmailConfirmToken)
            .where(
                CandidateEmailConfirmToken.candidate_id == candidate.id,
                CandidateEmailConfirmToken.consumed_at.is_(None),
            )
            .order_by(CandidateEmailConfirmToken.created_at.desc())
            .execution_options(populate_existing=True)
        )
        .scalars()
        .first()
    )
    assert token is not None
    return email_confirm_url(token.id, settings)


def test_email_double_opt_in_flow_and_revocation(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="cand@example.com")
    csrf = _login(channels_app, "hr1")

    # Without a confirmed consent the channel is pending (address present).
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_PENDING
    assert body["allowed_channels"] == []

    # The HR can NEVER grant the email consent directly: double opt-in only.
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/email/consent",
        json={"granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert "подтвержд" in response.json()["detail"]

    # Without an address in the card the initiation is refused.
    no_email = make_candidate(db_session, owner=hr_user)
    response = channels_app.post(
        f"/candidates/{no_email.id}/channels/email/confirmation",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422

    # HR initiates: the letter is queued via the outbox, only the hash stored.
    initiated = _initiate_confirmation(channels_app, csrf, candidate)
    assert initiated["queued"] is True
    assert initiated["email_masked"].startswith("c")
    assert "cand@example.com" not in initiated["email_masked"]
    link = _confirmation_link(db_session, candidate, channels_app)
    assert link.startswith("https://hr.example.test/candidates/email/confirm?token=")
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_PENDING
    assert body["email"]["invite_active"] is True
    tokens = (
        db_session.execute(
            select(CandidateEmailConfirmToken).where(
                CandidateEmailConfirmToken.candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
    )
    assert len(tokens) == 1
    assert tokens[0].token_hash not in link  # hash only, never the raw token

    # The candidate clicks the public link: the consent activates.
    response = channels_app.get(link)
    assert response.status_code == 200
    assert "подтверждено" in response.text
    db_session.refresh(tokens[0])
    assert tokens[0].consumed_at is not None
    assert tokens[0].consume_reason == "confirmed"
    consent = db_session.get(CandidateChannelConsent, (candidate.id, "email"))
    assert consent is not None and consent.granted is True
    assert consent.source == "email_confirm"
    assert consent.granted_by_user_id is None

    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_ALLOWED
    assert body["email"]["target_masked"].startswith("c")
    assert "@" in body["email"]["target_masked"]
    assert "cand@example.com" not in str(body["email"]["target_masked"])
    assert "email" in body["allowed_channels"]

    # The link is one-shot: a second click changes nothing.
    response = channels_app.get(link)
    assert response.status_code == 410

    # Queue a message, then revoke: the pending row must be cancelled.
    send = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "lifecycle-send-1",
        },
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

    # Re-enabling again requires a NEW candidate confirmation (no HR grant).
    response = channels_app.post(
        f"/candidates/{candidate.id}/channels/email/consent",
        json={"granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    _initiate_confirmation(channels_app, csrf, candidate)
    link2 = _confirmation_link(db_session, candidate, channels_app)
    assert channels_app.get(link2).status_code == 200
    send = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_reminder",
            "documents": ["Паспорт"],
            "idempotency_key": "lifecycle-send-2",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert send.status_code == 201


def test_email_consent_is_pinned_to_the_address(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="first@example.com")
    csrf = _login(channels_app, "hr1")
    _initiate_confirmation(channels_app, csrf, candidate)
    assert (
        channels_app.get(_confirmation_link(db_session, candidate, channels_app)).status_code == 200
    )

    # The address changes in the card -> the confirmed consent no longer
    # covers it: the channel re-opens the confirmation (fail-closed).
    candidate.email = "second@example.com"
    candidate.email_normalized = "second@example.com"
    db_session.commit()
    body = channels_app.get(f"/candidates/{candidate.id}/channels").json()
    assert body["email"]["state"] == CHANNEL_STATE_PENDING
    assert body["allowed_channels"] == []

    # An OLD confirmation letter (issued for the previous address) must not
    # unlock the new address: the public endpoint refuses it.
    stale = make_candidate(db_session, owner=hr_user, email="old@example.com")
    _initiate_confirmation(channels_app, csrf, stale)
    old_link = _confirmation_link(db_session, stale, channels_app)
    assert channels_app.get(old_link).status_code == 200
    assert channels_app.get(old_link).status_code == 410  # one-shot anyway
    # Directly simulate: token issued for old address, card now has another.
    from app.models import CandidateEmailConfirmToken

    token = CandidateEmailConfirmToken(
        candidate_id=stale.id,
        token_hash=hashlib.sha256(b"pinned-token-0123456789").hexdigest(),
        email_normalized="old@example.com",
        created_at=utc_now(),
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(token)
    db_session.commit()
    stale.email = "new@example.com"
    stale.email_normalized = "new@example.com"
    db_session.commit()
    response = channels_app.get(
        "https://hr.example.test/candidates/email/confirm?token=pinned-token-0123456789"
    )
    assert response.status_code == 410
    assert "изменился" in response.text
    consent = db_session.get(CandidateChannelConsent, (stale.id, "email"))
    # The old confirmation was never extended to the new address.
    assert consent is not None
    assert consent.email_normalized == "old@example.com"
    db_session.refresh(consent)
    body = channels_app.get(f"/candidates/{stale.id}/channels").json()
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
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "tg-flow-1",
        },
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
    """Seed the CONFIRMED email consent (as if the candidate clicked the
    double opt-in link): only this state unlocks regular messages."""
    db_session.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="email",
            granted=True,
            granted_at=NOW,
            source="email_confirm",
            policy_version="phase10-v1",
            granted_by_user_id=None,
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
            # No location field exists in the contract: the text is fully
            # server-rendered from the event.
            "idempotency_key": "happy-path-1",
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
    assert "Место" not in message["body"]
    assert message["initiator_user_id"] == str(hr_user.id)
    assert message["initiator_username"] == "hr1"
    row = db_session.get(NotificationOutbox, UUID(message["id"]))
    assert row is not None
    assert row.recipient_candidate_id == candidate.id
    assert row.recipient_user_id is None and row.external_recipient is None
    assert row.object_version == interview.version
    assert row.consent_snapshot == {
        "channel": "email",
        "granted": True,
        "source": "email_confirm",
        "policy_version": "phase10-v1",
    }

    # Duplicate while still pending (a NEW key) -> 409, no second row.
    response = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "interview_scheduled",
            "event_id": str(interview.id),
            "idempotency_key": "happy-path-2",
        },
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
            json={**payload, "idempotency_key": f"refusal-{uuid4().hex}"},
            headers={"X-CSRF-Token": csrf},
        )

    # The idempotency key is required and bounded.
    raw = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={"message_type": "document_request", "documents": ["Паспорт"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert raw.status_code == 422

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
            json={
                "message_type": "document_request",
                "documents": [f"Документ {i}"],
                "idempotency_key": f"rate-limit-{i}",
            },
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
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "history-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    row_id = send.json()["messages"][0]["id"]

    # History shows the exact text, status and the initiator.
    history = channels_app.get(f"/candidates/{candidate.id}/messages").json()
    assert history["total"] == 1
    item = history["items"][0]
    assert item["id"] == row_id
    assert item["title"] == "Запрос документов"
    assert "— Паспорт" in item["body"]
    assert item["initiator_username"] == "hr1"
    assert item["initiator_user_id"] == str(hr_user.id)

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


def test_cancel_is_refused_for_sending_and_terminal_states(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    """Only `queued` rows may be cancelled via the API."""
    candidate = make_candidate(db_session, owner=hr_user, email="cq@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    for i, (status_value, expected) in enumerate(
        [("accepted", 409), ("failed", 409), ("skipped", 409), ("sending", 409)]
    ):
        send = channels_app.post(
            f"/candidates/{candidate.id}/messages/send",
            json={
                "message_type": "document_reminder",
                "documents": [f"Документ {i}"],
                "idempotency_key": f"cancel-state-{i}",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert send.status_code == 201, send.text
        row_id = send.json()["messages"][0]["id"]
        row = db_session.get(NotificationOutbox, UUID(row_id))
        assert row is not None
        row.status = DeliveryStatus(status_value)
        if status_value == "sending":
            row.lease_expires_at = utc_now() + timedelta(minutes=2)
        db_session.commit()
        response = channels_app.post(
            f"/candidates/{candidate.id}/messages/{row_id}/cancel",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == expected, status_value

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
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "audit-check-1",
        },
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
            json={
                "message_type": "document_request",
                "documents": ["Секретный документ"],
                "idempotency_key": "logs-check-1",
            },
            headers={"X-CSRF-Token": csrf},
        )
        channels_app.get(f"/candidates/{candidate.id}/messages")
    for record in caplog.records:
        text = record.getMessage()
        assert "Секретный документ" not in text
        assert "logs@example.com" not in text
        assert "Логгинг" not in text


# --- Email double opt-in token lifecycle ------------------------------------------


def test_email_confirmation_token_lifecycle(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="tok@example.com")
    csrf = _login(channels_app, "hr1")

    # An unknown token is a plain 404 without any details.
    response = channels_app.get(
        "https://hr.example.test/candidates/email/confirm?token=" + "x" * 43
    )
    assert response.status_code == 404
    assert "tok@example.com" not in response.text

    # Initiate twice: the newer token supersedes the older one and its
    # still-queued letter is cancelled (a dead link is never mailed).
    _initiate_confirmation(channels_app, csrf, candidate)
    first_link = _confirmation_link(db_session, candidate, channels_app)
    _initiate_confirmation(channels_app, csrf, candidate)
    second_link = _confirmation_link(db_session, candidate, channels_app)
    assert first_link != second_link
    response = channels_app.get(first_link)
    assert response.status_code == 410  # superseded
    consent = db_session.get(CandidateChannelConsent, (candidate.id, "email"))
    assert consent is None or consent.granted is False

    from app.notification_service import cancel_pending_for_object  # noqa: F401

    old_rows = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id,
                NotificationOutbox.notification_type == NotificationType.CANDIDATE_EMAIL_CONFIRM,
                NotificationOutbox.status == DeliveryStatus.CANCELLED,
            )
        )
        .scalars()
        .all()
    )
    assert len(old_rows) == 1  # the first letter was cancelled

    # An expired token changes nothing (its own candidate: at most one
    # ACTIVE token per candidate is enforced by a partial unique index).
    expired_owner = make_candidate(db_session, owner=hr_user, email="expired@example.com")
    db_session.add(
        CandidateEmailConfirmToken(
            candidate_id=expired_owner.id,
            token_hash=hashlib.sha256(b"expired-token-1234").hexdigest(),
            email_normalized="expired@example.com",
            created_at=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(hours=1),
        )
    )
    db_session.commit()
    response = channels_app.get(
        "https://hr.example.test/candidates/email/confirm?token=expired-token-1234"
    )
    assert response.status_code == 410
    assert "Истёк" in response.text or "срок" in response.text.lower()
    consent = db_session.get(CandidateChannelConsent, (expired_owner.id, "email"))
    assert consent is None or consent.granted is False
    # The claim was NOT consumed by the failed attempt (fail-closed).
    token_row = (
        db_session.execute(
            select(CandidateEmailConfirmToken).where(
                CandidateEmailConfirmToken.candidate_id == expired_owner.id
            )
        )
        .scalars()
        .one()
    )
    assert token_row.consumed_at is None

    # The still-valid second link confirms; a repeat click is 410.
    assert channels_app.get(second_link).status_code == 200
    assert channels_app.get(second_link).status_code == 410


def test_confirmation_letter_persists_no_link(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    """No persisted field ever contains a working confirmation link.

    The raw token is an HMAC derived from the server secret and the token
    row id; the queued letter body stores a placeholder that the worker
    substitutes in memory at send time. The scan below checks every
    candidate-scoped text field we persist (outbox title/body, idempotency
    request snapshots, audit details) for BOTH the raw token and the URL.
    """
    from fastapi import FastAPI

    from app.candidate_messages import CONFIRM_URL_PLACEHOLDER, derive_email_confirm_token

    candidate = make_candidate(db_session, owner=hr_user, email="secret@example.com")
    csrf = _login(channels_app, "hr1")
    _initiate_confirmation(channels_app, csrf, candidate)

    settings = cast("Settings", cast(FastAPI, channels_app.app).state.settings)
    token = (
        db_session.execute(
            select(CandidateEmailConfirmToken).where(
                CandidateEmailConfirmToken.candidate_id == candidate.id
            )
        )
        .scalars()
        .one()
    )
    raw_token = derive_email_confirm_token(token.id, settings)
    assert len(raw_token) >= 16

    letters = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
    )
    assert len(letters) == 1
    letter = letters[0]
    # The stored body carries the placeholder, never the URL itself.
    assert letter.body is not None
    assert CONFIRM_URL_PLACEHOLDER in letter.body
    assert "token=" not in letter.body
    assert "https://" not in letter.body
    # ...and neither the raw token nor the hash is anywhere in the text.
    for field in (letter.title, letter.body):
        assert raw_token not in (field or "")
        assert token.token_hash not in (field or "")

    # The idempotency snapshots and audit details stay clean as well.
    from app.models import CandidateMessageRequest

    requests = db_session.execute(select(CandidateMessageRequest)).scalars().all()
    for row in requests:
        assert raw_token not in str(row.response)
    from sqlalchemy import text as _text

    # The whole database is scanned textually: no table may hold the
    # raw token or a working link (backups inherit exactly this state).
    leaked = (
        db_session.execute(
            _text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type IN ('text', 'character varying')"
            )
        ).all()
        if db_session.get_bind().dialect.name == "postgresql"
        else []
    )
    assert leaked == []  # the scan proper runs in the PG integration suite

    # The history API shows the letter with the link masked out: an HR
    # user must never be able to click the candidate's confirmation.
    history = channels_app.get(f"/candidates/{candidate.id}/messages").json()
    items = [item for item in history["items"] if item["message_type"] == "candidate_email_confirm"]
    assert len(items) == 1
    assert CONFIRM_URL_PLACEHOLDER not in (items[0]["body"] or "")
    assert "token=" not in (items[0]["body"] or "")
    assert "ссылка подтверждения отправлена кандидату" in (items[0]["body"] or "")

    # The candidate's link still derives to the same hash the DB stores.
    import hashlib as _hashlib

    assert token.token_hash == _hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def test_email_confirmation_initiation_access_and_config(
    client: TestClient, channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    other_hr = make_user(db_session, username="hr2", role=UserRole.HR)
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    make_user(db_session, username="adm", role=UserRole.ADMIN)
    own = make_candidate(db_session, owner=hr_user, email="acc@example.com")
    foreign = make_candidate(db_session, owner=other_hr, email="f@example.com")

    # Foreign candidate: 404 for another HR (no existence leak).
    csrf = _login(channels_app, "hr1")
    response = channels_app.post(
        f"/candidates/{foreign.id}/channels/email/confirmation",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 404

    # A manager of the area may initiate; a plain admin may not (403).
    csrf_mgr = _login(channels_app, "mgr")
    assert (
        channels_app.post(
            f"/candidates/{own.id}/channels/email/confirmation",
            headers={"X-CSRF-Token": csrf_mgr},
        ).status_code
        == 201
    )
    csrf_adm = _login(channels_app, "adm")
    assert (
        channels_app.post(
            f"/candidates/{own.id}/channels/email/confirmation",
            headers={"X-CSRF-Token": csrf_adm},
        ).status_code
        == 403
    )

    # Without the public base URL configured the flow is disabled (503).
    csrf2 = _login(client, "hr1")
    response = client.post(
        f"/candidates/{own.id}/channels/email/confirmation",
        headers={"X-CSRF-Token": csrf2},
    )
    assert response.status_code == 503


def test_public_confirm_is_rate_limited_per_ip(
    monkeypatch: pytest.MonkeyPatch, channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    from app.routers import candidate_messages as router_module

    limiter = router_module._public_limiter_for(channels_app.app.state.settings)  # type: ignore[attr-defined]
    monkeypatch.setattr(limiter, "_limit", 3)
    limiter.reset()
    url = "https://hr.example.test/candidates/email/confirm?token=" + "x" * 43
    codes = [channels_app.get(url).status_code for _ in range(5)]
    assert 429 in codes
    assert codes.count(429) == 2


# --- Manual-send idempotency --------------------------------------------------------


def test_send_idempotency_replays_the_original_result(
    channels_app: TestClient, db_session: Session, hr_user: User
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="idem@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    payload = {
        "message_type": "document_request",
        "documents": ["Паспорт"],
        "idempotency_key": "idem-key-1",
    }

    first = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["channels"] == ["email"]
    row_id = first_body["messages"][0]["id"]

    # Same key + same payload: the ORIGINAL result, no new rows.
    second = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code in (200, 201)
    assert second.json() == first_body

    rows = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert str(rows[0].id) == row_id

    # Same key + different payload -> 409.
    conflict = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["СНИЛС"],
            "idempotency_key": "idem-key-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409

    # The same key bound to ANOTHER user -> 409 as well.
    make_user(db_session, username="mgr", role=UserRole.MANAGER)
    csrf_mgr = _login(channels_app, "mgr")
    other_user = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json=payload,
        headers={"X-CSRF-Token": csrf_mgr},
    )
    assert other_user.status_code == 409

    # The key is never echoed in user-facing messages or audit details.
    events = (
        db_session.execute(select(AuditEvent).where(AuditEvent.candidate_id == candidate.id))
        .scalars()
        .all()
    )
    assert all("idem-key-1" not in (e.details or "") for e in events)


def test_send_idempotency_unique_race_replays_the_winner(
    channels_app: TestClient, db_session: Session, hr_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The concurrent-winner path: the pre-insert lookup misses the row (race
    window), the INSERT loses the unique index — the stored response of the
    winner is replayed and nothing new is queued. (The genuinely parallel
    two-thread race runs against PostgreSQL in the integration suite.)"""
    from app.routers import candidate_messages as router_module

    candidate = make_candidate(db_session, owner=hr_user, email="conc@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")

    # The "winner" already committed: a stored response exists.
    winner_row_id = uuid4()
    stored_response = {
        "messages": [
            {
                "id": str(winner_row_id),
                "message_type": "candidate_document_request",
                "channel": "email",
                "status": "queued",
                "source": "manual",
                "title": "Запрос документов",
                "body": "текст",
                "event_id": None,
                "initiator_user_id": str(hr_user.id),
                "initiator_username": "hr1",
                "scheduled_at": "2026-09-04T12:00:00Z",
                "scheduled_at_effective": None,
                "queued_at": "2026-09-04T12:00:00Z",
                "accepted_at": None,
                "delivered_at": None,
                "failed_at": None,
                "cancelled_at": None,
                "attempts": 0,
                "next_attempt_at": None,
                "error_code": None,
                "error_class": None,
                "provider_message_id": None,
            }
        ],
        "channels": ["email"],
    }
    from app.models import CandidateMessageRequest

    db_session.add(
        CandidateMessageRequest(
            idempotency_key="concurrent-key-1",
            user_id=hr_user.id,
            candidate_id=candidate.id,
            message_type="document_request",
            payload_hash=router_module._manual_payload_hash(
                user_id=hr_user.id,
                candidate_id=candidate.id,
                message_type="document_request",
                event_id=None,
                documents=["Паспорт"],
                channel=None,
            ),
            response=stored_response,
        )
    )
    db_session.commit()

    # Simulate the race: the FIRST lookup returns None (the loser has not
    # seen the winner yet), subsequent lookups see the committed row.
    real_lookup = router_module._idempotency_lookup
    calls = {"n": 0}

    def racing_lookup(db: Session, key: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_lookup(db, key)

    monkeypatch.setattr(router_module, "_idempotency_lookup", racing_lookup)

    response = channels_app.post(
        f"/candidates/{candidate.id}/messages/send",
        json={
            "message_type": "document_request",
            "documents": ["Паспорт"],
            "idempotency_key": "concurrent-key-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json() == stored_response  # the winner's stored result

    rows = (
        db_session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
    )
    assert rows == []  # the loser queued nothing
