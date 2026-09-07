"""Unit tests for Telegram account linking, token entropy, expiry and unlinking (Phase 9)."""

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import NotificationPreference, UserRole
from app.telegram_adapter import (
    confirm_link_token,
    generate_link_token,
    hash_link_token,
    unlink_telegram,
)
from app.utils import utc_now
from tests.conftest import make_user


def test_generate_link_token_entropy_and_hashing(db_session: Session) -> None:
    user = make_user(db_session, username="hr_link_test", role=UserRole.HR)
    raw_token, token_row = generate_link_token(db_session, user_id=user.id, ttl_minutes=15)
    db_session.commit()

    assert len(raw_token) >= 40  # 32 bytes base64url is 43 chars
    assert raw_token != token_row.token_hash
    assert token_row.token_hash == hash_link_token(raw_token)
    assert token_row.used_at is None
    assert token_row.expires_at > utc_now()


def test_confirm_link_token_success(db_session: Session) -> None:
    user = make_user(db_session, username="hr_link_2", role=UserRole.HR)
    raw_token, _token_row = generate_link_token(db_session, user_id=user.id, ttl_minutes=15)
    db_session.commit()

    confirmed_user, confirmed_token = confirm_link_token(
        db_session,
        token=raw_token,
        chat_id=123456789012,
        username="tg_user_1",
    )
    db_session.commit()

    assert confirmed_user.id == user.id
    assert confirmed_token.used_at is not None

    pref = db_session.get(NotificationPreference, user.id)
    assert pref is not None
    assert pref.telegram_chat_id == 123456789012
    assert pref.telegram_username == "tg_user_1"
    assert pref.telegram_opt_in is True
    assert pref.telegram_consent_at is not None
    assert pref.telegram_consent_source == "link_token"
    assert "telegram" in pref.enabled_channels


def test_confirm_link_token_single_use_protection(db_session: Session) -> None:
    user = make_user(db_session, username="hr_single_use", role=UserRole.HR)
    raw_token, _ = generate_link_token(db_session, user_id=user.id, ttl_minutes=15)
    db_session.commit()

    # First use succeeds
    confirm_link_token(db_session, token=raw_token, chat_id=11111)
    db_session.commit()

    # Second use must fail
    with pytest.raises(ValueError, match="уже был использован"):
        confirm_link_token(db_session, token=raw_token, chat_id=11111)


def test_confirm_link_token_expired_protection(db_session: Session) -> None:
    user = make_user(db_session, username="hr_expired", role=UserRole.HR)
    t_past = utc_now() - timedelta(minutes=20)
    raw_token, _ = generate_link_token(db_session, user_id=user.id, ttl_minutes=15, now=t_past)
    db_session.commit()

    with pytest.raises(ValueError, match="истёк"):
        confirm_link_token(db_session, token=raw_token, chat_id=22222, now=utc_now())


def test_confirm_link_token_invalid_token(db_session: Session) -> None:
    with pytest.raises(ValueError, match="Недействительный или несуществующий"):
        confirm_link_token(db_session, token="non_existent_token_12345", chat_id=33333)


def test_unlink_telegram_clears_fields_and_revokes_consent(db_session: Session) -> None:
    user = make_user(db_session, username="hr_unlink", role=UserRole.HR)
    raw_token, _ = generate_link_token(db_session, user_id=user.id, ttl_minutes=15)
    confirm_link_token(db_session, token=raw_token, chat_id=44444, username="unlink_me")
    db_session.commit()

    # Unlink
    unlinked = unlink_telegram(db_session, user_id=user.id)
    db_session.commit()
    assert unlinked is True

    pref = db_session.get(NotificationPreference, user.id)
    assert pref is not None
    assert pref.telegram_chat_id is None
    assert pref.telegram_username is None
    assert pref.telegram_opt_in is False
    assert pref.telegram_consent_at is None
    assert "telegram" not in pref.enabled_channels

    # Unlinking again returns False
    assert unlink_telegram(db_session, user_id=user.id) is False
