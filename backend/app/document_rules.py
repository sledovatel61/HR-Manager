"""Durable rule discovery and execution in the EXISTING PostgreSQL outbox.

The analytics ledger commits with the stage transition. A bounded anti-join
scanner discovers unprocessed facts (no volatile callbacks/cursors). Scheduled
rules discover exact candidate assignments once, at assignment time + days.
Unique outbox keys arbitrate two scanners. Each job uses existing lease/retry;
action, message enqueue and immutable execution result commit atomically.
"""

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.document_schemas import RuleParams
from app.documents import (
    advisory,
    apply_version,
    can_access,
    candidate_for,
    current_set,
    has_grant,
    queue_documents,
)
from app.models import (
    AccessGrantScope,
    AnalyticsFact,
    AnalyticsFactType,
    Candidate,
    CandidateDocumentSet,
    DeliveryChannel,
    DeliveryStatus,
    DocumentListVersion,
    DocumentRule,
    DocumentRuleExecution,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationSource,
    NotificationType,
    User,
    UserRole,
)
from app.notification_service import schedule


def scan_document_rules(db: Session, *, settings: Settings, now: datetime, batch_size: int) -> int:
    count = 0
    rules = db.scalars(
        select(DocumentRule).where(DocumentRule.enabled.is_(True)).order_by(DocumentRule.id)
    ).all()
    for rule in rules:
        advisory(db, f"document-rule:{rule.id}")
        db.refresh(rule)
        if not rule.enabled:
            db.commit()
            continue
        owner = db.get(User, rule.owner_id, populate_existing=True)
        if owner is None or not owner.is_active:
            db.commit()
            continue
        global_scope = has_grant(db, owner, AccessGrantScope.CANDIDATE_DOCUMENTS_ALL)
        if owner.role == UserRole.ADMIN and not global_scope:
            db.commit()
            continue
        params = RuleParams.model_validate(rule.params)
        source = AnalyticsFact if params.trigger == "stage_transition" else CandidateDocumentSet
        handled = exists().where(
            NotificationOutbox.rule_id == rule.id,
            NotificationOutbox.template_version == rule.version,
            NotificationOutbox.object_type == "document_rule_job",
            NotificationOutbox.object_id == source.id,
        )
        query = (
            select(source)
            .join(Candidate, Candidate.id == source.candidate_id)
            .where(~handled, Candidate.deleted_at.is_(None))
        )
        if not global_scope:
            query = query.where(Candidate.owner_user_id == owner.id)
        if source == AnalyticsFact:
            query = query.where(
                AnalyticsFact.fact_type == AnalyticsFactType.STAGE_CHANGED,
                AnalyticsFact.stage_to == params.stage.value,
                AnalyticsFact.created_at >= rule.updated_at,
            )
        else:
            query = query.where(
                CandidateDocumentSet.created_at <= now - timedelta(days=params.days or 1)
            )
        for raw_trigger in db.scalars(
            query.order_by(source.created_at, source.id).limit(batch_size)
        ).all():
            trigger = cast(AnalyticsFact | CandidateDocumentSet, raw_trigger)
            trigger_version = trigger.revision if isinstance(trigger, CandidateDocumentSet) else 1
            due = trigger.created_at + timedelta(days=params.days or 0)
            row = schedule(
                db,
                recipient_user_id=rule.owner_id,
                type_=NotificationType.REMINDER_DUE,
                source=NotificationSource.RULE,
                title="Исполнение личного правила",
                object_type="document_rule_job",
                object_id=trigger.id,
                object_version=trigger_version,
                dedupe_key=f"document-rule:{rule.id}:{rule.version}:{trigger.id}",
                scheduled_at=due,
                template="document_rule_job",
                template_version=rule.version,
                initiator_user_id=rule.owner_id,
            )
            if row:
                row.rule_id = rule.id
                row.document_context = {
                    "candidate_id": str(trigger.candidate_id),
                    "params": rule.params,
                }
                count += 1
                db.flush()
        db.commit()
    return count


def execution(db: Session, row: NotificationOutbox, outcome: str) -> None:
    context = row.document_context
    assert context and row.rule_id and row.object_id and row.template_version
    db.add(
        DocumentRuleExecution(
            rule_id=row.rule_id,
            rule_version=row.template_version,
            candidate_id=UUID(context["candidate_id"]),
            trigger_id=row.object_id,
            trigger_version=row.object_version or 1,
            action=context["params"]["action"],
            params=context["params"],
            outcome=outcome,
            dedupe_key=row.idempotency_key,
            outbox_id=row.id,
        )
    )


def perform_job(db: Session, row: NotificationOutbox, *, settings: Settings, now: datetime) -> str:
    """Called inside process_row's bounded retry wrapper, before row locking."""
    assert row.rule_id
    advisory(db, f"document-rule:{row.rule_id}")
    context = row.document_context
    assert context
    user = db.get(User, row.initiator_user_id) if row.initiator_user_id else None
    rule = db.get(DocumentRule, row.rule_id, populate_existing=True)
    candidate = db.get(Candidate, UUID(context["candidate_id"]))
    outcome = "skipped"
    if candidate and user and can_access(db, user, candidate):
        candidate = candidate_for(db, candidate.id, user, mutate=True)
    row = db.scalars(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if row.status != DeliveryStatus.SENDING:
        return row.status.value
    params = RuleParams.model_validate(context["params"])
    if (
        rule
        and rule.enabled
        and rule.version == row.template_version
        and candidate
        and user
        and can_access(db, user, candidate)
        and candidate.stage.value == params.stage.value
    ):
        snapshot = current_set(db, candidate.id)
        if params.action == "apply_list" and snapshot is None:
            version = db.scalar(
                select(DocumentListVersion).where(
                    DocumentListVersion.list_id == params.list_id,
                    DocumentListVersion.state == "published",
                    DocumentListVersion.stage.in_(["", candidate.stage.value]),
                )
            )
            if version:
                apply_version(db, candidate, user, version.id, 0)
                outcome = "applied"
        elif (
            params.action != "apply_list"
            and snapshot
            and snapshot.snapshot["list_id"] == str(params.list_id)
            and (not params.list_version_id or snapshot.list_version_id == params.list_version_id)
        ):
            # A scheduled occurrence belongs to the precise assignment, not a replacement.
            if params.trigger != "scheduled_reminder" or snapshot.id == row.object_id:
                assert params.channel
                message = queue_documents(
                    db,
                    candidate=candidate,
                    user=user,
                    snapshot=snapshot,
                    message_type=params.action,
                    channel=DeliveryChannel(params.channel),
                    dedupe_key=f"rule-action:{row.id}",
                    settings=settings,
                    rule=rule,
                )
                if message:
                    outcome = "queued"
    execution(db, row, outcome)
    row.status = DeliveryStatus.DELIVERED  # internal job, NEVER an external delivery claim
    row.delivered_at = now
    row.lease_expires_at = None
    row.attempts += 1
    db.add(
        NotificationDeliveryAttempt(
            outbox_id=row.id,
            attempt_no=row.attempts,
            started_at=row.started_at or now,
            finished_at=now,
            outcome="delivered",
        )
    )
    db.commit()
    return row.status.value


def validate_rule_access(db: Session, user: User, params: RuleParams) -> None:
    from app.documents import has_grant
    from app.models import AccessGrantScope, UserRole

    if user.role == UserRole.ADMIN and not has_grant(
        db, user, AccessGrantScope.CANDIDATE_DOCUMENTS_ALL
    ):
        raise HTTPException(403, "Администрирование не даёт права создавать кандидатские правила.")
    version = db.scalar(
        select(DocumentListVersion).where(
            DocumentListVersion.list_id == params.list_id, DocumentListVersion.state == "published"
        )
    )
    if version is None or version.stage not in ("", params.stage.value):
        raise HTTPException(422, "Список не опубликован или не соответствует этапу.")
    if params.list_version_id and version.id != params.list_version_id:
        raise HTTPException(422, "Выберите текущую опубликованную версию.")
