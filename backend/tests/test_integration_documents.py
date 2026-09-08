"""Real PostgreSQL races and immutable-history constraints; never SQLite."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.document_rules import scan_document_rules
from app.document_schemas import ExpectedVersion, NewVersion
from app.models import (
    CandidateDocumentSet,
    DocumentListVersion,
    DocumentRuleExecution,
    NotificationOutbox,
    User,
)
from app.routers.documents import new_version, transition
from app.utils import utc_now
from tests import test_documents as contracts
from tests.test_worker_candidate import _settings

pytestmark = pytest.mark.integration


def parallel(action: Callable[[int], int]) -> list[int]:
    barrier = Barrier(2)

    def run(i: int) -> int:
        barrier.wait(timeout=10)
        return action(i)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, [0, 1]))


def test_pg_exact_snapshot_and_conflicts(pg_client: TestClient, pg_db: Session) -> None:
    contracts.test_exact_snapshot_conflict_and_missing(pg_client, pg_db)


def test_pg_concurrent_draft_allocation(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine
) -> None:
    _, _, parent, _, _ = contracts.setup_documents(pg_client, pg_db)
    admin = pg_db.scalar(select(User).where(User.username == "doc-admin"))
    assert admin
    user_id = admin.id
    pg_db.rollback()

    def action(_: int) -> int:
        with Session(pg_engine) as db:
            user = db.get(User, user_id)
            assert user
            try:
                new_version(
                    UUID(parent["id"]),
                    NewVersion(**contracts.CONTENT, expected_version=parent["version"]),
                    db,
                    user,
                )
                return 201
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    assert sorted(parallel(action)) == [201, 409]
    with Session(pg_engine) as db:
        versions = db.scalars(
            select(DocumentListVersion).where(DocumentListVersion.list_id == UUID(parent["id"]))
        ).all()
        assert len(versions) == 2 and len({v.number for v in versions}) == 2


def test_pg_concurrent_publication_empty_scope(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine
) -> None:
    from tests.test_candidate_messages import _login

    contracts.setup_documents(pg_client, pg_db)
    headers = {"X-CSRF-Token": _login(pg_client, "doc-admin")}
    parents = [
        pg_client.post(
            "/document-lists", json={**contracts.CONTENT, "stage": "offer"}, headers=headers
        ).json()
        for _ in range(2)
    ]
    admin = pg_db.scalar(select(User).where(User.username == "doc-admin"))
    assert admin
    user_id = admin.id
    pg_db.rollback()

    def action(i: int) -> int:
        with Session(pg_engine) as db:
            user = db.get(User, user_id)
            assert user
            p = parents[i]
            transition(
                UUID(p["id"]),
                UUID(p["versions"][0]["id"]),
                "publish",
                ExpectedVersion(expected_version=p["version"]),
                db,
                user,
            )
            return 200

    assert parallel(action) == [200, 200]
    with Session(pg_engine) as db:
        versions = db.scalars(
            select(DocumentListVersion).where(DocumentListVersion.stage == "offer")
        ).all()
        assert sorted(v.state for v in versions) == ["archived", "published"]


@pytest.mark.parametrize(
    "table,mutation",
    [
        ("document_list_versions", "UPDATE document_list_versions SET name = 'Недопустимо'"),
        ("document_list_versions", "DELETE FROM document_list_versions"),
        ("candidate_document_sets", "UPDATE candidate_document_sets SET revision=99"),
        ("candidate_document_sets", "DELETE FROM candidate_document_sets"),
    ],
)
def test_pg_immutable_snapshot_guards(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine, table: str, mutation: str
) -> None:
    contracts.setup_documents(pg_client, pg_db)
    pg_db.rollback()
    with Session(pg_engine) as db, pytest.raises(IntegrityError):
        db.execute(text(mutation))
        db.commit()


def test_pg_actual_stage_transition_apply_and_parallel_scanners(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine
) -> None:
    from app.worker import claim_batch, process_row
    from tests.conftest import make_candidate
    from tests.test_candidate_messages import _login

    hr, _, parent, _, _ = contracts.setup_documents(pg_client, pg_db)
    candidate = make_candidate(pg_db, owner=hr)
    headers = {"X-CSRF-Token": _login(pg_client, hr.username)}
    params = {
        "trigger": "stage_transition",
        "action": "apply_list",
        "stage": "offer",
        "list_id": parent["id"],
    }
    response = pg_client.post(
        "/document-rules", json={"name": "Применить", "params": params}, headers=headers
    )
    assert response.status_code == 201, response.text
    rule = response.json()
    change = pg_client.patch(
        f"/candidates/{candidate.id}", json={"stage": "offer"}, headers=headers
    )
    assert change.status_code == 200, change.text
    now = utc_now() + timedelta(days=1)
    now = now.replace(hour=12, minute=0)
    while now.weekday() > 4:
        now += timedelta(days=1)
    settings = _settings(NOTIFICATION_DEFAULT_TIMEZONE="UTC")
    pg_db.rollback()

    def action(_: int) -> int:
        with Session(pg_engine) as db:
            return scan_document_rules(db, settings=settings, now=now, batch_size=20)

    assert sorted(parallel(action)) == [0, 1]
    with Session(pg_engine) as db:
        jobs = claim_batch(db, now=now, batch_size=20, lease_seconds=120)
        assert len(jobs) == 1
        job_id = jobs[0].id

    def execute(_: int) -> int:
        with Session(pg_engine) as db:
            job = db.get(NotificationOutbox, job_id)
            assert job
            assert process_row(db, job, settings=settings, now=now) == "delivered"
            return 1

    assert parallel(execute) == [1, 1]
    with Session(pg_engine) as db:
        assert (
            len(
                db.scalars(
                    select(CandidateDocumentSet).where(
                        CandidateDocumentSet.candidate_id == candidate.id
                    )
                ).all()
            )
            == 1
        )
        executions = db.scalars(
            select(DocumentRuleExecution).where(DocumentRuleExecution.rule_id == UUID(rule["id"]))
        ).all()
        assert len(executions) == 1 and executions[0].outcome == "applied"
    with Session(pg_engine) as db, pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM document_rule_executions"))
        db.commit()


def test_pg_rules_disable_history(pg_client: TestClient, pg_db: Session) -> None:
    # These contracts do not use API channel configuration; execution uses fake settings.
    contracts.test_rules_execute_disable_and_history(pg_client, pg_db)


def test_pg_scheduled_rights_revocation(pg_client: TestClient, pg_db: Session) -> None:
    contracts.test_scheduled_reminder_and_rights_loss(pg_client, pg_db)


def test_pg_parallel_receipt_updates(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine
) -> None:
    from app.document_schemas import ItemUpdate
    from app.routers.documents import item_state

    hr, candidate, _, snapshot, _ = contracts.setup_documents(pg_client, pg_db)
    user_id, candidate_id = hr.id, candidate.id
    pg_db.rollback()

    def action(_: int) -> int:
        with Session(pg_engine) as db:
            user = db.get(User, user_id)
            assert user
            try:
                item_state(
                    candidate_id,
                    "passport",
                    ItemUpdate(
                        set_id=UUID(snapshot["set_id"]), state="received", expected_version=1
                    ),
                    db,
                    user,
                )
                return 200
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    assert sorted(parallel(action)) == [200, 409]


def test_pg_receipt_mutation_serializes_with_provider(
    pg_client: TestClient, pg_db: Session, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import TimeoutError
    from threading import Event
    from typing import Any

    import app.worker as worker
    from app.main import create_app
    from app.models import DeliveryStatus
    from app.smtp import SmtpSendResult
    from app.worker import process_external_row

    started, release = Event(), Event()
    sent = []

    def provider(*args: Any, **kwargs: Any) -> SmtpSendResult:
        sent.append(kwargs)
        started.set()
        assert release.wait(10)
        return SmtpSendResult(outcome="accepted", provider_message_id="provider-1")

    monkeypatch.setattr(worker, "_send_email", provider)
    settings = _settings()
    with TestClient(create_app(settings, engine=pg_engine)) as client:
        _, candidate, _, snapshot, headers = contracts.setup_documents(client, pg_db)
        contracts._allow_email(pg_db, candidate)
        response = client.post(
            f"/candidates/{candidate.id}/messages/send",
            json={
                "message_type": "document_request",
                "channel": "email",
                "document_set_id": snapshot["set_id"],
                "idempotency_key": "provider-race-1",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        row_id = UUID(response.json()["messages"][0]["id"])
        row = pg_db.get(NotificationOutbox, row_id)
        assert row
        row.status = DeliveryStatus.SENDING
        row.lease_expires_at = utc_now() + timedelta(minutes=2)
        pg_db.commit()

        def send() -> str:
            with Session(pg_engine) as db:
                return process_external_row(db, row_id, settings=settings, now=utc_now())

        with ThreadPoolExecutor(max_workers=2) as pool:
            sending = pool.submit(send)
            assert started.wait(5)
            mutation = pool.submit(
                client.patch,
                f"/candidates/{candidate.id}/documents/passport",
                json={"state": "received", "set_id": snapshot["set_id"], "expected_version": 1},
                headers=headers,
            )
            try:
                with pytest.raises(TimeoutError):
                    mutation.result(timeout=0.2)
            finally:
                release.set()
            assert sending.result(timeout=10) == "accepted"
            assert mutation.result(timeout=10).status_code == 200
        assert len(sent) == 1
        pg_db.refresh(row)
        assert row.provider_message_id == "provider-1" and row.delivered_at is None
        # A terminal send and its exact text cannot be rewritten/deleted.
        with Session(pg_engine) as db, pytest.raises(IntegrityError):
            db.execute(
                text("UPDATE notification_outbox SET body = 'Иной текст' WHERE id=:id"),
                {"id": row_id},
            )
            db.commit()


def test_pg_disabled_rule_before_discovery(pg_client: TestClient, pg_db: Session) -> None:
    _, _, parent, _, headers = contracts.setup_documents(pg_client, pg_db)
    params = {
        "trigger": "scheduled_reminder",
        "action": "document_reminder",
        "stage": "new",
        "list_id": parent["id"],
        "channel": "email",
        "days": 1,
    }
    created = pg_client.post(
        "/document-rules",
        json={"name": "Выключено", "params": params, "enabled": False},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert (
        scan_document_rules(
            pg_db, settings=_settings(), now=utc_now() + timedelta(days=3), batch_size=20
        )
        == 0
    )
    with pytest.raises(IntegrityError), pg_db.begin_nested():
        pg_db.execute(text("DELETE FROM document_rules"))
