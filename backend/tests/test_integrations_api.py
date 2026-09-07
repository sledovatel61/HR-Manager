"""Unit tests for the Telegram/SMTP integrations API (SQLite, fake Bot API)."""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import app.routers.integrations as integrations_module
from app.config import Settings
from app.main import create_app
from app.models import (
    AuditAction,
    AuditEvent,
    DeliveryChannel,
    NotificationOutbox,
    NotificationPreference,
    TelegramLink,
    TelegramLinkToken,
    UserEmail,
    UserRole,
)
from app.telegram import TelegramStartUpdate, TelegramUpdatesResult
from tests.conftest import FIXTURE_PASSWORD, make_user

BOT_TOKEN = "unit-test-bot-token-must-never-leak"
SMTP_PASSWORD = "unit-test-smtp-password-must-never-leak"


def _channel_settings(**overrides: str) -> Settings:
    values = {
        "APP_ENV": "test",
        "SECRET_KEY": "unit-test-secret-key",
        "DATABASE_URL": "sqlite+pysqlite://",
        "TELEGRAM_ENABLED": "true",
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "TELEGRAM_BOT_USERNAME": "hr_test_bot",
        "SMTP_ENABLED": "true",
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "587",
        "SMTP_ENCRYPTION": "starttls",
        "SMTP_USERNAME": "user",
        "SMTP_PASSWORD": SMTP_PASSWORD,
        "SMTP_FROM_ADDRESS": "noreply@example.test",
        "INTEGRATION_RATE_LIMIT": "1000",
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture()
def channel_client(unit_engine: Engine) -> Iterator[TestClient]:
    app = create_app(_channel_settings(), engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _raw_token_from_link(deep_link: str) -> str:
    query = parse_qs(urlparse(deep_link).query)
    return query["start"][0]


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# --- status -------------------------------------------------------------------


def test_status_defaults_nothing_configured_for_new_user(
    channel_client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(channel_client, "hr1")
    body = channel_client.get("/integrations/status").json()
    assert body["telegram"]["state"] == "not_configured"
    assert body["telegram"]["configured"] is True
    assert body["telegram"]["linked"] is False
    assert body["telegram"]["opt_in"] is False
    assert body["email"]["state"] == "not_configured"
    assert body["email"]["verified"] is False
    dumped = channel_client.get("/integrations/status").text
    assert BOT_TOKEN not in dumped
    assert SMTP_PASSWORD not in dumped


def test_status_disabled_channels_report_not_configured(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")
    body = client.get("/integrations/status").json()
    assert body["telegram"]["configured"] is False
    assert body["email"]["configured"] is False


# --- Telegram linking ----------------------------------------------------------


def test_link_code_entropy_hash_storage_and_supersede(
    channel_client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")

    first = channel_client.post("/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf})
    assert first.status_code == 201, first.text
    link1 = first.json()["deep_link"]
    assert link1.startswith("https://t.me/hr_test_bot?start=")
    raw1 = _raw_token_from_link(link1)
    assert len(raw1) >= 40  # ~256-bit entropy, urlsafe

    second = channel_client.post("/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf})
    raw2 = _raw_token_from_link(second.json()["deep_link"])
    assert raw1 != raw2

    tokens = db_session.execute(select(TelegramLinkToken)).scalars().all()
    assert len(tokens) == 2
    # Only hashes are stored.
    assert {t.token_hash for t in tokens} == {_token_hash(raw1), _token_hash(raw2)}
    assert all(len(t.token_hash) == 64 for t in tokens)
    superseded = next(t for t in tokens if t.token_hash == _token_hash(raw1))
    assert superseded.consumed_at is not None
    assert superseded.consume_reason == "superseded"
    active = next(t for t in tokens if t.token_hash == _token_hash(raw2))
    assert active.consumed_at is None
    # TTL respected.
    ttl = (active.expires_at - active.created_at).total_seconds()
    assert 14 * 60 < ttl <= 15 * 60 + 1


def test_link_code_requires_configuration(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    response = client.post("/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503


def test_confirm_happy_path_binds_numeric_chat_id(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    raw = _raw_token_from_link(
        channel_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
        ).json()["deep_link"]
    )

    def fake_poll(config, *, offset):  # type: ignore[no-untyped-def]
        return TelegramUpdatesResult(
            ok=True,
            updates=(TelegramStartUpdate(update_id=100, chat_id=555001122, token=raw),),
            max_update_id=100,
        )

    monkeypatch.setattr(integrations_module, "_poll_starts_impl", fake_poll)
    response = channel_client.post("/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is True

    link = db_session.get(TelegramLink, user.id)
    assert link is not None and link.chat_id == 555001122
    assert isinstance(link.chat_id, int)
    assert link.linked_at is not None and link.revoked_at is None
    token = db_session.execute(select(TelegramLinkToken)).scalar_one()
    assert token.consumed_at is not None and token.consume_reason == "linked"
    # Audit without chat id.
    events = db_session.execute(select(AuditEvent)).scalars().all()
    actions = {e.action for e in events}
    assert AuditAction.TELEGRAM_LINK_STARTED in actions
    assert AuditAction.TELEGRAM_LINK_CONFIRMED in actions
    assert all("555001122" not in (e.details or "") for e in events)


def test_confirm_without_start_keeps_token_active(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    channel_client.post("/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf})

    def fake_poll(config, *, offset):  # type: ignore[no-untyped-def]
        return TelegramUpdatesResult(ok=True, updates=(), max_update_id=7)

    monkeypatch.setattr(integrations_module, "_poll_starts_impl", fake_poll)
    response = channel_client.post("/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409
    token = db_session.execute(select(TelegramLinkToken)).scalar_one()
    assert token.consumed_at is None  # retry stays possible
    assert channel_client.get("/integrations/status").json()["telegram"]["state"] == "pending"


def test_confirm_is_idor_safe_between_users(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(db_session, username="alice", role=UserRole.HR)
    make_user(db_session, username="mallory", role=UserRole.HR)
    csrf_alice = _login(channel_client, "alice")
    raw = _raw_token_from_link(
        channel_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf_alice}
        ).json()["deep_link"]
    )
    csrf_mallory = _login(channel_client, "mallory")

    seen_tokens: list[str] = []

    def fake_poll(config, *, offset):  # type: ignore[no-untyped-def]
        # The bot inbox contains Alice's /start — Mallory must not steal it.
        return TelegramUpdatesResult(
            ok=True,
            updates=(TelegramStartUpdate(update_id=50, chat_id=999, token=raw),),
            max_update_id=50,
        )

    monkeypatch.setattr(integrations_module, "_poll_starts_impl", fake_poll)
    response = channel_client.post(
        "/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf_mallory}
    )
    # Mallory has no active token of her own: nothing to confirm.
    assert response.status_code == 409
    assert db_session.execute(select(TelegramLink)).scalars().all() == []
    token = db_session.execute(select(TelegramLinkToken)).scalar_one()
    assert token.consumed_at is None
    assert seen_tokens == []


def test_confirm_single_use_second_attempt_fails(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    raw = _raw_token_from_link(
        channel_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
        ).json()["deep_link"]
    )

    def fake_poll(config, *, offset):  # type: ignore[no-untyped-def]
        return TelegramUpdatesResult(
            ok=True,
            updates=(TelegramStartUpdate(update_id=60, chat_id=111, token=raw),),
            max_update_id=60,
        )

    monkeypatch.setattr(integrations_module, "_poll_starts_impl", fake_poll)
    assert (
        channel_client.post(
            "/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    # The token is consumed; a repeated confirm finds no active token.
    assert (
        channel_client.post(
            "/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 409
    )


def test_unlink_and_relink_flow(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")

    def poll_for(raw: str, chat: int, update_id: int):  # type: ignore[no-untyped-def]
        def fake_poll(config, *, offset):  # type: ignore[no-untyped-def]
            return TelegramUpdatesResult(
                ok=True,
                updates=(TelegramStartUpdate(update_id=update_id, chat_id=chat, token=raw),),
                max_update_id=update_id,
            )

        return fake_poll

    raw1 = _raw_token_from_link(
        channel_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
        ).json()["deep_link"]
    )
    monkeypatch.setattr(integrations_module, "_poll_starts_impl", poll_for(raw1, 111, 70))
    assert (
        channel_client.post(
            "/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    unlink = channel_client.post("/integrations/telegram/unlink", headers={"X-CSRF-Token": csrf})
    assert unlink.status_code == 200
    assert unlink.json()["state"] == "revoked"
    assert unlink.json()["linked"] is False
    link = db_session.get(TelegramLink, user.id)
    assert link is not None and link.revoked_at is not None

    raw2 = _raw_token_from_link(
        channel_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
        ).json()["deep_link"]
    )
    monkeypatch.setattr(integrations_module, "_poll_starts_impl", poll_for(raw2, 222, 71))
    assert (
        channel_client.post(
            "/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    db_session.refresh(link)
    assert link.chat_id == 222 and link.revoked_at is None
    events = db_session.execute(select(AuditEvent)).scalars().all()
    assert AuditAction.TELEGRAM_UNLINKED in {e.action for e in events}


def test_telegram_consent_opt_in_and_out(channel_client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    response = channel_client.put(
        "/integrations/telegram/consent",
        json={"opt_in": True, "consent_granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["opt_in"] is True
    assert response.json()["consent_granted"] is True
    assert response.json()["policy_version"] == "phase9-v1"
    preference = db_session.get(NotificationPreference, user.id)
    assert preference is not None and preference.telegram_opt_in is True
    assert preference.telegram_consent_granted is True
    assert preference.telegram_consent_source == "web-ui"
    assert preference.telegram_consent_at is not None
    assert "telegram" in preference.enabled_channels

    response = channel_client.put(
        "/integrations/telegram/consent",
        json={"opt_in": False, "consent_granted": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.json()["opt_in"] is False
    db_session.refresh(preference)
    assert preference.telegram_opt_in is False
    assert preference.telegram_consent_granted is False
    assert "telegram" not in preference.enabled_channels
    events = db_session.execute(select(AuditEvent)).scalars().all()
    assert sum(1 for e in events if e.action == AuditAction.TELEGRAM_CONSENT_UPDATED) == 2


@pytest.mark.parametrize("channel", ["telegram", "email"])
def test_consent_contract_rejects_missing_or_contradictory_grant(
    channel_client: TestClient, db_session: Session, channel: str
) -> None:
    """Fail-closed consent: activation needs opt_in=true AND an explicit
    consent_granted=true; anything else is a 422 with no state change."""
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    url = f"/integrations/{channel}/consent"

    # Missing grant field: pydantic 422, nothing stored.
    assert (
        channel_client.put(url, json={"opt_in": True}, headers={"X-CSRF-Token": csrf}).status_code
        == 422
    )
    # Contradictory grant: explicit 422, nothing stored.
    response = channel_client.put(
        url, json={"opt_in": True, "consent_granted": False}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 422
    assert "consent_granted" in response.json()["detail"]
    preference = db_session.get(NotificationPreference, user.id)
    assert preference is None or getattr(preference, f"{channel}_opt_in") is not True
    # No consent audit for rejected attempts.
    events = db_session.execute(select(AuditEvent)).scalars().all()
    assert not [e for e in events if e.action.value.endswith("consent_updated")]

    # A bare opt_in=true row without the grant (legacy/inconsistent state)
    # must NOT report the channel as active.
    row = preference or NotificationPreference(
        user_id=user.id,
        timezone="Europe/Moscow",
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        workdays=[1, 2, 3, 4, 5],
        enabled_types=["system_alert"],
        enabled_channels=["in_app", channel],
    )
    if preference is None:
        db_session.add(row)
    setattr(row, f"{channel}_opt_in", True)
    setattr(row, f"{channel}_consent_granted", False)
    db_session.commit()
    status = channel_client.get("/integrations/status").json()[channel]
    assert status["opt_in"] is False


@pytest.mark.parametrize("channel", ["telegram", "email"])
def test_consent_opt_out_and_reenable(
    channel_client: TestClient, db_session: Session, channel: str
) -> None:
    """Opt-out always revokes (even the grant); re-enabling needs a fresh
    explicit grant — a revoke never sends and never self-heals."""
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    url = f"/integrations/{channel}/consent"

    granted = channel_client.put(
        url, json={"opt_in": True, "consent_granted": True}, headers={"X-CSRF-Token": csrf}
    )
    assert granted.status_code == 200
    revoked = channel_client.put(
        url, json={"opt_in": False, "consent_granted": False}, headers={"X-CSRF-Token": csrf}
    )
    assert revoked.status_code == 200
    preference = db_session.get(NotificationPreference, user.id)
    assert preference is not None
    assert getattr(preference, f"{channel}_opt_in") is False
    assert getattr(preference, f"{channel}_consent_granted") is False
    assert getattr(preference, f"{channel}_consent_at") is not None
    assert getattr(preference, f"{channel}_consent_policy_version") == "phase9-v1"
    assert channel not in (preference.enabled_channels or [])
    assert channel_client.get("/integrations/status").json()[channel]["opt_in"] is False

    # Re-enable requires the full explicit pair again.
    assert (
        channel_client.put(
            url, json={"opt_in": True, "consent_granted": False}, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 422
    )
    again = channel_client.put(
        url, json={"opt_in": True, "consent_granted": True}, headers={"X-CSRF-Token": csrf}
    )
    assert again.status_code == 200
    assert channel_client.get("/integrations/status").json()[channel]["opt_in"] is True


def test_telegram_test_requires_link_then_queues(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    assert (
        channel_client.post(
            "/integrations/telegram/test", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 409
    )
    db_session.add(TelegramLink(user_id=user.id, chat_id=123, linked_at=datetime.now(UTC)))
    db_session.commit()
    response = channel_client.post("/integrations/telegram/test", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    row = db_session.get(NotificationOutbox, UUID(response.json()["outbox_id"]))
    assert row is not None and row.channel == DeliveryChannel.TELEGRAM
    assert row.template == "channel_test"
    assert row.recipient_user_id == user.id


# --- Email channel -------------------------------------------------------------


def _fixed_email_token(
    monkeypatch: pytest.MonkeyPatch, raw: str = "EMAIL-TOKEN-" + "x" * 32
) -> str:
    monkeypatch.setattr(integrations_module.secrets, "token_urlsafe", lambda n=32: raw)
    return raw


def test_email_set_queues_verification_without_leaking_address(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    raw = _fixed_email_token(monkeypatch)
    response = channel_client.put(
        "/integrations/email",
        json={"email": "Ivan.Petrov@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pending_email_masked"] == "I***@example.com"
    assert "Ivan.Petrov@example.com" not in response.text
    assert body["verification_queued"] is True

    stored = db_session.get(UserEmail, user.id)
    assert stored is not None
    assert stored.pending_email == "Ivan.Petrov@example.com"
    assert stored.verification_token_hash == _token_hash(raw)
    assert stored.email is None  # not usable before confirmation
    outbox = db_session.execute(select(NotificationOutbox)).scalars().all()
    assert len(outbox) == 1
    assert outbox[0].channel == DeliveryChannel.EMAIL
    assert outbox[0].external_recipient == "Ivan.Petrov@example.com"
    assert outbox[0].template == "email_verification"
    audit = db_session.execute(select(AuditEvent)).scalars().all()
    assert AuditAction.EMAIL_ADDRESS_SET in {e.action for e in audit}
    assert all("Ivan.Petrov@example.com" not in (e.details or "") for e in audit)


def test_email_set_rejects_injection_and_requires_config(
    channel_client: TestClient, client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    bad = channel_client.put(
        "/integrations/email",
        json={"email": "a@example.test\nBcc: evil@example.test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code in (422, 503)
    csrf_plain = _login(client, "hr1")
    disabled = client.put(
        "/integrations/email", json={"email": "a@example.com"}, headers={"X-CSRF-Token": csrf_plain}
    )
    assert disabled.status_code == 503


def test_email_confirm_happy_path_and_wrong_code(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    raw = _fixed_email_token(monkeypatch)
    channel_client.put(
        "/integrations/email", json={"email": "me@example.com"}, headers={"X-CSRF-Token": csrf}
    )
    wrong = channel_client.post(
        "/integrations/email/confirm",
        json={"token": "wrong-token-value-00000000000000"},
        headers={"X-CSRF-Token": csrf},
    )
    assert wrong.status_code == 404
    ok = channel_client.post(
        "/integrations/email/confirm", json={"token": raw}, headers={"X-CSRF-Token": csrf}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"verified": True, "address_masked": "m***@example.com"}
    stored = db_session.get(UserEmail, user.id)
    assert stored is not None and stored.email == "me@example.com"
    assert stored.verified_at is not None
    assert stored.pending_email is None and stored.verification_token_hash is None
    # The token is single-use.
    again = channel_client.post(
        "/integrations/email/confirm", json={"token": raw}, headers={"X-CSRF-Token": csrf}
    )
    assert again.status_code == 404
    assert channel_client.get("/integrations/status").json()["email"]["verified"] is True


def test_email_confirm_expired_and_attempts_budget(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    raw = _fixed_email_token(monkeypatch)
    channel_client.put(
        "/integrations/email", json={"email": "me@example.com"}, headers={"X-CSRF-Token": csrf}
    )
    stored = db_session.get(UserEmail, user.id)
    assert stored is not None
    stored.verification_expires_at = datetime(2000, 1, 1, tzinfo=UTC)
    db_session.commit()
    expired = channel_client.post(
        "/integrations/email/confirm", json={"token": raw}, headers={"X-CSRF-Token": csrf}
    )
    assert expired.status_code == 410

    stored.verification_expires_at = datetime(2030, 1, 1, tzinfo=UTC)
    stored.verification_attempts = 10
    db_session.commit()
    exhausted = channel_client.post(
        "/integrations/email/confirm",
        json={"token": "wrong-token-value-00000000000000"},
        headers={"X-CSRF-Token": csrf},
    )
    assert exhausted.status_code == 429


def test_email_remove_and_consent(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(channel_client, "hr1")
    raw = _fixed_email_token(monkeypatch)
    channel_client.put(
        "/integrations/email", json={"email": "me@example.com"}, headers={"X-CSRF-Token": csrf}
    )
    channel_client.post(
        "/integrations/email/confirm", json={"token": raw}, headers={"X-CSRF-Token": csrf}
    )
    consent = channel_client.put(
        "/integrations/email/consent",
        json={"opt_in": True, "consent_granted": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert consent.json()["opt_in"] is True
    assert channel_client.get("/integrations/status").json()["email"]["state"] == "works"
    removed = channel_client.delete("/integrations/email", headers={"X-CSRF-Token": csrf})
    assert removed.status_code == 204
    stored = db_session.get(UserEmail, user.id)
    assert stored is not None and stored.email is None
    assert channel_client.get("/integrations/status").json()["email"]["state"] == "not_configured"
    events = db_session.execute(select(AuditEvent)).scalars().all()
    assert AuditAction.EMAIL_REMOVED in {e.action for e in events}
    assert AuditAction.EMAIL_CONSENT_UPDATED in {e.action for e in events}


# --- Admin ---------------------------------------------------------------------


def test_admin_channels_state_without_secrets_and_hr_forbidden(
    channel_client: TestClient, db_session: Session
) -> None:
    make_user(db_session, username="admin1", role=UserRole.ADMIN)
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf_admin = _login(channel_client, "admin1")
    response = channel_client.get("/admin/integrations/channels")
    assert response.status_code == 200
    body = response.json()
    assert body["telegram"] == {
        "enabled": True,
        "configured": True,
        "bot_username": "hr_test_bot",
    }
    assert body["smtp"]["enabled"] is True
    assert body["smtp"]["host"] == "smtp.example.test"
    assert body["smtp"]["from_address"] == "noreply@example.test"
    assert BOT_TOKEN not in response.text
    assert SMTP_PASSWORD not in response.text

    _login(channel_client, "hr1")
    assert channel_client.get("/admin/integrations/channels").status_code == 403
    assert channel_client.post(
        "/admin/integrations/smtp/check", headers={"X-CSRF-Token": csrf_admin}
    ).status_code in (401, 403)


def test_admin_checks_and_test_send(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.smtp import SmtpCheckResult
    from app.telegram import TelegramCheckResult

    admin = make_user(db_session, username="admin1", role=UserRole.ADMIN)
    csrf = _login(channel_client, "admin1")
    monkeypatch.setattr(
        integrations_module, "_telegram_check_impl", lambda config: TelegramCheckResult(ok=True)
    )
    monkeypatch.setattr(
        integrations_module, "_smtp_check_impl", lambda config: SmtpCheckResult(ok=True)
    )
    tg = channel_client.post("/admin/integrations/telegram/check", headers={"X-CSRF-Token": csrf})
    assert tg.json()["ok"] is True
    smtp = channel_client.post("/admin/integrations/smtp/check", headers={"X-CSRF-Token": csrf})
    assert smtp.json()["ok"] is True

    # Test send needs the admin's own verified mailbox first.
    assert (
        channel_client.post(
            "/admin/integrations/smtp/test-send", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 409
    )
    db_session.add(
        UserEmail(user_id=admin.id, email="admin@example.com", verified_at=datetime.now(UTC))
    )
    db_session.commit()
    test = channel_client.post("/admin/integrations/smtp/test-send", headers={"X-CSRF-Token": csrf})
    assert test.status_code == 200, test.text
    row = db_session.get(NotificationOutbox, UUID(test.json()["outbox_id"]))
    assert row is not None and row.channel == DeliveryChannel.EMAIL
    assert row.recipient_user_id == admin.id
    events = db_session.execute(select(AuditEvent)).scalars().all()
    actions = {e.action for e in events}
    assert AuditAction.TELEGRAM_CHECKED in actions
    assert AuditAction.SMTP_CHECKED in actions
    assert AuditAction.SMTP_TEST_QUEUED in actions


# --- CSRF / rate limits ---------------------------------------------------------


def test_mutations_require_csrf(channel_client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(channel_client, "hr1")
    # No X-CSRF-Token header although the session cookie is present.
    assert channel_client.post("/integrations/telegram/link-code").status_code == 403
    assert (
        channel_client.put("/integrations/telegram/consent", json={"opt_in": True}).status_code
        == 403
    )


@pytest.fixture()
def limited_client(unit_engine: Engine) -> Iterator[TestClient]:
    app = create_app(_channel_settings(INTEGRATION_RATE_LIMIT="2"), engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


def test_link_code_rate_limit(limited_client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(limited_client, "hr1")
    assert (
        limited_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 201
    )
    assert (
        limited_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 201
    )
    third = limited_client.post("/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf})
    assert third.status_code == 429
    assert "Retry-After" in third.headers


def test_confirm_foreign_chat_conflict_is_fail_closed(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chat bound to another account can never be taken over: 409, the
    holder keeps the binding, the token stays active, and the conflict is
    audited without any chat identifier."""
    from app.models import TelegramStartEvent

    owner = make_user(db_session, username="owner", role=UserRole.HR)
    make_user(db_session, username="intruder", role=UserRole.HR)
    db_session.add(
        TelegramLink(
            user_id=owner.id,
            chat_id=555001,
            linked_at=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        )
    )
    db_session.commit()
    csrf = _login(channel_client, "intruder")
    raw = _raw_token_from_link(
        channel_client.post(
            "/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf}
        ).json()["deep_link"]
    )

    def fake_poll(config, *, offset):  # type: ignore[no-untyped-def]
        # The holder's chat pressed /start with the intruder's code.
        return TelegramUpdatesResult(
            ok=True,
            updates=(TelegramStartUpdate(update_id=71, chat_id=555001, token=raw),),
            max_update_id=71,
        )

    monkeypatch.setattr(integrations_module, "_poll_starts_impl", fake_poll)
    response = channel_client.post("/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409, response.text
    assert "555001" not in response.text  # the foreign chat id never leaks

    db_session.expire_all()
    by_chat = (
        db_session.execute(select(TelegramLink).where(TelegramLink.chat_id == 555001))
        .scalars()
        .all()
    )
    assert [link.user_id for link in by_chat] == [owner.id]  # holder unchanged
    token = db_session.execute(select(TelegramLinkToken)).scalar_one()
    assert token.consumed_at is None  # retry stays possible after unlink
    assert db_session.execute(select(TelegramStartEvent)).scalars().all() == []
    conflict = (
        db_session.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.TELEGRAM_LINK_CONFLICT)
        )
        .scalars()
        .all()
    )
    assert len(conflict) == 1
    assert "555001" not in (conflict[0].details or "")


def test_confirm_inactive_user_cannot_confirm(
    channel_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deactivated account is rejected with 401 on the confirm path even
    with a live session and a live token (dependency gates the handler)."""
    user = make_user(db_session, username="leaver", role=UserRole.HR)
    csrf = _login(channel_client, "leaver")
    channel_client.post("/integrations/telegram/link-code", headers={"X-CSRF-Token": csrf})

    def fake_poll(config, *, offset):  # type: ignore[no-untyped-def]
        raise AssertionError("poll must not run for an inactive user")

    monkeypatch.setattr(integrations_module, "_poll_starts_impl", fake_poll)
    user.is_active = False
    db_session.commit()
    response = channel_client.post("/integrations/telegram/confirm", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 401
    assert db_session.execute(select(TelegramLink)).scalars().all() == []
    token = db_session.execute(select(TelegramLinkToken)).scalar_one()
    assert token.consumed_at is None
