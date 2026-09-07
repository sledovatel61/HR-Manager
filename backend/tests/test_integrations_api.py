"""API tests for Telegram and Email integrations (Phase 9)."""

from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import NotificationPreference, UserRole
from app.smtp_adapter import SmtpSendResult
from app.telegram_adapter import TelegramSendResult, generate_link_token
from tests.conftest import FIXTURE_PASSWORD, make_user


def _login(client: TestClient, username: str, password: str = FIXTURE_PASSWORD) -> httpx.Response:
    return client.post("/auth/login", json={"username": username, "password": password})


def _csrf(response: httpx.Response) -> str:
    return response.json()["csrf_token"]


def test_integrations_status_shapes_and_no_secrets(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr_status_user", role=UserRole.HR)
    _login(client, "hr_status_user")
    status_resp = client.get("/integrations/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert "telegram" in data
    assert "email" in data
    assert "in_app" in data
    assert data["in_app"]["status"] == "working"
    assert "password" not in str(data)
    assert "token" not in str(data)


def test_telegram_link_initiate_and_confirm(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr_tg_flow", role=UserRole.HR)
    login_resp = _login(client, "hr_tg_flow")
    csrf = _csrf(login_resp)

    with patch("app.telegram_adapter.get_me", return_value={"username": "TestHrBot"}):
        initiate_resp = client.post(
            "/integrations/telegram/link/initiate",
            headers={"X-CSRF-Token": csrf},
        )
    # If bot token is empty by default in test settings, let's test with patched settings or token
    if initiate_resp.status_code == 400:
        client.app.state.settings.telegram_bot_token = "123456:BOT_TOKEN"
        client.app.state.settings.telegram_bot_username = "TestHrBot"
        initiate_resp = client.post(
            "/integrations/telegram/link/initiate",
            headers={"X-CSRF-Token": csrf},
        )

    assert initiate_resp.status_code == 200
    init_data = initiate_resp.json()
    token = init_data["token"]
    assert init_data["bot_username"] == "TestHrBot"
    assert "start=" in init_data["deep_link"]

    # Confirm
    with patch("app.routers.integrations.telegram_send_message"):
        confirm_resp = client.post(
            "/integrations/telegram/link/confirm",
            json={"token": token, "chat_id": 99887766, "username": "tg_flow_user"},
            headers={"X-CSRF-Token": csrf},
        )
    assert confirm_resp.status_code == 200
    c_data = confirm_resp.json()
    assert c_data["status"] == "working"
    assert c_data["linked"] is True

    # Check status endpoint
    status_resp = client.get("/integrations/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["telegram"]["linked"] is True


def test_telegram_link_confirm_idor_rejected(client: TestClient, db_session: Session) -> None:
    user1 = make_user(db_session, username="user_one", role=UserRole.HR)
    make_user(db_session, username="user_two", role=UserRole.HR)

    raw_token, _ = generate_link_token(db_session, user_id=user1.id, ttl_minutes=15)
    db_session.commit()

    # User 2 logs in and tries to confirm User 1's token
    login_resp = _login(client, "user_two")
    csrf = _csrf(login_resp)

    confirm_resp = client.post(
        "/integrations/telegram/link/confirm",
        json={"token": raw_token, "chat_id": 55555},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirm_resp.status_code == 403
    assert "другой учётной записи" in confirm_resp.json()["detail"]


def test_telegram_unlink(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr_unlink_api", role=UserRole.HR)
    raw_token, _ = generate_link_token(db_session, user_id=hr.id, ttl_minutes=15)
    client.app.state.settings.telegram_bot_token = "123456:BOT_TOKEN"

    login_resp = _login(client, "hr_unlink_api")
    csrf = _csrf(login_resp)

    with patch("app.routers.integrations.telegram_send_message"):
        client.post(
            "/integrations/telegram/link/confirm",
            json={"token": raw_token, "chat_id": 77777},
            headers={"X-CSRF-Token": csrf},
        )

    # Unlink
    unlink_resp = client.post(
        "/integrations/telegram/unlink",
        headers={"X-CSRF-Token": csrf},
    )
    assert unlink_resp.status_code == 200

    # Status is now pending_confirmation or not linked
    status_resp = client.get("/integrations/status")
    assert status_resp.json()["telegram"]["linked"] is False


def test_telegram_webhook_linking(client: TestClient, db_session: Session) -> None:
    hr = make_user(db_session, username="hr_webhook_user", role=UserRole.HR)
    raw_token, _ = generate_link_token(db_session, user_id=hr.id, ttl_minutes=15)
    db_session.commit()

    client.app.state.settings.telegram_bot_token = "123456:BOT_TOKEN"
    update_payload = {
        "update_id": 10001,
        "message": {
            "message_id": 1,
            "from": {"id": 88888, "is_bot": False, "first_name": "Anna", "username": "anna_tg"},
            "chat": {"id": 88888, "type": "private"},
            "date": 1700000000,
            "text": f"/start {raw_token}",
        },
    }

    with patch("app.routers.integrations.telegram_send_message"):
        resp = client.post("/integrations/telegram/webhook", json=update_payload)
        assert resp.status_code == 200

    pref = db_session.get(NotificationPreference, hr.id)
    assert pref is not None
    assert pref.telegram_chat_id == 88888
    assert pref.telegram_username == "anna_tg"
    assert pref.telegram_opt_in is True


def test_admin_telegram_test_connection_rbac(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="regular_hr", role=UserRole.HR)
    make_user(db_session, username="admin_probe", role=UserRole.ADMIN)

    # Regular HR is forbidden
    hr_login = _login(client, "regular_hr")
    hr_csrf = _csrf(hr_login)
    res_hr = client.post(
        "/admin/integrations/telegram/test-connection",
        headers={"X-CSRF-Token": hr_csrf},
    )
    assert res_hr.status_code == 403

    # Admin is allowed
    admin_login = _login(client, "admin_probe")
    admin_csrf = _csrf(admin_login)
    client.app.state.settings.telegram_bot_token = "123456:BOT_TOKEN"

    with patch(
        "app.routers.integrations.telegram_get_me",
        return_value={"id": 123456, "username": "ProbeBot", "first_name": "Probe"},
    ):
        res_admin = client.post(
            "/admin/integrations/telegram/test-connection",
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert res_admin.status_code == 200
        data = res_admin.json()
        assert data["ok"] is True
        assert data["bot_username"] == "ProbeBot"
        assert "token" not in str(data)


def test_admin_smtp_test_connection_rbac(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr_smtp_rbac", role=UserRole.HR)
    make_user(db_session, username="admin_smtp_probe", role=UserRole.ADMIN)

    # Regular HR is forbidden
    hr_login = _login(client, "hr_smtp_rbac")
    hr_csrf = _csrf(hr_login)
    res_hr = client.post(
        "/admin/integrations/smtp/test-connection",
        headers={"X-CSRF-Token": hr_csrf},
    )
    assert res_hr.status_code == 403

    # Admin is allowed
    admin_login = _login(client, "admin_smtp_probe")
    admin_csrf = _csrf(admin_login)
    client.app.state.settings.smtp_host = "smtp.mail.com"

    with patch(
        "app.routers.integrations.smtp_verify_connection",
        return_value={
            "ok": True,
            "host": "smtp.mail.com",
            "port": 587,
            "use_tls": False,
            "use_starttls": True,
            "authenticated": True,
        },
    ):
        res_admin = client.post(
            "/admin/integrations/smtp/test-connection",
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert res_admin.status_code == 200
        data = res_admin.json()
        assert data["ok"] is True
        assert data["host"] == "smtp.mail.com"
        assert "password" not in str(data)


def test_admin_test_send_telegram_and_email(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="admin_sender", role=UserRole.ADMIN)
    admin_login = _login(client, "admin_sender")
    admin_csrf = _csrf(admin_login)

    # 1. Telegram test send
    with patch(
        "app.routers.integrations.telegram_send_message",
        return_value=TelegramSendResult(accepted=True, provider_message_id="msg-999"),
    ):
        tg_res = client.post(
            "/admin/integrations/test-send",
            json={"channel": "telegram", "recipient": "12345678"},
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert tg_res.status_code == 200
        assert tg_res.json()["ok"] is True
        assert tg_res.json()["provider_message_id"] == "msg-999"

    # 2. Email test send
    with patch(
        "app.routers.integrations.smtp_send_email",
        return_value=SmtpSendResult(accepted=True, provider_message_id="<msg-888@test>"),
    ):
        em_res = client.post(
            "/admin/integrations/test-send",
            json={"channel": "email", "recipient": "test@example.com", "subject": "Test Subj"},
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert em_res.status_code == 200
        assert em_res.json()["ok"] is True
        assert em_res.json()["provider_message_id"] == "<msg-888@test>"
