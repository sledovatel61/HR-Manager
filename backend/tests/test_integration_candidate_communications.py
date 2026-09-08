"""PostgreSQL integration tests for candidate communications (phase 10).

Runs against a real PostgreSQL (Alembic ``0010`` applied): advisory-locked
candidate claims never overlap across worker sessions, an interview event
auto-queues candidate letters in the same transaction, and the worker
delivers them with honest ``accepted`` semantics.
"""

from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import app.worker as worker_module
from app.candidate_communications import EMAIL_CHANNEL, schedule_candidate_message
from app.config import Settings
from app.models import (
    AuditAction,
    AuditEvent,
    Candidate,
    CandidateContactChannel,
    CandidateMessage,
    CandidateMessageSource,
    CandidateMessageType,
    UserRole,
)
from app.smtp import SmtpSendResult
from tests.conftest import (
    FIXTURE_PASSWORD,
    make_candidate,
    make_user,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _with_transport(base: Settings) -> Settings:
    """Base integration settings + transport config for candidate sends."""
    values = base.model_dump()
    values.update(
        {
            "smtp_enabled": True,
            "smtp_host": "127.0.0.1",
            "smtp_port": 1025,
            "smtp_from_address": "hr@example.test",
            "telegram_enabled": True,
            "telegram_bot_token": "test-token",
            "telegram_bot_username": "hr_test_bot",
        }
    )
    return Settings.model_validate(values)


def _bind_email(
    db: Session, candidate: Candidate, *, address: str = "candidate@example.test"
) -> None:
    db.add(
        CandidateContactChannel(
            candidate_id=candidate.id,
            channel=EMAIL_CHANNEL,
            email_address=address,
            consent_granted=True,
            consent_at=NOW,
            consent_source="candidate_email_link",
            consent_policy_version="phase10-v1",
        )
    )
    db.commit()


def test_candidate_claim_never_overlaps_across_worker_sessions(
    pg_db: Session, pg_engine: Engine
) -> None:
    owner = make_user(pg_db, username="pg-claim-cand", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=owner, email="candidate@example.test")
    _bind_email(pg_db, candidate)
    for index in range(4):
        schedule_candidate_message(
            pg_db,
            candidate=candidate,
            channel=EMAIL_CHANNEL,
            message_type=CandidateMessageType.DOCUMENTS_REQUEST,
            source=CandidateMessageSource.MANUAL,
            title="Запрос документов",
            body=f"Тело {index}",
            recipient_email="candidate@example.test",
            idempotency_key=f"pg-claim-{index}",
            now=NOW,
        )
    pg_db.commit()

    first = worker_module.claim_candidate_batch(pg_db, now=NOW, batch_size=3, lease_seconds=120)
    second_session = Session(pg_engine)
    try:
        second = worker_module.claim_candidate_batch(
            second_session, now=NOW, batch_size=3, lease_seconds=120
        )
    finally:
        second_session.close()
    first_ids = {row.id for row in first}
    second_ids = {row.id for row in second}
    assert len(first_ids) == 3
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert all(row.status == "sending" for row in list(first) + list(second))


def test_candidate_worker_delivers_email_accepted_on_postgres(
    pg_db: Session, pg_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = make_user(pg_db, username="pg-send-cand", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=owner, email="candidate@example.test")
    _bind_email(pg_db, candidate)
    row, _ = schedule_candidate_message(
        pg_db,
        candidate=candidate,
        channel=EMAIL_CHANNEL,
        message_type=CandidateMessageType.DOCUMENTS_REQUEST,
        source=CandidateMessageSource.MANUAL,
        title="Запрос документов",
        body="Тело письма",
        recipient_email="candidate@example.test",
        idempotency_key="pg-send-1",
        now=NOW,
    )
    pg_db.commit()

    claimed = worker_module.claim_candidate_batch(pg_db, now=NOW, batch_size=1, lease_seconds=120)
    assert claimed and claimed[0].id == row.id

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        return SmtpSendResult(outcome="accepted", provider_message_id="pg-mail-1")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    status = worker_module.process_candidate_message(
        pg_db,
        row.id,
        settings=_with_transport(pg_settings),
        now=NOW,
    )
    assert status == "accepted"
    pg_db.refresh(row)
    assert row.status == "accepted"
    assert row.accepted_at is not None
    assert row.delivered_at is None
    assert row.provider_message_id == "pg-mail-1"


def test_interview_event_auto_queues_candidate_message(
    pg_client: TestClient, pg_db: Session, pg_settings: Settings
) -> None:
    app = cast(FastAPI, pg_client.app)
    app.state.settings = _with_transport(pg_settings)
    hr = make_user(pg_db, username="pg-event-cand", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr, email="candidate@example.test")
    _bind_email(pg_db, candidate)
    csrf = _login(pg_client, "pg-event-cand")

    created = pg_client.post(
        "/events",
        json={
            "candidate_id": str(candidate.id),
            "type": "interview",
            "title": "Собеседование с кандидатом",
            "starts_at": "2026-10-05T10:00:00Z",
            "location": "Офис, переговорная 3",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]

    # Same transaction: one queued letter (channel allowed), event snapshot.
    rows = (
        pg_db.execute(select(CandidateMessage).where(CandidateMessage.event_id == event_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.message_type == CandidateMessageType.INTERVIEW_SCHEDULED
    assert row.source == CandidateMessageSource.EVENT
    assert row.status == "queued"
    assert row.event_version == 1
    assert "переговорная 3" in row.body
    assert row.recipient_email == "candidate@example.test"

    # Rescheduling the interview replaces the stale queued letter.
    updated = pg_client.patch(
        f"/events/{event_id}",
        json={
            "expected_version": 1,
            "starts_at": "2026-10-06T11:00:00Z",
            "location": "Офис, переговорная 5",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    pg_db.expire_all()
    queued = (
        pg_db.execute(
            select(CandidateMessage).where(
                CandidateMessage.event_id == event_id,
                CandidateMessage.status == "queued",
            )
        )
        .scalars()
        .all()
    )
    assert len(queued) == 1
    assert queued[0].message_type == CandidateMessageType.INTERVIEW_RESCHEDULED
    assert queued[0].event_version == 2
    assert "переговорная 5" in queued[0].body


def test_consent_flow_and_revoke_on_postgres(
    pg_client: TestClient, pg_db: Session, pg_settings: Settings
) -> None:
    app = cast(FastAPI, pg_client.app)
    app.state.settings = _with_transport(pg_settings)
    hr = make_user(pg_db, username="pg-consent", role=UserRole.HR)
    candidate = make_candidate(pg_db, owner=hr, email="candidate@example.test")
    csrf = _login(pg_client, "pg-consent")

    requested = pg_client.post(
        f"/candidates/{candidate.id}/communications/email/consent-request",
        headers={"X-CSRF-Token": csrf},
    )
    assert requested.status_code == 202, requested.text
    invite = pg_db.execute(
        select(CandidateMessage).where(
            CandidateMessage.message_type == CandidateMessageType.CONSENT_INVITE
        )
    ).scalar_one()
    raw = invite.body.rsplit("consent/", 1)[1].split("\n", 1)[0].split()[0].strip()
    assert pg_client.get(f"/public/candidates/consent/{raw}").status_code == 200

    channel = pg_db.execute(
        select(CandidateContactChannel).where(CandidateContactChannel.candidate_id == candidate.id)
    ).scalar_one()
    assert channel.consent_granted is True
    assert channel.email_address == "candidate@example.test"

    grants = (
        pg_db.execute(
            select(AuditEvent).where(
                AuditEvent.action == AuditAction.CANDIDATE_CONSENT_GRANTED,
                AuditEvent.candidate_id == candidate.id,
            )
        )
        .scalars()
        .all()
    )
    assert len(grants) == 1
    assert "candidate@example.test" not in (grants[0].details or "")


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]
