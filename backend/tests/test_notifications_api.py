"""Unit tests for the notification center API (SQLite, RBAC, no PII)."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    User,
    UserRole,
)
from app.notification_service import deliver_in_app, schedule, schedule_notification_row
from tests.conftest import FIXTURE_PASSWORD, make_user

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _make_notification(
    db: Session, username: str, title: str, *, read: bool = False
) -> Notification:
    existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    user = existing if existing is not None else make_user(db, username=username, role=UserRole.HR)
    row = schedule_notification_row(
        db,
        type_=NotificationType.SYSTEM_ALERT,
        recipient_user_id=user.id,
        title=title,
        source=NotificationSource.SYSTEM,
        dedupe_key=f"test:{username}:{title}",
        scheduled_at=NOW,
    )
    assert row is not None
    db.commit()
    deliver_in_app(db, row, now=NOW)
    # Finalize like the worker's success path (delivered + attempt history).
    row.status = DeliveryStatus.DELIVERED
    row.delivered_at = NOW
    row.attempts += 1
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=row.attempts,
            started_at=NOW,
            finished_at=NOW,
            outcome="delivered",
        )
    )
    db.commit()
    notification = db.execute(select(Notification)).scalars().all()[-1]
    if read:
        notification.read_at = NOW
        db.commit()
    return notification


def test_list_own_notifications_with_unread_count(client: TestClient, db_session: Session) -> None:
    first = _make_notification(db_session, "hr1", "Первое")
    _make_notification(db_session, "hr2", "Чужое")
    _login(client, "hr1")

    response = client.get("/notifications")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["unread_count"] == 1
    assert body["items"][0]["id"] == str(first.id)
    assert body["items"][0]["title"] == "Первое"
    # Foreign rows never leak.
    assert all(item["title"] != "Чужое" for item in body["items"])


def test_unread_only_and_pagination(client: TestClient, db_session: Session) -> None:
    _make_notification(db_session, "hr1", "Непрочитанное")
    _make_notification(db_session, "hr1", "Прочитанное", read=True)
    _login(client, "hr1")

    body = client.get("/notifications", params={"unread_only": "true"}).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Непрочитанное"
    assert body["unread_count"] == 1

    assert client.get("/notifications", params={"limit": "0"}).status_code == 422
    assert client.get("/notifications", params={"limit": "101"}).status_code == 422


def test_unread_count_endpoint(client: TestClient, db_session: Session) -> None:
    _make_notification(db_session, "hr1", "A")
    _make_notification(db_session, "hr1", "B")
    _make_notification(db_session, "hr1", "C", read=True)
    _login(client, "hr1")
    assert client.get("/notifications/unread-count").json() == {"count": 2}


def test_mark_read_and_dismiss_own_only(client: TestClient, db_session: Session) -> None:
    mine = _make_notification(db_session, "hr1", "Моё")
    foreign = _make_notification(db_session, "hr2", "Чужое")
    csrf = _login(client, "hr1")

    # Bulk with a foreign id: the foreign row is NOT affected, no error.
    response = client.post(
        "/notifications/mark-read",
        json={"ids": [str(mine.id), str(foreign.id)]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 204
    db_session.refresh(mine)
    db_session.refresh(foreign)
    assert mine.read_at is not None
    assert foreign.read_at is None

    response = client.post(
        "/notifications/dismiss",
        json={"ids": [str(mine.id)]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 204
    db_session.refresh(mine)
    assert mine.dismissed_at is not None


def test_mark_all_read(client: TestClient, db_session: Session) -> None:
    _make_notification(db_session, "hr1", "A")
    _make_notification(db_session, "hr1", "B")
    csrf = _login(client, "hr1")
    response = client.post("/notifications/mark-all-read", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert client.get("/notifications/unread-count").json() == {"count": 0}


def test_bulk_limit_enforced(client: TestClient, db_session: Session) -> None:
    _make_notification(db_session, "hr1", "base")
    csrf = _login(client, "hr1")
    ids = ["11111111-1111-4111-8111-111111111111"] * 101
    response = client.post(
        "/notifications/mark-read", json={"ids": ids}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 422


def test_foreign_notification_is_404(client: TestClient, db_session: Session) -> None:
    foreign = _make_notification(db_session, "hr2", "Чужое")
    _make_notification(db_session, "hr1", "base")
    _login(client, "hr1")
    assert client.get(f"/notifications/{foreign.id}").status_code == 404
    assert client.get(f"/notifications/{foreign.id}/resolve").status_code == 404
    assert client.get(f"/notifications/{foreign.id}/delivery").status_code == 404


def test_resolve_rechecks_candidate_access(client: TestClient, db_session: Session) -> None:
    from app.models import Candidate, CandidateSource, CandidateStage
    from app.utils import utc_now

    owner = make_user(db_session, username="owner", role=UserRole.HR)
    other = make_user(db_session, username="other", role=UserRole.HR)
    candidate = Candidate(
        full_name="Кандидат",
        full_name_normalized="кандидат",
        source=CandidateSource.REFERRAL,
        position="Dev",
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=0,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db_session.add(candidate)
    db_session.commit()

    row = schedule_notification_row(
        db_session,
        type_=NotificationType.CANDIDATE_TRANSFERRED,
        recipient_user_id=owner.id,
        object_type="candidate",
        object_id=candidate.id,
        dedupe_key="resolve-test",
        scheduled_at=NOW,
    )
    assert row is not None
    db_session.commit()
    deliver_in_app(db_session, row, now=NOW)
    db_session.commit()
    notification = db_session.execute(select(Notification)).scalars().all()[-1]

    # Owner: allowed.
    _login(client, "owner")
    body = client.get(f"/notifications/{notification.id}/resolve").json()
    assert body == {"allowed": True, "object_type": "candidate", "object_id": str(candidate.id)}

    # A foreign HR: the same notification id is 404 for them (no leak)…
    _login(client, "other")
    assert client.get(f"/notifications/{notification.id}/resolve").status_code == 404

    # …and access is re-checked when the owner loses the candidate.
    candidate.owner_user_id = other.id
    db_session.commit()
    _login(client, "owner")
    body = client.get(f"/notifications/{notification.id}/resolve").json()
    assert body["allowed"] is False


def test_delivery_history_own_only(client: TestClient, db_session: Session) -> None:
    notification = _make_notification(db_session, "hr1", "Моё")
    _login(client, "hr1")
    body = client.get(f"/notifications/{notification.id}/delivery").json()
    assert body["channel"] == "in_app"
    assert body["status"] == "delivered"  # delivered in this fixture path
    assert body["attempts"] >= 1
    assert len(body["attempts_history"]) >= 1

    deliveries = client.get("/notifications/deliveries").json()
    assert deliveries["total"] == 1
    assert "title" not in str(deliveries).lower() or "notification_type" in str(deliveries)


def _make_external_row(
    db: Session, username: str, *, recipient_none: bool = False
) -> NotificationOutbox:
    from uuid import uuid4

    user = make_user(db, username=username, role=UserRole.HR)
    if recipient_none:
        # An unowned row (the verification-letter shape): built directly —
        # schedule() only issues external rows for the email verification
        # letter, and this fixture needs an unowned row for the 404 path.
        row = NotificationOutbox(
            recipient_user_id=None,
            external_recipient="pending@example.com",
            channel=DeliveryChannel.EMAIL,
            notification_type=NotificationType.SYSTEM_ALERT,
            source=NotificationSource.SYSTEM,
            title="Внешнее",
            template="email_verification",
            template_version=1,
            status=DeliveryStatus.QUEUED,
            queued_at=NOW,
            scheduled_at=NOW,
            idempotency_key=f"system_alert:ext:{uuid4().hex}",
        )
        db.add(row)
        db.flush()
    else:
        scheduled = schedule(
            db,
            recipient_user_id=user.id,
            channel=DeliveryChannel.TELEGRAM,
            type_=NotificationType.SYSTEM_ALERT,
            title="Внешнее",
            dedupe_key=f"ext:{uuid4().hex}",
            scheduled_at=NOW,
        )
        assert scheduled is not None
        row = scheduled
    row.status = DeliveryStatus.ACCEPTED
    row.accepted_at = NOW
    row.provider_message_id = "4242"
    row.attempts = 2
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=1,
            started_at=NOW,
            finished_at=NOW,
            outcome="failed",
            error_code="telegram_429",
            error_class="telegram_rate_limited",
        )
    )
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=2,
            started_at=NOW,
            finished_at=NOW,
            outcome="accepted",
            provider_message_id="4242",
        )
    )
    db.commit()
    assert isinstance(row, NotificationOutbox)
    return row


def test_delivery_detail_external_row_with_attempts(
    client: TestClient, db_session: Session
) -> None:
    row = _make_external_row(db_session, "hr1")
    _login(client, "hr1")
    response = client.get(f"/notifications/deliveries/{row.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["channel"] == "telegram"
    assert body["status"] == "accepted"
    assert body["provider_message_id"] == "4242"
    assert body["delivered_at"] is None  # accepted is never delivered/read
    assert len(body["attempts_history"]) == 2
    assert body["attempts_history"][0]["error_class"] == "telegram_rate_limited"
    assert body["attempts_history"][1]["provider_message_id"] == "4242"


def test_delivery_detail_foreign_and_unowned_are_404(
    client: TestClient, db_session: Session
) -> None:
    row = _make_external_row(db_session, "hr1")
    make_user(db_session, username="hr2", role=UserRole.HR)
    _login(client, "hr2")
    assert client.get(f"/notifications/deliveries/{row.id}").status_code == 404
    assert client.get("/notifications/deliveries/not-a-uuid").status_code == 404

    unowned = _make_external_row(db_session, "hr3", recipient_none=True)
    _login(client, "hr1")
    assert client.get(f"/notifications/deliveries/{unowned.id}").status_code == 404


def test_deliveries_list_includes_external_provider_ids(
    client: TestClient, db_session: Session
) -> None:
    row = _make_external_row(db_session, "hr1")
    _login(client, "hr1")
    body = client.get("/notifications/deliveries").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(row.id)
    assert body["items"][0]["channel"] == "telegram"
    assert body["items"][0]["provider_message_id"] == "4242"
