"""Unit tests for notification preferences (defaults, validation, audit)."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditAction, AuditEvent, UserRole
from tests.conftest import FIXTURE_PASSWORD, make_user


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "timezone": "Europe/Moscow",
        "quiet_hours_start": "21:00",
        "quiet_hours_end": "08:00",
        "workdays": [1, 2, 3, 4, 5],
        "enabled_types": ["event_assigned", "reminder_due"],
        "enabled_channels": ["in_app"],
    }
    payload.update(overrides)
    return payload


def test_defaults_until_initialized(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")
    body = client.get("/notification-preferences").json()
    assert body["initialized"] is False
    assert body["timezone"] == "Europe/Moscow"
    assert body["quiet_hours_start"] == "21:00"
    assert body["quiet_hours_end"] == "08:00"
    assert body["workdays"] == [1, 2, 3, 4, 5]
    assert "in_app" in body["enabled_channels"]


def test_put_and_get_roundtrip(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    response = client.put(
        "/notification-preferences",
        json=_payload(timezone="Asia/Yekaterinburg", quiet_hours_end="09:00"),
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["initialized"] is True
    assert body["timezone"] == "Asia/Yekaterinburg"

    saved = client.get("/notification-preferences").json()
    assert saved == body

    # The save is audited.
    audit = (
        db_session.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.PREFERENCE_UPDATED)
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1


def test_validation(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    csrf = _login(client, "hr1")
    for bad in (
        _payload(timezone="Mars/Olympus"),
        _payload(quiet_hours_start="25:00"),
        _payload(quiet_hours_end="9:00"),
        _payload(workdays=[0, 1]),
        _payload(workdays=[1, 1]),
        _payload(workdays=[]),
        _payload(enabled_types=["unknown_type"]),
        _payload(enabled_channels=["pigeon"]),
    ):
        response = client.put("/notification-preferences", json=bad, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 422, bad


def test_timezones_endpoint(client: TestClient, db_session: Session) -> None:
    make_user(db_session, username="hr1", role=UserRole.HR)
    _login(client, "hr1")
    body = client.get("/notification-preferences/timezones").json()
    assert "Europe/Moscow" in body["timezones"]
    assert len(body["timezones"]) > 10
