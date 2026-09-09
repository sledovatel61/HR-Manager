"""Phase 11 API, worker, privacy and constructor contracts (SQLite units)."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.document_rules import scan_document_rules
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AnalyticsFact,
    AnalyticsFactType,
    Candidate,
    CandidateDocumentItem,
    DeliveryStatus,
    DocumentRuleExecution,
    NotificationOutbox,
    User,
    UserRole,
)
from app.utils import utc_now
from app.worker import claim_batch, process_external_row, process_row
from tests.conftest import make_candidate, make_user
from tests.test_candidate_messages import _allow_email, _login
from tests.test_candidate_messages import channels_app as _channels_app
from tests.test_worker_candidate import _settings

channels_app = _channels_app

CONTENT: dict[str, Any] = {
    "name": "Приём на работу",
    "description": "Обязательные документы",
    "items": [
        {"key": "passport", "name": "Паспорт", "required": True},
        {"key": "diploma", "name": "Диплом", "required": True},
        {"key": "extra", "name": "Дополнительная справка", "required": False},
    ],
}


def setup_documents(client: TestClient, db: Session) -> tuple[User, Candidate, dict, dict, dict]:
    admin = make_user(db, username="doc-admin", role=UserRole.ADMIN)
    hr = make_user(db, username="doc-hr")
    candidate = make_candidate(db, owner=hr, email="documents@example.com")
    csrf = _login(client, admin.username)
    headers = {"X-CSRF-Token": csrf}
    created = client.post("/document-lists", json=CONTENT, headers=headers)
    assert created.status_code == 201, created.text
    parent = created.json()
    v = parent["versions"][0]
    published = client.post(
        f"/document-lists/{parent['id']}/versions/{v['id']}/publish",
        json={"expected_version": parent["version"]},
        headers=headers,
    )
    assert published.status_code == 200, published.text
    parent = published.json()
    csrf = _login(client, hr.username)
    headers = {"X-CSRF-Token": csrf}
    applied = client.post(
        f"/candidates/{candidate.id}/documents",
        json={"version_id": v["id"], "expected_revision": 0},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    return hr, candidate, parent, applied.json(), headers


def test_exact_snapshot_conflict_and_missing(channels_app: TestClient, db_session: Session) -> None:
    hr, candidate, parent, snapshot, headers = setup_documents(channels_app, db_session)
    assert snapshot["missing_required"] == ["passport", "diploma"]
    old_version = snapshot["exact_version"]
    payload = {"set_id": snapshot["set_id"], "expected_version": 1, "state": "received"}
    url = f"/candidates/{candidate.id}/documents/passport"
    assert channels_app.patch(url, json=payload, headers=headers).json()["missing_required"] == [
        "diploma"
    ]
    assert channels_app.patch(url, json=payload, headers=headers).status_code == 409
    assert channels_app.patch(url, json=payload).status_code == 403
    headers = {"X-CSRF-Token": _login(channels_app, "doc-admin")}
    changed = channels_app.post(
        f"/document-lists/{parent['id']}/versions",
        json={**CONTENT, "name": "Новая версия", "expected_version": parent["version"]},
        headers=headers,
    ).json()
    version = changed["versions"][0]
    assert (
        channels_app.post(
            f"/document-lists/{parent['id']}/versions/{version['id']}/publish",
            json={"expected_version": changed["version"]},
            headers=headers,
        ).status_code
        == 200
    )
    _login(channels_app, hr.username)
    current = channels_app.get(f"/candidates/{candidate.id}/documents").json()
    assert current["exact_version"] == old_version
    assert current["items"][0]["state"] == "received"
    assert (
        channels_app.post(
            f"/candidates/{candidate.id}/documents",
            json={"version_id": version["id"], "expected_revision": 0},
            headers={"X-CSRF-Token": _login(channels_app, hr.username)},
        ).status_code
        == 409
    )


def test_access_grants_idor_deleted_and_list_admin(
    channels_app: TestClient, db_session: Session
) -> None:
    _, candidate, _, snapshot, headers = setup_documents(channels_app, db_session)
    assert channels_app.post("/document-lists", json=CONTENT, headers=headers).status_code == 403
    assert not channels_app.get("/document-lists").json()["can_manage"]
    manager = make_user(db_session, username="doc-manager", role=UserRole.MANAGER)
    _login(channels_app, manager.username)
    url = f"/candidates/{candidate.id}/documents"
    assert channels_app.get(url).status_code == 404
    admin_headers = {"X-CSRF-Token": _login(channels_app, "doc-admin")}
    grant = channels_app.post(
        "/admin/access-grants",
        json={"user_id": str(manager.id), "scope": "candidate_documents_all"},
        headers=admin_headers,
    )
    assert grant.status_code == 200, grant.text
    _login(channels_app, manager.username)
    assert channels_app.get(url).json()["set_id"] == snapshot["set_id"]
    db_session.add(AccessGrant(user_id=manager.id, scope=AccessGrantScope.DOCUMENT_LISTS_MANAGE))
    db_session.commit()
    assert channels_app.get("/document-lists").json()["can_manage"]
    candidate.deleted_at = utc_now()
    db_session.commit()
    assert channels_app.get(url).status_code == 404


@pytest.mark.parametrize(
    "field,value",
    [
        ("trigger", "eval"),
        ("action", "sql"),
        ("channel", "sms"),
        ("days", True),
        ("days", 1.5),
        ("days", 31),
        ("days", 0),
        ("template", "Hello"),
        ("url", "https://example.com"),
        ("stage", "unknown"),
    ],
)
def test_closed_rule_params(
    channels_app: TestClient, db_session: Session, field: str, value: Any
) -> None:
    _, _, parent, _, headers = setup_documents(channels_app, db_session)
    params = {
        "trigger": "scheduled_reminder",
        "action": "document_reminder",
        "stage": "new",
        "list_id": parent["id"],
        "channel": "email",
        "days": 1,
        field: value,
    }
    response = channels_app.post(
        "/document-rules", json={"name": "Правило", "params": params}, headers=headers
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("kind", ["document_request", "document_reminder"])
@pytest.mark.parametrize("channel", ["email", "telegram"])
def test_manual_server_snapshot_and_send_revalidation(
    channels_app: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    channel: str,
) -> None:
    import app.worker as worker
    from app.models import CandidateChannelConsent, CandidateTelegramLink
    from app.smtp import SmtpSendResult
    from app.telegram import TelegramSendResult

    _, candidate, _, snapshot, headers = setup_documents(channels_app, db_session)
    _allow_email(db_session, candidate)
    db_session.add(
        CandidateTelegramLink(candidate_id=candidate.id, chat_id=123456, linked_at=utc_now())
    )
    db_session.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="telegram",
            granted=True,
            source="telegram_start",
            policy_version="phase10-v1",
        )
    )
    db_session.commit()
    sent = []

    def email(*args: Any, **kwargs: Any) -> SmtpSendResult:
        sent.append(kwargs)
        return SmtpSendResult(outcome="accepted", provider_message_id="smtp-real")

    def telegram(*args: Any, **kwargs: Any) -> TelegramSendResult:
        sent.append(kwargs)
        return TelegramSendResult(outcome="accepted", provider_message_id="42")

    monkeypatch.setattr(worker, "_send_email", email)
    monkeypatch.setattr(worker, "_send_telegram", telegram)
    payload = {
        "message_type": kind,
        "channel": channel,
        "document_set_id": snapshot["set_id"],
        "idempotency_key": str(uuid4()),
    }
    url = f"/candidates/{candidate.id}/messages/send"
    first = channels_app.post(url, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    assert "Дополнительная справка" not in first.json()["messages"][0]["body"]
    assert channels_app.post(url, json=payload, headers=headers).json() == first.json()
    assert (
        channels_app.post(
            url, json={**payload, "idempotency_key": str(uuid4())}, headers=headers
        ).json()["messages"][0]["id"]
        == first.json()["messages"][0]["id"]
    )
    row = db_session.get(NotificationOutbox, UUID(first.json()["messages"][0]["id"]))
    assert row
    row.status = DeliveryStatus.SENDING
    row.lease_expires_at = utc_now() + timedelta(minutes=2)
    db_session.commit()
    item = db_session.get(CandidateDocumentItem, (UUID(snapshot["set_id"]), "passport"))
    assert item
    item.state = "received"
    db_session.commit()
    assert (
        process_external_row(db_session, row.id, settings=_settings(), now=utc_now()) == "skipped"
    )
    assert sent == []
    # The original exact text has NOT been rewritten to a smaller list.
    db_session.refresh(row)
    assert row.body == first.json()["messages"][0]["body"]


def test_rules_execute_disable_and_history(channels_app: TestClient, db_session: Session) -> None:
    hr, candidate, parent, _snapshot, headers = setup_documents(channels_app, db_session)
    _allow_email(db_session, candidate)
    params = {
        "trigger": "stage_transition",
        "action": "document_request",
        "stage": "new",
        "list_id": parent["id"],
        "channel": "email",
    }
    response = channels_app.post(
        "/document-rules", json={"name": "Запрос", "params": params}, headers=headers
    )
    assert response.status_code == 201, response.text
    rule = response.json()
    fact = AnalyticsFact(
        candidate_id=candidate.id,
        owner_user_id=hr.id,
        fact_type=AnalyticsFactType.STAGE_CHANGED,
        stage_from="offer",
        stage_to="new",
        fact_at=utc_now(),
    )
    db_session.add(fact)
    db_session.commit()
    settings = _settings(NOTIFICATION_DEFAULT_TIMEZONE="UTC")
    now = utc_now().replace(hour=12)
    assert scan_document_rules(db_session, settings=settings, now=now, batch_size=20) == 1
    assert scan_document_rules(db_session, settings=settings, now=now, batch_size=20) == 0
    # Direct claim at a future permitted working-day noon.
    now = utc_now() + timedelta(days=1)
    now = now.replace(hour=12, minute=0)
    while now.weekday() > 4:
        now += timedelta(days=1)
    jobs = claim_batch(db_session, now=now, batch_size=20, lease_seconds=120)
    assert len(jobs) == 1
    assert process_row(db_session, jobs[0], settings=settings, now=now) == "delivered"
    execution = db_session.scalar(select(DocumentRuleExecution))
    assert execution and execution.outcome == "queued"
    history = channels_app.get(f"/document-rules/{rule['id']}/history").json()
    assert len(history) == 1 and history[0]["rule_version"] == 1
    update = {
        "name": rule["name"],
        "enabled": False,
        "params": rule["params"],
        "expected_version": rule["version"],
    }
    assert (
        channels_app.put(f"/document-rules/{rule['id']}", json=update, headers=headers).status_code
        == 200
    )
    assert (
        channels_app.put(f"/document-rules/{rule['id']}", json=update, headers=headers).status_code
        == 409
    )
    assert channels_app.get(f"/document-rules/{rule['id']}/history").json() == history
    messages = db_session.scalars(
        select(NotificationOutbox).where(NotificationOutbox.recipient_candidate_id == candidate.id)
    ).all()
    assert len(messages) == 1
    db_session.refresh(messages[0])
    assert messages[0].status == DeliveryStatus.CANCELLED
    assert channels_app.delete(f"/document-rules/{rule['id']}", headers=headers).status_code == 405


def test_scheduled_reminder_and_rights_loss(channels_app: TestClient, db_session: Session) -> None:
    hr, candidate, parent, _snapshot, headers = setup_documents(channels_app, db_session)
    params = {
        "trigger": "scheduled_reminder",
        "action": "document_reminder",
        "stage": "new",
        "list_id": parent["id"],
        "channel": "email",
        "days": 2,
    }
    response = channels_app.post(
        "/document-rules", json={"name": "Напоминание", "params": params}, headers=headers
    )
    assert response.status_code == 201, response.text
    settings = _settings()
    assert scan_document_rules(db_session, settings=settings, now=utc_now(), batch_size=10) == 0
    now = utc_now() + timedelta(days=3)
    assert scan_document_rules(db_session, settings=settings, now=now, batch_size=10) == 1
    hr.is_active = False
    db_session.commit()
    job = db_session.scalar(
        select(NotificationOutbox).where(NotificationOutbox.object_type == "document_rule_job")
    )
    assert job
    job.status = DeliveryStatus.SENDING
    job.lease_expires_at = now + timedelta(minutes=2)
    db_session.commit()
    from app.document_rules import perform_job

    perform_job(db_session, job, settings=settings, now=now)
    result = db_session.scalar(select(DocumentRuleExecution))
    assert result and result.outcome == "skipped"
    assert not db_session.scalars(
        select(NotificationOutbox).where(NotificationOutbox.recipient_candidate_id == candidate.id)
    ).all()


def test_rule_job_retry_lease_cancel_wins(
    channels_app: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.document_rules as rules
    from app.worker import recover_stale_leases

    _, _, parent, _, headers = setup_documents(channels_app, db_session)
    params = {
        "trigger": "scheduled_reminder",
        "action": "document_reminder",
        "stage": "new",
        "list_id": parent["id"],
        "channel": "email",
        "days": 1,
    }
    rule = channels_app.post(
        "/document-rules", json={"name": "Повторы", "params": params}, headers=headers
    ).json()
    now = utc_now() + timedelta(days=3)
    now = now.replace(hour=12, minute=0)
    while now.weekday() > 4:
        now += timedelta(days=1)
    settings = _settings(NOTIFICATION_DEFAULT_TIMEZONE="UTC")
    rules.scan_document_rules(db_session, settings=settings, now=now, batch_size=20)
    jobs = claim_batch(db_session, now=now, batch_size=20, lease_seconds=120)
    assert len(jobs) == 1

    def broken(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("not logged: body or PII")

    monkeypatch.setattr(rules, "perform_job", broken)
    assert process_row(db_session, jobs[0], settings=settings, now=now) == "queued"
    db_session.refresh(jobs[0])
    assert jobs[0].attempts == 1
    jobs[0].status = DeliveryStatus.SENDING
    jobs[0].lease_expires_at = now - timedelta(seconds=1)
    db_session.commit()
    assert recover_stale_leases(db_session, now=now, lease_seconds=120, max_attempts=3) == 1
    db_session.refresh(jobs[0])
    assert jobs[0].attempts == 2
    update = {
        "name": rule["name"],
        "params": rule["params"],
        "enabled": False,
        "expected_version": 1,
    }
    assert (
        channels_app.put(f"/document-rules/{rule['id']}", json=update, headers=headers).status_code
        == 200
    )
    assert process_row(db_session, jobs[0], settings=settings, now=now) == "cancelled"
    rows = db_session.scalars(select(DocumentRuleExecution)).all()
    assert len(rows) == 1 and rows[0].outcome == "cancelled"


def test_rule_quiet_hours_cannot_be_bypassed(channels_app: TestClient, db_session: Session) -> None:
    from datetime import UTC, datetime

    from app.models import NotificationPreference

    hr, _, parent, _, headers = setup_documents(channels_app, db_session)
    db_session.add(
        NotificationPreference(
            user_id=hr.id,
            timezone="Europe/Amsterdam",
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            workdays=[1, 2, 3, 4, 5],
            enabled_types=[],
            enabled_channels=["in_app"],
        )
    )
    db_session.commit()
    rule = channels_app.post(
        "/document-rules",
        json={
            "name": "Тихие часы",
            "params": {
                "trigger": "scheduled_reminder",
                "action": "document_reminder",
                "stage": "new",
                "list_id": parent["id"],
                "channel": "email",
                "days": 1,
            },
        },
        headers=headers,
    )
    assert rule.status_code == 201, rule.text
    # Saturday during autumn DST change weekend. Next working day is Monday.
    now = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    settings = _settings()
    assert scan_document_rules(db_session, settings=settings, now=now, batch_size=20) == 1
    jobs = claim_batch(db_session, now=now, batch_size=20, lease_seconds=120)
    assert len(jobs) == 1
    jobs[0].quiet_hours_bypassed = True
    db_session.commit()
    assert process_row(db_session, jobs[0], settings=settings, now=now) == "queued"
    db_session.refresh(jobs[0])
    assert jobs[0].scheduled_at_effective == datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


def test_openapi_and_closed_content(channels_app: TestClient, db_session: Session) -> None:
    _, _, parent, _, headers = setup_documents(channels_app, db_session)
    schema = channels_app.get("/openapi.json").json()["components"]["schemas"]
    assert schema["RuleParams"]["additionalProperties"] is False
    assert schema["RuleParams"]["properties"]["trigger"]["enum"] == [
        "stage_transition",
        "scheduled_reminder",
    ]
    assert "expected_version" in schema["ItemUpdate"]["required"]
    headers = {"X-CSRF-Token": _login(channels_app, "doc-admin")}
    for items in (
        [{"key": "a", "name": " "}],
        [{"key": "a", "name": "<script>"}],
        [{"key": "a", "name": "Паспорт"}, {"key": "a", "name": "Диплом"}],
    ):
        response = channels_app.post(
            "/document-lists", json={**CONTENT, "items": items}, headers=headers
        )
        assert response.status_code == 422
    v = parent["versions"][0]
    assert (
        channels_app.post(
            f"/document-lists/{parent['id']}/versions/{v['id']}/publish",
            json={"expected_version": parent["version"]},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        channels_app.delete(
            f"/document-lists/{parent['id']}/versions/{v['id']}", headers=headers
        ).status_code
        == 404
    )


def test_cached_owner_role_is_not_an_authorization_snapshot(
    channels_app: TestClient, db_session: Session, unit_engine: Any
) -> None:
    from app.documents import can_access

    hr, candidate, _, _, _ = setup_documents(channels_app, db_session)
    assert can_access(db_session, hr, candidate)
    # Simulates a role change while this request waits for a candidate lock.
    with Session(unit_engine) as changed:
        owner = changed.get(User, hr.id)
        assert owner
        owner.role = UserRole.ADMIN
        changed.commit()
    assert hr.role == UserRole.HR  # deliberately cached instance
    assert not can_access(db_session, hr, candidate)  # fresh grant/role required
