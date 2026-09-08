"""Unit tests for the candidate-communications service (phase 10).

Runs against the in-memory SQLite engine: template composition/sanitising,
token lifecycle, channel-state computation, transactional scheduling with
idempotency, cancellation rules and the stateless unsubscribe signature.
No network, no real contacts, no PII in failures.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.candidate_communications import (
    CANDIDATE_CONSENT_POLICY_VERSION,
    EMAIL_CHANNEL,
    TELEGRAM_CHANNEL,
    active_token,
    build_revoke_url,
    cancel_for_event,
    cancel_pending_candidate_messages,
    channel_state,
    compose_message_text,
    consume_token,
    existing_by_idempotency_key,
    issue_token,
    new_token,
    schedule_candidate_message,
    schedule_consent_invite,
    token_hash,
    verify_revoke_url,
)
from app.config import Settings
from app.models import (
    Candidate,
    CandidateChannelPurpose,
    CandidateChannelToken,
    CandidateContactChannel,
    CandidateMessage,
    CandidateMessageSource,
    CandidateMessageType,
    Event,
    EventStatus,
    EventType,
    UserRole,
)
from tests.conftest import make_candidate, make_event, make_user


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "test",
        "SECRET_KEY": "phase10-unit-test-secret",
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


NOW = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)


def _event(
    db: Session,
    *,
    starts_at: datetime | None = None,
    remind_at: datetime | None = None,
    status: EventStatus = EventStatus.SCHEDULED,
) -> Event:
    owner = make_user(db, username=f"owner-{uuid4().hex[:8]}", role=UserRole.HR)
    assignee = make_user(db, username=f"assignee-{uuid4().hex[:8]}", role=UserRole.HR)
    candidate = make_candidate(db, owner=owner, email="candidate@example.test")
    return make_event(
        db,
        candidate=candidate,
        author=owner,
        assignee=assignee,
        type_=EventType.INTERVIEW,
        title="Собеседование",
        starts_at=starts_at or (NOW + timedelta(days=1)),
        remind_at=remind_at,
        status=status,
    )


# --- Template composition -----------------------------------------------------


def test_compose_interview_scheduled_text() -> None:
    title, body = compose_message_text(
        CandidateMessageType.INTERVIEW_SCHEDULED,
        candidate_full_name="Иванов Иван",
        position="Python-разработчик",
        starts_at=NOW,
        location="Москва, ул. Ленина, 1",
        timezone="UTC",
    )
    assert title == "Собеседование назначено"
    assert "Иванов Иван" in body
    assert "Python-разработчик" in body
    assert "Ленина" in body
    assert body.count("\n\n") >= 1


def test_compose_all_interview_kinds_have_titles() -> None:
    cases: list[tuple[CandidateMessageType, str]] = [
        (CandidateMessageType.INTERVIEW_SCHEDULED, "Собеседование назначено"),
        (CandidateMessageType.INTERVIEW_REMINDER, "Напоминание о собеседовании"),
        (CandidateMessageType.INTERVIEW_RESCHEDULED, "Собеседование перенесено"),
        (CandidateMessageType.INTERVIEW_CANCELLED, "Собеседование отменено"),
        (CandidateMessageType.DOCUMENTS_REQUEST, "Запрос документов"),
        (CandidateMessageType.DOCUMENTS_REMINDER, "Напоминание о документах"),
    ]
    for kind, expected_title in cases:
        title, _body = compose_message_text(
            kind,
            candidate_full_name="Петров Пётр",
            starts_at=NOW + timedelta(days=1),
            documents=["Паспорт", "Диплом"],
            timezone="UTC",
        )
        assert title == expected_title, kind


def test_compose_documents_lists_every_item_on_own_line() -> None:
    _title, body = compose_message_text(
        CandidateMessageType.DOCUMENTS_REQUEST,
        candidate_full_name="Сидорова Анна",
        documents=["Паспорт", "Диплом об образовании"],
        timezone="UTC",
    )
    assert "• Паспорт" in body
    assert "• Диплом об образовании" in body


def test_compose_sanitises_user_content_no_line_injection() -> None:
    _title, body = compose_message_text(
        CandidateMessageType.DOCUMENTS_REQUEST,
        candidate_full_name="Иван\nПетров\r\n",
        documents=["Паспорт\nВторой документ"],
        timezone="UTC",
    )
    # Line breaks inside user-provided fields collapse into single lines.
    assert "Иван Петров" in body
    assert "\nПетров" not in body
    assert "• Паспорт Второй документ" in body
    assert body.count("Второй документ") == 1


def test_consent_invite_contains_one_shot_confirm_url() -> None:
    raw = new_token()
    title, body = compose_message_text(
        CandidateMessageType.CONSENT_INVITE,
        candidate_full_name="Иванова Мария",
        confirm_url=f"https://app.test/public/candidates/consent/{raw}",
        timezone="UTC",
    )
    assert title == "Подтвердите получение сообщений"
    assert raw in body


def test_revoke_footer_appears_only_when_url_given() -> None:
    _title, body = compose_message_text(
        CandidateMessageType.DOCUMENTS_REQUEST,
        candidate_full_name="Иван",
        documents=["Паспорт"],
        timezone="UTC",
    )
    assert "Отказаться от сообщений" not in body
    _title, body_with = compose_message_text(
        CandidateMessageType.DOCUMENTS_REQUEST,
        candidate_full_name="Иван",
        documents=["Паспорт"],
        revoke_url="https://app.test/public/candidates/revoke/abc/email?sig=xyz",
        timezone="UTC",
    )
    assert "Отказаться от сообщений" in body_with
    assert "sig=xyz" in body_with


# --- Tokens -------------------------------------------------------------------


def test_issue_active_supersede_and_consume(db_session: Session) -> None:
    owner = make_user(db_session, username="tokens-owner", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email="c@example.test")

    first = issue_token(
        db_session,
        candidate_id=candidate.id,
        channel=EMAIL_CHANNEL,
        purpose=CandidateChannelPurpose.EMAIL_CONSENT,
        ttl=timedelta(hours=72),
        now=NOW,
    )
    second = issue_token(
        db_session,
        candidate_id=candidate.id,
        channel=EMAIL_CHANNEL,
        purpose=CandidateChannelPurpose.EMAIL_CONSENT,
        ttl=timedelta(hours=72),
        now=NOW,
    )
    db_session.commit()
    db_session.refresh(first)
    assert first.consumed_at == NOW
    assert first.consume_reason == "superseded"

    active = active_token(
        db_session,
        candidate_id=candidate.id,
        channel=EMAIL_CHANNEL,
        purpose=CandidateChannelPurpose.EMAIL_CONSENT,
        now=NOW,
    )
    assert active is not None and active.id == second.id

    # Single-use atomic consume.
    assert consume_token(db_session, second, reason="confirmed", now=NOW) is True
    db_session.commit()
    assert (
        consume_token(db_session, second, reason="confirmed", now=NOW + timedelta(minutes=1))
        is False
    )
    db_session.commit()

    # Expired tokens are not returned as active (superseding only covers
    # unconsumed rows, and the time filter drops expired ones anyway).
    expired = issue_token(
        db_session,
        candidate_id=candidate.id,
        channel=EMAIL_CHANNEL,
        purpose=CandidateChannelPurpose.EMAIL_CONSENT,
        ttl=timedelta(hours=1),
        now=NOW - timedelta(hours=2),
    )
    db_session.commit()
    assert expired.consumed_at is None
    assert (
        active_token(
            db_session,
            candidate_id=candidate.id,
            channel=EMAIL_CHANNEL,
            purpose=CandidateChannelPurpose.EMAIL_CONSENT,
            now=NOW,
        )
        is None
    )


def test_raw_token_only_hash_is_stored(db_session: Session) -> None:
    raw = new_token()
    owner = make_user(db_session, username="hash-owner", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email="c@example.test")
    row = issue_token(
        db_session,
        candidate_id=candidate.id,
        channel=TELEGRAM_CHANNEL,
        purpose=CandidateChannelPurpose.TELEGRAM_LINK,
        ttl=timedelta(minutes=10),
        now=NOW,
        raw=raw,
    )
    db_session.commit()
    stored = db_session.get(CandidateChannelToken, row.id)
    assert stored is not None
    assert stored.token_hash == token_hash(raw)
    assert raw not in (stored.token_hash,)
    # The raw value appears nowhere in the row's serialisable columns.
    dumped = {c.name: getattr(stored, c.name) for c in stored.__table__.columns}
    assert raw not in [value for value in dumped.values() if isinstance(value, str)]


# --- Scheduling and idempotency ----------------------------------------------


def test_schedule_and_idempotent_duplicate(db_session: Session) -> None:
    owner = make_user(db_session, username="schedule-owner", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email="c@example.test")
    first, created = schedule_candidate_message(
        db_session,
        candidate=candidate,
        channel=EMAIL_CHANNEL,
        message_type=CandidateMessageType.DOCUMENTS_REQUEST,
        source=CandidateMessageSource.MANUAL,
        title="Запрос документов",
        body="Здравствуйте!",
        recipient_email="c@example.test",
        idempotency_key="manual:abc",
        now=NOW,
    )
    db_session.commit()
    assert created is True
    duplicate, created_again = schedule_candidate_message(
        db_session,
        candidate=candidate,
        channel=EMAIL_CHANNEL,
        message_type=CandidateMessageType.DOCUMENTS_REQUEST,
        source=CandidateMessageSource.MANUAL,
        title="Запрос документов",
        body="Здравствуйте!",
        recipient_email="c@example.test",
        idempotency_key="manual:abc",
        now=NOW,
    )
    db_session.commit()
    assert created_again is False
    assert duplicate.id == first.id
    assert existing_by_idempotency_key(db_session, "manual:abc") is not None
    rows = db_session.execute(select(CandidateMessage)).scalars().all()
    assert len(rows) == 1


def test_cancel_pending_only_and_returns_count(db_session: Session) -> None:
    owner = make_user(db_session, username="cancel-owner", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email="c@example.test")
    queued, _ = schedule_candidate_message(
        db_session,
        candidate=candidate,
        channel=EMAIL_CHANNEL,
        message_type=CandidateMessageType.DOCUMENTS_REQUEST,
        source=CandidateMessageSource.MANUAL,
        title="Запрос документов",
        body="Тело",
        recipient_email="c@example.test",
        idempotency_key="cancel-1",
        now=NOW,
    )
    sent, _ = schedule_candidate_message(
        db_session,
        candidate=candidate,
        channel=EMAIL_CHANNEL,
        message_type=CandidateMessageType.DOCUMENTS_REMINDER,
        source=CandidateMessageSource.MANUAL,
        title="Напоминание",
        body="Тело",
        recipient_email="c@example.test",
        idempotency_key="cancel-2",
        now=NOW,
    )
    sent.status = "accepted"
    sent.accepted_at = NOW
    db_session.commit()

    count = cancel_pending_candidate_messages(
        db_session, candidate_id=candidate.id, channel=EMAIL_CHANNEL, now=NOW + timedelta(minutes=1)
    )
    db_session.commit()
    assert count == 1
    db_session.refresh(queued)
    db_session.refresh(sent)
    assert queued.status == "cancelled"
    assert sent.status == "accepted"  # immutable delivered history untouched


def test_cancel_for_event(db_session: Session) -> None:
    event = _event(db_session)
    schedule_candidate_message(
        db_session,
        candidate=event.candidate,
        channel=EMAIL_CHANNEL,
        message_type=CandidateMessageType.INTERVIEW_REMINDER,
        source=CandidateMessageSource.EVENT,
        title="Напоминание",
        body="Тело",
        recipient_email=event.candidate.email,
        event_id=event.id,
        event_version=event.version,
        idempotency_key=f"ev-{event.id}",
        now=NOW,
    )
    db_session.commit()
    assert cancel_for_event(db_session, event_id=event.id, now=NOW + timedelta(minutes=1)) == 1
    db_session.commit()
    row = db_session.execute(select(CandidateMessage)).scalars().one()
    assert row.status == "cancelled"


# --- Consent invite -----------------------------------------------------------


def test_schedule_consent_invite_queues_message_and_token(db_session: Session) -> None:
    owner = make_user(db_session, username="invite-owner", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email="invite@example.test")
    message, token = schedule_consent_invite(
        db_session,
        candidate=candidate,
        base_url="https://app.test/",
        settings=_settings(),
        initiator_user_id=owner.id,
        now=NOW,
    )
    db_session.commit()
    assert message.message_type == CandidateMessageType.CONSENT_INVITE
    assert message.channel == EMAIL_CHANNEL
    assert message.source == CandidateMessageSource.SYSTEM
    assert message.recipient_email == "invite@example.test"
    assert message.quiet_hours_bypassed is True
    assert token.email_address == "invite@example.test"
    assert "public/candidates/consent/" in message.body
    # Content is immutable: no partial writes over an existing row.
    db_session.refresh(message)
    assert message.body.count("public/candidates/consent/") == 1


def _state(
    db: Session, candidate: Candidate, channel: str, settings: Settings, now: datetime
) -> str:
    return channel_state(db, candidate, channel, settings=settings, now=now)[0]


def test_channel_state_matrix(db_session: Session) -> None:
    settings = _settings()
    owner = make_user(db_session, username="state-owner", role=UserRole.HR)
    fresh = make_candidate(db_session, owner=owner, email="state@example.test")
    assert _state(db_session, fresh, EMAIL_CHANNEL, settings, NOW) == "not_connected"

    no_address = make_candidate(db_session, owner=owner, email=None)
    assert _state(db_session, no_address, EMAIL_CHANNEL, settings, NOW) == "not_connected"

    # Pending double-opt-in: an active consent token exists.
    issue_token(
        db_session,
        candidate_id=fresh.id,
        channel=EMAIL_CHANNEL,
        purpose=CandidateChannelPurpose.EMAIL_CONSENT,
        ttl=timedelta(hours=72),
        now=NOW,
    )
    db_session.commit()
    assert _state(db_session, fresh, EMAIL_CHANNEL, settings, NOW) == "pending_confirmation"

    # Allowed: consented binding + provider configured.
    row = CandidateContactChannel(
        candidate_id=fresh.id,
        channel=EMAIL_CHANNEL,
        email_address="state@example.test",
        consent_granted=True,
        consent_at=NOW,
        consent_source="candidate_email_link",
        consent_policy_version=CANDIDATE_CONSENT_POLICY_VERSION,
    )
    db_session.add(row)
    db_session.commit()
    assert _state(db_session, fresh, EMAIL_CHANNEL, settings, NOW) == "allowed"

    # Temporarily unavailable when the provider is not configured.
    disabled = _settings(SMTP_ENABLED="false")
    state, reason = channel_state(db_session, fresh, EMAIL_CHANNEL, settings=disabled, now=NOW)
    assert state == "temporarily_unavailable"
    assert reason == "channel_not_configured"

    # Denied after an explicit opt-out.
    row.consent_granted = False
    row.consent_at = None
    row.revoked_at = NOW
    row.revoke_reason = "hr_revoked"
    db_session.commit()
    assert _state(db_session, fresh, EMAIL_CHANNEL, settings, NOW) == "denied"

    # Telegram starts not_connected; a recent temp failure blocks sends.
    fresh_tg = make_candidate(db_session, owner=owner, email="tg@example.test")
    assert _state(db_session, fresh_tg, TELEGRAM_CHANNEL, settings, NOW) == "not_connected"
    bound = CandidateContactChannel(
        candidate_id=fresh_tg.id,
        channel=TELEGRAM_CHANNEL,
        chat_id=123456789,
        consent_granted=True,
        consent_at=NOW,
        consent_source="telegram_start",
        consent_policy_version=CANDIDATE_CONSENT_POLICY_VERSION,
    )
    db_session.add(bound)
    db_session.commit()
    assert _state(db_session, fresh_tg, TELEGRAM_CHANNEL, settings, NOW) == "allowed"
    bound.last_error_class = "telegram_timeout"
    bound.last_error_at = NOW
    db_session.commit()
    assert (
        _state(db_session, fresh_tg, TELEGRAM_CHANNEL, settings, NOW) == "temporarily_unavailable"
    )


# --- Stateless unsubscribe signature ------------------------------------------


def test_revoke_url_signature_roundtrip_and_tamper() -> None:
    candidate_id = uuid4()
    url = build_revoke_url(
        "https://app.test/", candidate_id=candidate_id, channel=EMAIL_CHANNEL, secret_key="s3cret"
    )
    assert url.startswith(
        f"https://app.test/public/candidates/revoke/{candidate_id}/{EMAIL_CHANNEL}?sig="
    )
    signature = url.split("sig=", 1)[1]
    assert verify_revoke_url(candidate_id, EMAIL_CHANNEL, signature, "s3cret") is True
    assert verify_revoke_url(candidate_id, TELEGRAM_CHANNEL, signature, "s3cret") is False
    assert verify_revoke_url(candidate_id, EMAIL_CHANNEL, "deadbeef", "s3cret") is False
    assert verify_revoke_url(candidate_id, EMAIL_CHANNEL, signature, "other-secret") is False


def test_schedule_requires_valid_mailbox(db_session: Session) -> None:
    owner = make_user(db_session, username="mail-owner", role=UserRole.HR)
    candidate = make_candidate(db_session, owner=owner, email=None)
    try:
        schedule_consent_invite(
            db_session,
            candidate=candidate,
            base_url="https://app.test/",
            settings=_settings(),
            initiator_user_id=owner.id,
            now=NOW,
        )
    except ValueError as exc:
        assert "адрес" in str(exc)
    else:  # pragma: no cover - the guard must trigger
        raise AssertionError("expected ValueError for a candidate without an email")
