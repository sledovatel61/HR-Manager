"""Unit tests for personal automation rules (phase 11).

Coverage:

* API: vocabulary, create/list/get/edit/toggle/delete of MY rules only
  (foreign rule 404), closed-schema validation (422), trigger/action
  matrix, unpublished list refused, optimistic concurrency (409), scope
  gate (admin without grant 403, HR/manager allowed), soft delete keeps
  history, disable/edit cancels queued jobs;
* engine — stage_entered: real execution on the candidate PATCH (apply
  list, queue request, queue delayed reminder), closed conditions (stage,
  list, has_missing_required, channel), owner quiet hours/workdays applied
  to the scheduled time, durable dedupe (same transition twice = one
  action), disabled/deleted rule never runs, inactive owner / out-of-scope
  candidate / revoked grant → skipped, no silent replacement of a current
  list; rule failure is contained (HTTP 200, ``failed`` history row);
* engine — documents_missing_due: worker pass queues a reminder once per
  day per assignment, skips complete/non-required lists, respects
  ``days_after`` in the owner's timezone;
* worker send-time checks: rule row skipped when the rule was disabled
  meanwhile, when the documents were received meanwhile (no provider
  call) and partially re-rendered when only some remain; rule rows use
  the OWNER's quiet hours.
* history rows carry ids/classes only — never text or PII.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.automation_rules as rules_module
import app.worker as worker_module
from app.automation_rules import run_stage_entered_rules, scan_due_rules
from app.config import Settings
from app.main import create_app
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    AuditEvent,
    AutomationRule,
    AutomationRuleExecution,
    Candidate,
    CandidateChannelConsent,
    CandidateDocumentAssignment,
    CandidateDocumentItem,
    CandidateDocumentStatus,
    CandidateStage,
    DeliveryChannel,
    DeliveryStatus,
    NotificationOutbox,
    NotificationPreference,
    NotificationSource,
    NotificationType,
    User,
    UserRole,
)
from app.smtp import SmtpSendResult
from app.worker import process_external_row, process_row
from tests.conftest import FIXTURE_PASSWORD, make_candidate, make_user

NOW = datetime(2026, 9, 4, 9, 0, 0, tzinfo=UTC)  # Friday 12:00 Moscow

ITEMS = [
    {"item_key": "passport", "name": "Паспорт", "is_required": True},
    {"item_key": "snils", "name": "СНИЛС", "is_required": True},
    {"item_key": "photo", "name": "Фото", "is_required": False},
]


def _login(client: TestClient, username: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": FIXTURE_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _settings(**overrides: str) -> Settings:
    values = {
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "SECRET_KEY": "unit-test-secret-key-0123456789abcdef",
        "SMTP_ENABLED": "true",
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "2525",
        "SMTP_ENCRYPTION": "starttls",
        "SMTP_USERNAME": "mailer@example.test",
        "SMTP_PASSWORD": "mailer-secret",
        "SMTP_FROM_ADDRESS": "hr@example.test",
        "CANDIDATE_EMAIL_CONFIRM_BASE_URL": "https://hr.example.test",
        "WORKER_MAX_ATTEMPTS": "3",
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture()
def channels_app(unit_engine: Any) -> Iterator[TestClient]:
    app = create_app(_settings(), engine=unit_engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_user(db_session: Session) -> User:
    return make_user(db_session, username="admin1", role=UserRole.ADMIN)


@pytest.fixture()
def hr_user(db_session: Session) -> User:
    return make_user(db_session, username="hr1", role=UserRole.HR)


def _allow_email(db: Session, candidate: Candidate) -> None:
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


def _published_list(client: TestClient, admin_csrf: str, *, name: str = "Приём") -> dict:
    created = client.post(
        "/document-lists",
        json={"name": name, "items": ITEMS},
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


@pytest.fixture()
def published(channels_app: TestClient, db_session: Session, admin_user: User) -> dict:
    csrf = _login(channels_app, "admin1")
    result = _published_list(channels_app, csrf)
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    return result


def _rule_payload(**overrides: Any) -> dict:
    payload: dict = {
        "name": "Оффер → запрос документов",
        "trigger_type": "stage_entered",
        "trigger_params": {"stage": "offer"},
        "conditions": {},
        "action_type": "send_document_request",
        "action_params": {},
    }
    payload.update(overrides)
    return payload


def _create_rule(client: TestClient, csrf: str, **overrides: Any) -> dict:
    response = client.post(
        "/automation-rules", json=_rule_payload(**overrides), headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_stage(client: TestClient, csrf: str, candidate: Candidate, stage: str) -> Any:
    return client.patch(
        f"/candidates/{candidate.id}", json={"stage": stage}, headers={"X-CSRF-Token": csrf}
    )


def _executions(db: Session, rule_id: str) -> list[AutomationRuleExecution]:
    return list(
        db.execute(
            select(AutomationRuleExecution)
            .where(AutomationRuleExecution.rule_id == UUID(rule_id))
            .order_by(AutomationRuleExecution.executed_at)
        )
        .scalars()
        .all()
    )


def _outbox(db: Session, candidate: Candidate) -> list[NotificationOutbox]:
    return list(
        db.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.recipient_candidate_id == candidate.id)
            .order_by(NotificationOutbox.queued_at)
        )
        .scalars()
        .all()
    )


# --- API ------------------------------------------------------------------------


def test_vocabulary_and_validation(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    csrf = _login(channels_app, "hr1")
    vocabulary = channels_app.get("/automation-rules/vocabulary").json()
    assert vocabulary["triggers"] == ["stage_entered", "documents_missing_due"]
    assert vocabulary["trigger_actions"]["documents_missing_due"] == ["send_document_reminder"]
    assert vocabulary["channels"] == ["email", "telegram"] and vocabulary["max_delay_days"] == 30
    assert "offer" in vocabulary["stages"]

    bad = [
        _rule_payload(trigger_params={}),
        _rule_payload(trigger_params={"stage": "nope"}),
        _rule_payload(trigger_params={"stage": "offer", "extra": 1}),
        _rule_payload(action_type="apply_document_list", action_params={}),
        _rule_payload(action_type="apply_document_list", action_params={"list_id": str(uuid4())}),
        _rule_payload(action_type="send_document_reminder", action_params={"delay_days": 31}),
        _rule_payload(action_type="send_document_reminder", action_params={"delay_days": -1}),
        _rule_payload(action_params={"channel": "sms"}),
        _rule_payload(conditions={"stage": "unknown"}),
        _rule_payload(conditions={"list_id": str(uuid4())}),
        _rule_payload(conditions={"body": "text"}),
        _rule_payload(
            trigger_type="documents_missing_due",
            trigger_params={"days_after": 3},
            action_type="send_document_request",
        ),
        _rule_payload(trigger_type="documents_missing_due", trigger_params={"days_after": 0}),
        _rule_payload(name=""),
        _rule_payload(name="x\x00y"),
        _rule_payload(recipient="someone@example.com"),
    ]
    for payload in bad:
        response = channels_app.post(
            "/automation-rules", json=payload, headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 422, (payload, response.text)
        assert "hr1" not in response.text  # no payload echo of identities

    # Canonical storage of typed params (unknown-free, exclude_none conditions).
    rule = _create_rule(
        channels_app,
        csrf,
        action_type="send_document_reminder",
        action_params={"delay_days": 3, "channel": "email"},
        conditions={"has_missing_required": True, "channel": None},
    )
    assert rule["action_params"] == {"channel": "email", "delay_days": 3}
    assert rule["conditions"] == {"has_missing_required": True}
    assert rule["is_enabled"] is True and rule["version"] == 1
    assert rule["owner_user_id"] == str(hr_user.id)


def test_rules_are_private_and_versioned(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    make_user(db_session, username="hr2", role=UserRole.HR)
    csrf = _login(channels_app, "hr1")
    rule = _create_rule(channels_app, csrf)
    assert [r["id"] for r in channels_app.get("/automation-rules").json()["items"]] == [rule["id"]]
    assert channels_app.get(f"/automation-rules/{rule['id']}").status_code == 200
    assert channels_app.get(f"/automation-rules/{uuid4()}").status_code == 404
    assert channels_app.get("/automation-rules/nope").status_code == 404

    # Edit with a stale version → 409; with the right one → version bump.
    update = {
        "expected_version": 5,
        "name": "Новое имя",
        "trigger_type": "stage_entered",
        "trigger_params": {"stage": "interview_scheduled"},
        "conditions": {},
        "action_type": "apply_document_list",
        "action_params": {"list_id": published["id"]},
    }
    response = channels_app.put(
        f"/automation-rules/{rule['id']}", json=update, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 409
    update["expected_version"] = 1
    response = channels_app.put(
        f"/automation-rules/{rule['id']}", json=update, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2 and response.json()["name"] == "Новое имя"

    # Toggle.
    response = channels_app.post(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": 2, "is_enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200 and response.json()["is_enabled"] is False
    assert response.json()["version"] == 3
    response = channels_app.post(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": 2, "is_enabled": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": csrf})

    # Another user: the rule does not exist for them (list, get, edit, toggle, delete).
    csrf2 = _login(channels_app, "hr2")
    assert channels_app.get("/automation-rules").json()["items"] == []
    assert channels_app.get(f"/automation-rules/{rule['id']}").status_code == 404
    assert (
        channels_app.put(
            f"/automation-rules/{rule['id']}", json=update, headers={"X-CSRF-Token": csrf2}
        ).status_code
        == 404
    )
    assert (
        channels_app.post(
            f"/automation-rules/{rule['id']}/toggle",
            json={"expected_version": 3, "is_enabled": True},
            headers={"X-CSRF-Token": csrf2},
        ).status_code
        == 404
    )
    assert (
        channels_app.delete(
            f"/automation-rules/{rule['id']}", headers={"X-CSRF-Token": csrf2}
        ).status_code
        == 404
    )
    assert channels_app.get(f"/automation-rules/{rule['id']}/executions").status_code == 404
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": csrf2})

    # Soft delete keeps the row (history reference) but hides it.
    csrf = _login(channels_app, "hr1")
    response = channels_app.delete(
        f"/automation-rules/{rule['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 204
    assert channels_app.get(f"/automation-rules/{rule['id']}").status_code == 404
    assert channels_app.get("/automation-rules").json()["items"] == []
    row = db_session.get(AutomationRule, UUID(rule["id"]))
    assert row is not None and row.deleted_at is not None and row.is_enabled is False
    # CSRF/authentication.
    assert channels_app.post("/automation-rules", json=_rule_payload()).status_code == 403
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert channels_app.get("/automation-rules").status_code == 401


def test_rule_creation_requires_candidate_scope(
    channels_app: TestClient, db_session: Session, admin_user: User, published: dict
) -> None:
    make_user(db_session, username="mgr1", role=UserRole.MANAGER)
    csrf = _login(channels_app, "admin1")
    # Admin without the pilot grant: 403 (the list itself is readable).
    assert channels_app.get("/automation-rules").status_code == 200
    response = channels_app.post(
        "/automation-rules", json=_rule_payload(), headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 403
    db_session.add(
        AccessGrant(
            user_id=admin_user.id,
            scope=AccessGrantScope.PILOT_FULL_ACCESS,
            granted_by_user_id=admin_user.id,
        )
    )
    db_session.commit()
    rule = _create_rule(channels_app, csrf)
    # Grant revoked → the rule may be disabled but not re-enabled.
    grant = db_session.execute(select(AccessGrant)).scalar_one()
    grant.revoked_at = NOW
    db_session.commit()
    response = channels_app.post(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": 1, "is_enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    response = channels_app.post(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": 2, "is_enabled": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    csrf = _login(channels_app, "mgr1")
    _create_rule(channels_app, csrf)


# --- Engine: stage entered ---------------------------------------------------------------


def test_stage_rule_applies_list_then_queues_request(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="rule@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    apply_rule = _create_rule(
        channels_app,
        csrf,
        name="Применить список",
        action_type="apply_document_list",
        action_params={"list_id": published["id"]},
    )
    request_rule = _create_rule(
        channels_app,
        csrf,
        name="Запросить документы",
        conditions={"has_missing_required": True, "list_id": published["id"]},
    )
    _create_rule(channels_app, csrf, name="Другая стадия", trigger_params={"stage": "hired"})

    response = _set_stage(channels_app, csrf, candidate, "offer")
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "offer"

    # Rule 1 applied the CURRENT published version (snapshot, by rule).
    assignment = db_session.execute(
        select(CandidateDocumentAssignment).where(
            CandidateDocumentAssignment.candidate_id == candidate.id
        )
    ).scalar_one()
    assert assignment.assigned_by_user_id is None
    assert str(assignment.assigned_by_rule_id) == apply_rule["id"]
    assert assignment.version_number == 1 and len(assignment.items) == 3
    # Rule 2 (created later, evaluated after rule 1) saw the fresh list and
    # queued the request with the missing items only.
    rows = _outbox(db_session, candidate)
    assert len(rows) == 1
    row = rows[0]
    assert row.notification_type == NotificationType.CANDIDATE_DOCUMENT_REQUEST
    assert row.source == NotificationSource.RULE and str(row.rule_id) == request_rule["id"]
    assert row.initiator_user_id == hr_user.id
    assert row.object_type == "document_assignment" and row.object_id == assignment.id
    assert row.object_snapshot is not None
    assert row.object_snapshot["item_keys"] == ["passport", "snils", "photo"]
    assert "— Паспорт" in (row.body or "")
    assert row.status == DeliveryStatus.QUEUED
    assert row.scheduled_at is not None and row.scheduled_at >= row.queued_at - timedelta(seconds=1)

    # History: one row per matching rule; the third rule did not match.
    applied = _executions(db_session, apply_rule["id"])
    queued = _executions(db_session, request_rule["id"])
    assert [e.outcome.value for e in applied] == ["applied"]
    assert applied[0].list_id == assignment.list_id
    assert applied[0].list_version_id == assignment.version_id
    assert [e.outcome.value for e in queued] == ["queued"]
    assert queued[0].outbox_ids == [str(row.id)]
    assert queued[0].candidate_id == candidate.id
    assert queued[0].trigger_object_type == "candidate"
    assert queued[0].rule_version == 1
    for execution in applied + queued:
        assert "Паспорт" not in execution.dedupe_key
        assert "rule@example.com" not in execution.dedupe_key

    # The executions endpoint (mine only) exposes exactly this.
    history = channels_app.get(f"/automation-rules/{request_rule['id']}/executions").json()
    assert history["total"] == 1 and history["items"][0]["outcome"] == "queued"
    assert history["items"][0]["candidate_id"] == str(candidate.id)
    assert "body" not in history["items"][0]

    # Re-entering the same stage later is a NEW transition (new marker):
    # the list is already applied → skipped; a second request is a pending
    # duplicate for the outbox but a new logical action for the rule.
    _set_stage(channels_app, csrf, candidate, "interview_scheduled")
    _set_stage(channels_app, csrf, candidate, "offer")
    applied = _executions(db_session, apply_rule["id"])
    assert [e.outcome.value for e in applied] == ["applied", "skipped"]
    assert applied[1].outcome_class == "already_applied"
    assert (
        db_session.execute(
            select(CandidateDocumentAssignment).where(
                CandidateDocumentAssignment.candidate_id == candidate.id
            )
        )
        .scalars()
        .one()
        .id
        == assignment.id
    )  # never silently replaced

    # Audit: candidate stage change still there, rule executions not audited
    # with any text.
    for event in db_session.execute(select(AuditEvent)).scalars().all():
        assert "Паспорт" not in (event.details or "")


def test_stage_rule_dedupe_and_conditions(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="cond@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    rule = _create_rule(channels_app, csrf, conditions={"has_missing_required": True})
    channel_rule = _create_rule(channels_app, csrf, conditions={"channel": "telegram"})
    stage_rule = _create_rule(channels_app, csrf, conditions={"stage": "hired"})

    _set_stage(channels_app, csrf, candidate, "offer")
    # No list applied → has_missing_required is false → skipped, nothing queued.
    executions = _executions(db_session, rule["id"])
    assert [e.outcome_class for e in executions] == ["condition_missing_required"]
    assert [e.outcome_class for e in _executions(db_session, channel_rule["id"])] == [
        "condition_channel"
    ]
    assert [e.outcome_class for e in _executions(db_session, stage_rule["id"])] == [
        "condition_stage"
    ]
    assert _outbox(db_session, candidate) == []

    # Durable dedupe: the same logical transition evaluated twice (a retry
    # of the same request/marker) collapses into one execution row.
    db_session.refresh(candidate)
    outcomes = run_stage_entered_rules(
        db_session,
        candidate=candidate,
        new_stage=CandidateStage.OFFER,
        transition_at=candidate.updated_at,
        settings=_settings(),
        now=NOW,
    )
    db_session.commit()
    assert all(o.outcome_class == "duplicate" for o in outcomes)
    assert len(_executions(db_session, rule["id"])) == 1


def test_stage_rule_reminder_respects_owner_quiet_hours_and_workdays(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    db_session.add(
        NotificationPreference(
            user_id=hr_user.id,
            timezone="Asia/Yekaterinburg",  # UTC+5
            quiet_hours_start="18:00",
            quiet_hours_end="10:00",
            workdays=[1, 2, 3, 4, 5],
            enabled_types=["system_alert"],
            enabled_channels=["in_app"],
        )
    )
    db_session.commit()
    candidate = make_candidate(db_session, owner=hr_user, email="quiet@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    _create_rule(
        channels_app,
        csrf,
        name="Применить",
        action_type="apply_document_list",
        action_params={"list_id": published["id"]},
    )
    reminder_rule = _create_rule(
        channels_app,
        csrf,
        name="Напомнить через 2 дня",
        action_type="send_document_reminder",
        action_params={"delay_days": 2, "channel": "email"},
    )
    # Freeze «now»: Friday 2026-09-04 16:00 UTC = 21:00 Yekaterinburg (quiet).
    frozen = datetime(2026, 9, 4, 16, 0, 0, tzinfo=UTC)
    from app.routers import candidates as candidates_router

    original = candidates_router.utc_now
    candidates_router.utc_now = lambda: frozen
    try:
        response = _set_stage(channels_app, csrf, candidate, "offer")
    finally:
        candidates_router.utc_now = original
    assert response.status_code == 200, response.text
    rows = _outbox(db_session, candidate)
    assert len(rows) == 1
    row = rows[0]
    assert row.notification_type == NotificationType.CANDIDATE_DOCUMENT_REMINDER
    assert str(row.rule_id) == reminder_rule["id"]
    # +2 days = Sunday 21:00 local → not a workday and quiet → Monday 10:00
    # Yekaterinburg = 2026-09-07 05:00 UTC.
    assert row.scheduled_at == datetime(2026, 9, 7, 5, 0, tzinfo=UTC)
    assert row.quiet_hours_bypassed is False
    # The worker re-applies the OWNER's window when the row is processed early.
    row.status = DeliveryStatus.SENDING
    row.started_at = frozen
    row.lease_expires_at = frozen + timedelta(minutes=2)
    db_session.commit()
    status = process_row(
        db_session, row, settings=_settings(), now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
    )
    assert status == "queued"
    db_session.refresh(row)
    assert row.scheduled_at_effective == datetime(2026, 9, 7, 5, 0, tzinfo=UTC)


def test_stage_rule_not_executable_without_scope_or_when_disabled(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    other_hr = make_user(db_session, username="hr2", role=UserRole.HR)
    manager = make_user(db_session, username="mgr1", role=UserRole.MANAGER)
    mine = make_candidate(db_session, owner=hr_user, email="mine@example.com")
    theirs = make_candidate(db_session, owner=other_hr, email="theirs@example.com")
    _allow_email(db_session, mine)
    _allow_email(db_session, theirs)
    csrf = _login(channels_app, "hr1")
    rule = _create_rule(channels_app, csrf)
    disabled = _create_rule(channels_app, csrf, name="Выключенное", is_enabled=False)
    channels_app.post("/auth/logout", headers={"X-CSRF-Token": csrf})

    # The manager moves BOTH candidates: my rule fires only for MY candidate.
    mgr_csrf = _login(channels_app, "mgr1")
    assert manager.role == UserRole.MANAGER
    # Apply a list first so the request has something to ask for.
    for candidate in (mine, theirs):
        channels_app.post(
            f"/candidates/{candidate.id}/documents/apply",
            json={"list_id": published["id"]},
            headers={"X-CSRF-Token": mgr_csrf},
        )
        assert _set_stage(channels_app, mgr_csrf, candidate, "offer").status_code == 200
    executions = _executions(db_session, rule["id"])
    by_candidate = {e.candidate_id: (e.outcome.value, e.outcome_class) for e in executions}
    assert by_candidate[mine.id] == ("queued", None)
    assert by_candidate[theirs.id] == ("skipped", "out_of_scope")
    assert len(_outbox(db_session, mine)) == 1 and _outbox(db_session, theirs) == []
    assert _executions(db_session, disabled["id"]) == []

    # Owner deactivated → the rule is not executable any more.
    hr_user.is_active = False
    db_session.commit()
    _set_stage(channels_app, mgr_csrf, mine, "interview_scheduled")
    _set_stage(channels_app, mgr_csrf, mine, "offer")
    last = _executions(db_session, rule["id"])[-1]
    assert (last.outcome.value, last.outcome_class) == ("skipped", "owner_inactive")
    assert len(_outbox(db_session, mine)) == 1


def test_rule_failure_is_contained(
    channels_app: TestClient,
    db_session: Session,
    hr_user: User,
    published: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="boom@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    rule = _create_rule(channels_app, csrf)

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret detail boom@example.com")

    monkeypatch.setattr(rules_module, "_run_action", explode)
    with caplog.at_level("WARNING"):
        response = _set_stage(channels_app, csrf, candidate, "offer")
    # The main operation succeeded and committed.
    assert response.status_code == 200 and response.json()["stage"] == "offer"
    db_session.refresh(candidate)
    assert candidate.stage == CandidateStage.OFFER
    executions = _executions(db_session, rule["id"])
    assert len(executions) == 1
    assert executions[0].outcome.value == "failed"
    assert executions[0].outcome_class == "internal_error:RuntimeError"
    assert _outbox(db_session, candidate) == []
    assert "boom@example.com" not in caplog.text
    assert "secret detail" not in caplog.text
    history = channels_app.get(f"/automation-rules/{rule['id']}/executions").json()
    assert history["items"][0]["outcome"] == "failed"


def test_disable_and_edit_cancel_queued_jobs(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="cancel@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    channels_app.post(
        f"/candidates/{candidate.id}/documents/apply",
        json={"list_id": published["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    rule = _create_rule(
        channels_app,
        csrf,
        action_type="send_document_reminder",
        action_params={"delay_days": 5},
    )
    _set_stage(channels_app, csrf, candidate, "offer")
    rows = _outbox(db_session, candidate)
    assert len(rows) == 1 and rows[0].status == DeliveryStatus.QUEUED
    response = channels_app.post(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": 1, "is_enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    db_session.refresh(rows[0])
    assert rows[0].status == DeliveryStatus.CANCELLED
    # The immutable history is untouched.
    assert [e.outcome.value for e in _executions(db_session, rule["id"])] == ["queued"]
    audit = [
        e
        for e in db_session.execute(select(AuditEvent)).scalars().all()
        if e.action == AuditAction.AUTOMATION_RULE_DISABLED
    ]
    assert len(audit) == 1 and "cancelled_jobs=1" in (audit[0].details or "")

    # Re-enable, fire again, then EDIT: the old plan is cancelled too.
    channels_app.post(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": 2, "is_enabled": True},
        headers={"X-CSRF-Token": csrf},
    )
    _set_stage(channels_app, csrf, candidate, "interview_scheduled")
    _set_stage(channels_app, csrf, candidate, "offer")
    rows = _outbox(db_session, candidate)
    queued = [r for r in rows if r.status == DeliveryStatus.QUEUED]
    assert len(queued) == 1
    response = channels_app.put(
        f"/automation-rules/{rule['id']}",
        json={
            "expected_version": 3,
            "name": "Изменённое",
            "trigger_type": "stage_entered",
            "trigger_params": {"stage": "offer"},
            "conditions": {},
            "action_type": "send_document_reminder",
            "action_params": {"delay_days": 1},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    db_session.refresh(queued[0])
    assert queued[0].status == DeliveryStatus.CANCELLED
    # Delete cancels as well and keeps history.
    assert (
        channels_app.delete(
            f"/automation-rules/{rule['id']}", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 204
    )
    assert len(_executions(db_session, rule["id"])) == 2


# --- Engine: documents missing and due (worker pass) --------------------------------------


def _apply_directly(
    db: Session, candidate: Candidate, published: dict, *, assigned_at: datetime
) -> CandidateDocumentAssignment:
    from app.document_lists import apply_published_list

    assignment, _ = apply_published_list(
        db,
        candidate=candidate,
        list_id=UUID(published["id"]),
        actor_user_id=None,
        now=assigned_at,
    )
    db.commit()
    db.refresh(assignment)
    return assignment


def test_due_rule_queues_reminder_once_per_day(
    channels_app: TestClient, db_session: Session, hr_user: User, published: dict
) -> None:
    due = make_candidate(db_session, owner=hr_user, email="due@example.com")
    fresh = make_candidate(db_session, owner=hr_user, email="fresh@example.com")
    complete = make_candidate(db_session, owner=hr_user, email="done@example.com")
    for candidate in (due, fresh, complete):
        _allow_email(db_session, candidate)
    _apply_directly(db_session, due, published, assigned_at=NOW - timedelta(days=4))
    _apply_directly(db_session, fresh, published, assigned_at=NOW - timedelta(days=1))
    done = _apply_directly(db_session, complete, published, assigned_at=NOW - timedelta(days=9))
    for item in done.items:
        if item.is_required:
            item.status = CandidateDocumentStatus.RECEIVED
    db_session.commit()

    csrf = _login(channels_app, "hr1")
    rule = _create_rule(
        channels_app,
        csrf,
        name="Напоминание через 3 дня",
        trigger_type="documents_missing_due",
        trigger_params={"days_after": 3},
        action_type="send_document_reminder",
        action_params={"delay_days": 0},
    )
    processed = scan_due_rules(db_session, settings=_settings(), now=NOW)
    assert processed == 1
    rows = _outbox(db_session, due)
    assert len(rows) == 1
    assert rows[0].notification_type == NotificationType.CANDIDATE_DOCUMENT_REMINDER
    assert rows[0].source == NotificationSource.RULE and str(rows[0].rule_id) == rule["id"]
    assert rows[0].object_snapshot is not None
    assert rows[0].object_snapshot["item_keys"] == ["passport", "snils", "photo"]
    assert _outbox(db_session, fresh) == [] and _outbox(db_session, complete) == []
    # Same day again: idempotent (no second row, no second execution).
    assert scan_due_rules(db_session, settings=_settings(), now=NOW + timedelta(hours=3)) == 0
    assert len(_outbox(db_session, due)) == 1
    executions = _executions(db_session, rule["id"])
    assert len(executions) == 1 and executions[0].trigger_object_type == "document_assignment"
    # Next day: a new logical reminder (outbox pending-duplicate is the
    # engine's problem: the previous row must be gone first).
    rows[0].status = DeliveryStatus.ACCEPTED
    db_session.commit()
    assert scan_due_rules(db_session, settings=_settings(), now=NOW + timedelta(days=1)) == 1
    assert len(_outbox(db_session, due)) == 2
    # Disabled rule: nothing.
    channels_app.post(
        f"/automation-rules/{rule['id']}/toggle",
        json={"expected_version": 1, "is_enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert scan_due_rules(db_session, settings=_settings(), now=NOW + timedelta(days=2)) == 0


# --- Worker send-time checks --------------------------------------------------------------


def _claim(db: Session, row: NotificationOutbox) -> None:
    row.status = DeliveryStatus.SENDING
    row.started_at = NOW
    row.lease_expires_at = NOW + timedelta(minutes=2)
    db.commit()
    db.refresh(row)


def test_worker_skips_when_documents_received_meanwhile(
    channels_app: TestClient,
    db_session: Session,
    hr_user: User,
    published: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="late@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    assignment = _apply_directly(db_session, candidate, published, assigned_at=NOW)
    response = channels_app.post(
        f"/candidates/{candidate.id}/documents/messages",
        json={"message_type": "document_request", "idempotency_key": "late-0001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    row = db_session.get(NotificationOutbox, UUID(response.json()["messages"][0]["id"]))
    assert row is not None
    calls: list = []

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        calls.append(text_body)
        return SmtpSendResult(outcome="accepted", provider_message_id="smtp-1")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)

    # Partially received → the body is re-rendered from the remaining items.
    items = {item.item_key: item for item in assignment.items}
    items["passport"].status = CandidateDocumentStatus.RECEIVED
    db_session.commit()
    _claim(db_session, row)
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "accepted"
    assert len(calls) == 1
    assert "Паспорт" not in calls[0] and "— СНИЛС" in calls[0] and "— Фото" in calls[0]

    # Everything received → skipped without a provider call.
    response = channels_app.post(
        f"/candidates/{candidate.id}/documents/messages",
        json={"message_type": "document_reminder", "idempotency_key": "late-0002"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    row2 = db_session.get(NotificationOutbox, UUID(response.json()["messages"][0]["id"]))
    assert row2 is not None
    for item in db_session.execute(
        select(CandidateDocumentItem).where(CandidateDocumentItem.assignment_id == assignment.id)
    ).scalars():
        item.status = CandidateDocumentStatus.RECEIVED
    db_session.commit()
    _claim(db_session, row2)
    assert process_external_row(db_session, row2.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row2)
    assert row2.status == DeliveryStatus.SKIPPED
    assert row2.error_class == "documents_complete"
    assert len(calls) == 1

    # A replaced assignment also stops the old message.
    for item in db_session.execute(
        select(CandidateDocumentItem).where(CandidateDocumentItem.assignment_id == assignment.id)
    ).scalars():
        item.status = CandidateDocumentStatus.MISSING
    db_session.commit()
    response = channels_app.post(
        f"/candidates/{candidate.id}/documents/messages",
        json={"message_type": "document_reminder", "idempotency_key": "late-0003"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    row3 = db_session.get(NotificationOutbox, UUID(response.json()["messages"][0]["id"]))
    assert row3 is not None
    assignment.replaced_at = NOW
    db_session.commit()
    _claim(db_session, row3)
    assert process_external_row(db_session, row3.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row3)
    assert row3.error_class == "assignment_replaced"
    assert len(calls) == 1


def test_worker_skips_rule_row_when_rule_disabled_or_scope_lost(
    channels_app: TestClient,
    db_session: Session,
    hr_user: User,
    published: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = make_candidate(db_session, owner=hr_user, email="scope@example.com")
    _allow_email(db_session, candidate)
    csrf = _login(channels_app, "hr1")
    _apply_directly(db_session, candidate, published, assigned_at=NOW)
    rule = _create_rule(channels_app, csrf)
    _set_stage(channels_app, csrf, candidate, "offer")
    row = _outbox(db_session, candidate)[0]
    calls: list = []

    def fake_send(config, *, to_address, subject, text_body):  # type: ignore[no-untyped-def]
        calls.append(text_body)
        return SmtpSendResult(outcome="accepted", provider_message_id="smtp-1")

    monkeypatch.setattr(worker_module, "_send_email_impl", fake_send)
    # Claimed by the worker BEFORE the rule is disabled (the eager cancel
    # only touches queued rows) → the send-time check stops it.
    _claim(db_session, row)
    rule_row = db_session.get(AutomationRule, UUID(rule["id"]))
    assert rule_row is not None
    rule_row.is_enabled = False
    db_session.commit()
    assert process_external_row(db_session, row.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row)
    assert row.error_class == "rule_inactive" and calls == []

    # Ownership moved away from the rule owner → out of scope at send time.
    rule_row.is_enabled = True
    other = make_user(db_session, username="hr2", role=UserRole.HR)
    candidate.owner_user_id = other.id
    db_session.commit()
    _set_stage(channels_app, csrf, candidate, "interview_scheduled")
    row2 = NotificationOutbox(
        recipient_candidate_id=candidate.id,
        channel=DeliveryChannel.EMAIL,
        notification_type=NotificationType.CANDIDATE_DOCUMENT_REQUEST,
        source=NotificationSource.RULE,
        title="Запрос документов",
        body="x",
        idempotency_key=f"t:{uuid4().hex}",
        template="candidate_document_request",
        template_version=1,
        rule_id=rule_row.id,
        initiator_user_id=hr_user.id,
        status=DeliveryStatus.SENDING,
        started_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=2),
        queued_at=NOW,
    )
    db_session.add(row2)
    db_session.commit()
    assert process_external_row(db_session, row2.id, settings=_settings(), now=NOW) == "skipped"
    db_session.refresh(row2)
    assert row2.error_class == "rule_inactive" and calls == []
