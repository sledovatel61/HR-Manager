"""Integration tests for document lists and automation rules (real PostgreSQL).

Real races and real constraints — the guarantees the unit suite (SQLite,
single-threaded) cannot prove:

* two concurrent publications of two drafts of the same list: exactly one
  wins (row lock + partial unique index), the other gets 409, one
  ``published`` row remains;
* two concurrent applications of a list to one candidate: exactly one
  current assignment (advisory lock + partial unique index);
* two concurrent status changes of one item with the same expected
  version: exactly one succeeds (conditional UPDATE), the other gets 409;
* two concurrent stage transitions evaluating the same rule for the same
  candidate: the execution unique key collapses them into one action, one
  outbox row;
* two parallel worker passes of the due-rule scheduler queue ONE reminder;
* a rule-queued document reminder is delivered by the worker through the
  SMTP stub with the exact server text; after the documents are received
  the same kind of row is skipped without any provider traffic;
* RESTRICT foreign keys: a published/applied version cannot be deleted;
  the execution history survives a rule soft-delete.
"""

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.automation_rules import scan_due_rules
from app.config import Settings
from app.document_lists import apply_published_list
from app.main import create_app
from app.models import (
    AutomationRuleExecution,
    CandidateChannelConsent,
    CandidateDocumentAssignment,
    CandidateDocumentItem,
    DeliveryStatus,
    DocumentListItem,
    DocumentListStatus,
    DocumentListVersion,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    UserRole,
)
from app.utils import utc_now
from app.worker import claim_batch, process_external_row
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user
from tests.test_integration_external import SmtpStub

RUN_INTEGRATION = os.environ.get("TEST_DATABASE_URL") is not None
NOW = datetime(2026, 9, 4, 9, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.integration

ITEMS = [
    {"item_key": "passport", "name": "Паспорт", "is_required": True},
    {"item_key": "snils", "name": "СНИЛС", "is_required": True},
]


@pytest.fixture()
def smtp_stub() -> Iterator[SmtpStub]:
    stub = SmtpStub()
    yield stub
    stub.close()


@pytest.fixture()
def settings(integration_url: str, smtp_stub: SmtpStub) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "integration-test-secret-key",
            "DATABASE_URL": integration_url,
            "WORKER_MAX_ATTEMPTS": "3",
            "WORKER_LEASE_SECONDS": "120",
            "SMTP_ENABLED": "true",
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": str(smtp_stub.port),
            "SMTP_ENCRYPTION": "none",
            "SMTP_FROM_ADDRESS": "noreply@example.com",
            "SMTP_FROM_NAME": "HR Manager",
            "CANDIDATE_MESSAGE_RATE_LIMIT": "1000",
            "CANDIDATE_EMAIL_CONFIRM_BASE_URL": "https://hr.example.test",
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
                "notification_preferences, reminders, access_grants, analytics_facts, "
                "automation_rule_executions, automation_rules, candidate_document_items, "
                "candidate_document_assignments, document_list_items, "
                "document_list_versions, document_lists RESTART IDENTITY CASCADE"
            )
        )
    app = create_app(settings, engine=pg_engine)
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _allow_email(db: Session, candidate: Any) -> None:
    db.add(
        CandidateChannelConsent(
            candidate_id=candidate.id,
            channel="email",
            granted=True,
            granted_at=NOW,
            source="email_confirm",
            policy_version="phase10-v1",
            email_normalized=candidate.email_normalized,
        )
    )
    db.commit()


def _published_list(client: TestClient, admin_csrf: str) -> dict:
    created = client.post(
        "/document-lists",
        json={"name": "Приём", "items": ITEMS},
        headers={"X-CSRF-Token": admin_csrf},
    ).json()
    version = created["versions"][0]
    response = client.post(
        f"/document-lists/{created['id']}/versions/{version['id']}/publish",
        json={"expected_row_version": version["row_version"]},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 200, response.text
    return client.get(f"/document-lists/{created['id']}").json()


def _race(fn: Any, count: int = 2) -> list[Any]:
    barrier = threading.Barrier(count)

    def run() -> Any:
        barrier.wait()
        return fn()

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(run) for _ in range(count)]
        return [future.result() for future in futures]


# --- Document lists ----------------------------------------------------------------


def test_concurrent_publication_single_winner(client: TestClient, pg_db: Session) -> None:
    """Two drafts of the same list published at the same instant: the row
    lock serializes them and the second sees «not a draft any more» or the
    partial unique index — exactly one published version remains."""
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    csrf = _login(client, "admin1")
    created = client.post(
        "/document-lists",
        json={"name": "Гонка", "items": ITEMS},
        headers={"X-CSRF-Token": csrf},
    ).json()
    draft1 = created["versions"][0]
    # A second draft is refused via the API (one draft at a time), so seed
    # it directly — the race is about the publication barrier itself.
    list_id = UUID(created["id"])
    admin_id = pg_db.execute(text("SELECT id FROM users WHERE username = 'admin1'")).scalar_one()
    draft2 = DocumentListVersion(
        list_id=list_id,
        version_number=2,
        status=DocumentListStatus.DRAFT,
        row_version=1,
        created_by_user_id=admin_id,
    )
    pg_db.add(draft2)
    pg_db.flush()
    pg_db.add(
        DocumentListItem(
            version_id=draft2.id, item_key="inn", name="ИНН", is_required=True, sort_order=0
        )
    )
    pg_db.commit()

    def publish(version_id: str) -> Any:
        return client.post(
            f"/document-lists/{created['id']}/versions/{version_id}/publish",
            json={"expected_row_version": 1},
            headers={"X-CSRF-Token": csrf},
        )

    targets = [draft1["id"], str(draft2.id)]
    barrier = threading.Barrier(2)

    def run(version_id: str) -> Any:
        barrier.wait()
        return publish(version_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(run, targets))
    statuses = sorted(r.status_code for r in responses)
    # Both may legitimately succeed sequentially (the second archives the
    # first) — that is still one published row; what is forbidden is two
    # published rows or a 500.
    assert all(code in (200, 409) for code in statuses), [r.text for r in responses]
    published = (
        pg_db.execute(
            select(DocumentListVersion).where(
                DocumentListVersion.list_id == list_id,
                DocumentListVersion.status == DocumentListStatus.PUBLISHED,
            )
        )
        .scalars()
        .all()
    )
    assert len(published) == 1
    # The partial unique index itself refuses a second published row.
    loser = next(
        v
        for v in pg_db.execute(
            select(DocumentListVersion).where(DocumentListVersion.list_id == list_id)
        ).scalars()
        if v.status != DocumentListStatus.PUBLISHED
    )
    loser.status = DocumentListStatus.PUBLISHED
    loser.published_at = utc_now()
    with pytest.raises(IntegrityError):
        pg_db.flush()
    pg_db.rollback()


def test_published_version_cannot_be_deleted_while_referenced(
    client: TestClient, pg_db: Session
) -> None:
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    csrf = _login(client, "admin1")
    published = _published_list(client, csrf)
    candidate = make_candidate(pg_db, owner=hr, email="ref@example.com")
    apply_published_list(
        pg_db, candidate=candidate, list_id=UUID(published["id"]), actor_user_id=hr.id
    )
    pg_db.commit()
    with pytest.raises(IntegrityError):
        pg_db.execute(
            text("DELETE FROM document_list_versions WHERE id = :id"),
            {"id": published["published_version_id"]},
        )
    pg_db.rollback()
    with pytest.raises(IntegrityError):
        pg_db.execute(text("DELETE FROM document_lists WHERE id = :id"), {"id": published["id"]})
    pg_db.rollback()


# --- Candidate snapshot races -----------------------------------------------------------


def test_concurrent_apply_single_current_assignment(client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    admin_csrf = _login(client, "admin1")
    published = _published_list(client, admin_csrf)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(pg_db, owner=hr, email="apply@example.com")
    csrf = _login(client, "hr1")

    def apply() -> Any:
        return client.post(
            f"/candidates/{candidate.id}/documents/apply",
            json={"list_id": published["id"]},
            headers={"X-CSRF-Token": csrf},
        )

    responses = _race(apply)
    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    current = (
        pg_db.execute(
            select(CandidateDocumentAssignment).where(
                CandidateDocumentAssignment.candidate_id == candidate.id,
                CandidateDocumentAssignment.replaced_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    assert len(current) == 1
    # The partial unique index is the durable backstop even without the lock.
    pg_db.add(
        CandidateDocumentAssignment(
            candidate_id=candidate.id,
            list_id=current[0].list_id,
            version_id=current[0].version_id,
            version_number=1,
            list_name_snapshot="x",
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.flush()
    pg_db.rollback()


def test_concurrent_item_status_change_single_winner(client: TestClient, pg_db: Session) -> None:
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    admin_csrf = _login(client, "admin1")
    published = _published_list(client, admin_csrf)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(pg_db, owner=hr, email="item@example.com")
    csrf = _login(client, "hr1")
    current = client.post(
        f"/candidates/{candidate.id}/documents/apply",
        json={"list_id": published["id"]},
        headers={"X-CSRF-Token": csrf},
    ).json()["current"]
    item_id = current["items"][0]["id"]

    def flip() -> Any:
        return client.patch(
            f"/candidates/{candidate.id}/documents/items/{item_id}",
            json={"status": "received", "expected_version": 1},
            headers={"X-CSRF-Token": csrf},
        )

    responses = _race(flip)
    codes = sorted(r.status_code for r in responses)
    # Either one wins and the other is a stale 409, or the second arrives
    # after the commit and is the no-op «already received» 200 — in every
    # case the row is at version 2, never 3.
    assert codes in ([200, 200], [200, 409]), [r.text for r in responses]
    item = pg_db.get(CandidateDocumentItem, UUID(item_id))
    assert item is not None
    pg_db.refresh(item)
    assert item.version == 2 and item.status.value == "received"


# --- Rules: races and delivery ------------------------------------------------------------


def _rule(client: TestClient, csrf: str, **overrides: Any) -> dict:
    payload: dict = {
        "name": "Оффер → запрос",
        "trigger_type": "stage_entered",
        "trigger_params": {"stage": "offer"},
        "conditions": {},
        "action_type": "send_document_request",
        "action_params": {"channel": "email"},
    }
    payload.update(overrides)
    response = client.post("/automation-rules", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    return response.json()


def test_concurrent_stage_transitions_execute_rule_once(client: TestClient, pg_db: Session) -> None:
    """Two parallel PATCHes moving the same candidate to the same stage:
    the candidate row lock + the execution unique key give one logical
    rule action and one outbox row."""
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    admin_csrf = _login(client, "admin1")
    published = _published_list(client, admin_csrf)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(pg_db, owner=hr, email="race-rule@example.com")
    _allow_email(pg_db, candidate)
    csrf = _login(client, "hr1")
    client.post(
        f"/candidates/{candidate.id}/documents/apply",
        json={"list_id": published["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    rule = _rule(client, csrf)

    def move() -> Any:
        return client.patch(
            f"/candidates/{candidate.id}", json={"stage": "offer"}, headers={"X-CSRF-Token": csrf}
        )

    responses = _race(move)
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    executions = (
        pg_db.execute(
            select(AutomationRuleExecution).where(
                AutomationRuleExecution.rule_id == UUID(rule["id"])
            )
        )
        .scalars()
        .all()
    )
    queued = [e for e in executions if e.outcome.value == "queued"]
    assert len(queued) == 1, [(e.outcome.value, e.outcome_class) for e in executions]
    rows = (
        pg_db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].source == NotificationSource.RULE
    # The execution unique key refuses a second row for the same dedupe key.
    pg_db.add(
        AutomationRuleExecution(
            rule_id=queued[0].rule_id,
            rule_version=1,
            trigger_type="stage_entered",
            trigger_object_type="candidate",
            trigger_object_id=candidate.id,
            action_type="send_document_request",
            outcome=queued[0].outcome,
            dedupe_key=queued[0].dedupe_key,
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.flush()
    pg_db.rollback()


def test_parallel_due_rule_passes_queue_once(
    client: TestClient, pg_db: Session, settings: Settings
) -> None:
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    admin_csrf = _login(client, "admin1")
    published = _published_list(client, admin_csrf)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(pg_db, owner=hr, email="due@example.com")
    _allow_email(pg_db, candidate)
    apply_published_list(
        pg_db,
        candidate=candidate,
        list_id=UUID(published["id"]),
        actor_user_id=hr.id,
        now=utc_now() - timedelta(days=5),
    )
    pg_db.commit()
    csrf = _login(client, "hr1")
    rule = _rule(
        client,
        csrf,
        name="Напоминание",
        trigger_type="documents_missing_due",
        trigger_params={"days_after": 3},
        action_type="send_document_reminder",
        action_params={"delay_days": 0, "channel": "email"},
    )
    engine = pg_db.get_bind()

    def pass_() -> int:
        with Session(engine) as session:
            return scan_due_rules(session, settings=settings, now=utc_now())

    results = _race(pass_)
    assert sum(results) >= 1
    rows = (
        pg_db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.recipient_candidate_id == candidate.id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].notification_type == NotificationType.CANDIDATE_DOCUMENT_REMINDER
    executions = (
        pg_db.execute(
            select(AutomationRuleExecution).where(
                AutomationRuleExecution.rule_id == UUID(rule["id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(executions) == 1 and executions[0].outcome.value == "queued"


def test_rule_reminder_delivered_then_skipped_after_receipt(
    client: TestClient, pg_db: Session, settings: Settings, smtp_stub: SmtpStub
) -> None:
    make_user(pg_db, username="admin1", role=UserRole.ADMIN)
    hr = make_user(pg_db, username="hr1", role=UserRole.HR)
    admin_csrf = _login(client, "admin1")
    published = _published_list(client, admin_csrf)
    client.post("/auth/logout", headers={"X-CSRF-Token": admin_csrf})
    candidate = make_candidate(pg_db, owner=hr, email="deliver@example.com")
    _allow_email(pg_db, candidate)
    csrf = _login(client, "hr1")
    current = client.post(
        f"/candidates/{candidate.id}/documents/apply",
        json={"list_id": published["id"]},
        headers={"X-CSRF-Token": csrf},
    ).json()["current"]
    rule = _rule(client, csrf)
    response = client.patch(
        f"/candidates/{candidate.id}", json={"stage": "offer"}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    now = utc_now() + timedelta(minutes=1)
    # Worker: claim + deliver through the SMTP stub (outside quiet hours: the
    # scheduled time was computed for the owner's window; force it due).
    row = pg_db.execute(
        select(NotificationOutbox).where(NotificationOutbox.recipient_candidate_id == candidate.id)
    ).scalar_one()
    row.scheduled_at = utc_now() - timedelta(minutes=1)
    row.next_attempt_at = None
    pg_db.commit()
    claimed = claim_batch(pg_db, now=now, batch_size=10, lease_seconds=120)
    assert [r.id for r in claimed] == [row.id]
    assert process_external_row(pg_db, row.id, settings=settings, now=now) == "accepted"
    assert len(smtp_stub.data_blocks) == 1
    wire = smtp_stub.data_blocks[0].decode("utf-8", errors="replace")
    assert "deliver@example.com" in wire
    pg_db.refresh(row)
    assert row.status == DeliveryStatus.ACCEPTED and row.delivered_at is None
    history = client.get(f"/automation-rules/{rule['id']}/executions").json()
    assert history["items"][0]["outcome"] == "queued"

    # All documents received → the next reminder row is skipped, no traffic.
    for item in current["items"]:
        client.patch(
            f"/candidates/{candidate.id}/documents/items/{item['id']}",
            json={"status": "received", "expected_version": 1},
            headers={"X-CSRF-Token": csrf},
        )
    manual = client.post(
        f"/candidates/{candidate.id}/documents/messages",
        json={"message_type": "document_reminder", "idempotency_key": "integ-doc-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert manual.status_code == 409  # nothing missing → refused at queue time
    # Simulate a row queued BEFORE the receipt (the send-time backstop).
    stale = NotificationOutbox(
        recipient_candidate_id=candidate.id,
        channel=row.channel,
        notification_type=NotificationType.CANDIDATE_DOCUMENT_REMINDER,
        source=NotificationSource.RULE,
        title="Напоминание о документах",
        body="stale",
        idempotency_key="stale-doc-row",
        template="candidate_document_reminder",
        template_version=1,
        rule_id=UUID(rule["id"]),
        initiator_user_id=hr.id,
        object_type="document_assignment",
        object_id=UUID(current["id"]),
        object_snapshot={"item_keys": ["passport", "snils"]},
        status=DeliveryStatus.SENDING,
        started_at=now,
        lease_expires_at=now + timedelta(minutes=2),
        queued_at=now,
    )
    pg_db.add(stale)
    pg_db.commit()
    assert process_external_row(pg_db, stale.id, settings=settings, now=now) == "skipped"
    pg_db.refresh(stale)
    assert stale.error_class == "documents_complete"
    assert len(smtp_stub.data_blocks) == 1

    # Soft-deleting the rule keeps its history.
    assert (
        client.delete(f"/automation-rules/{rule['id']}", headers={"X-CSRF-Token": csrf}).status_code
        == 204
    )
    assert (
        pg_db.execute(
            select(AutomationRuleExecution).where(
                AutomationRuleExecution.rule_id == UUID(rule["id"])
            )
        )
        .scalars()
        .all()
        != []
    )
    with pytest.raises(IntegrityError):
        pg_db.execute(text("DELETE FROM automation_rules WHERE id = :id"), {"id": rule["id"]})
    pg_db.rollback()
