"""Document operations. All writes join the caller's transaction.

Candidate mutation advisory lock serializes snapshots/status changes with the
Phase 10 provider call. Parent row locks serialize revision allocation. Global
scope publication lock also covers the *empty* scope (no row to lock yet).
"""

import hashlib
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import record_event
from app.candidate_messages import (
    allowed_candidate_channels,
    queue_candidate_message,
    render_candidate_message,
)
from app.config import Settings
from app.document_schemas import CandidateDocumentsOut, CandidateItemOut, VersionOut
from app.models import (
    AccessGrant,
    AccessGrantScope,
    AuditAction,
    Candidate,
    CandidateDocumentItem,
    CandidateDocumentSet,
    DeliveryChannel,
    DocumentListVersion,
    DocumentRule,
    NotificationOutbox,
    NotificationSource,
    User,
    UserRole,
)
from app.notification_service import lock_candidate_for_mutation
from app.utils import utc_now


def has_grant(db: Session, user: User, scope: AccessGrantScope) -> bool:
    return (
        db.scalar(
            select(AccessGrant.id).where(
                AccessGrant.user_id == user.id,
                AccessGrant.scope.in_([scope, AccessGrantScope.PILOT_FULL_ACCESS]),
                AccessGrant.revoked_at.is_(None),
            )
        )
        is not None
    )


def can_manage(db: Session, user: User) -> bool:
    user = db.get(User, user.id, populate_existing=True) or user
    return user.is_active and (
        user.role == UserRole.ADMIN or has_grant(db, user, AccessGrantScope.DOCUMENT_LISTS_MANAGE)
    )


def can_access(db: Session, user: User | None, candidate: Candidate) -> bool:
    # A request/job may have waited for a candidate advisory lock. Do not
    # authorize it with a pre-lock identity-map copy of the owner's role.
    if user is not None:
        user = db.get(User, user.id, populate_existing=True)
    if user is None or not user.is_active or candidate.deleted_at is not None:
        return False
    if has_grant(db, user, AccessGrantScope.CANDIDATE_DOCUMENTS_ALL):
        return True
    return user.role in (UserRole.HR, UserRole.MANAGER) and candidate.owner_user_id == user.id


def candidate_for(
    db: Session, candidate_id: UUID, user: User, *, mutate: bool = False
) -> Candidate:
    if mutate:
        lock_candidate_for_mutation(db, candidate_id)
    query = (
        select(Candidate)
        .where(Candidate.id == candidate_id)
        .execution_options(populate_existing=True)
    )
    if mutate:
        query = query.with_for_update()
    candidate = db.scalar(query)
    if candidate is None or not can_access(db, user, candidate):
        raise HTTPException(404, "Кандидат не найден.")
    return candidate


def advisory(db: Session, key: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})


def audit(
    db: Session, user: User, detail: str, candidate: Candidate | None = None, *, rule: bool = False
) -> None:
    record_event(
        db,
        AuditAction.DOCUMENT_RULE_CHANGED if rule else AuditAction.DOCUMENT_CHANGED,
        actor=user,
        candidate_id=candidate.id if candidate else None,
        details=detail,
        commit=False,
    )


def current_set(db: Session, candidate_id: UUID) -> CandidateDocumentSet | None:
    return db.scalar(
        select(CandidateDocumentSet)
        .where(CandidateDocumentSet.candidate_id == candidate_id)
        .order_by(CandidateDocumentSet.revision.desc())
        .limit(1)
    )


def missing_items(db: Session, snapshot: CandidateDocumentSet) -> list[dict]:
    states = {
        row.key: row.state
        for row in db.scalars(
            select(CandidateDocumentItem)
            .where(CandidateDocumentItem.set_id == snapshot.id)
            .execution_options(populate_existing=True)
        )
    }
    return [
        item
        for item in snapshot.snapshot["items"]
        if item["required"] and states.get(item["key"]) == "missing"
    ]


def documents_out(db: Session, candidate: Candidate) -> CandidateDocumentsOut:
    snapshot = current_set(db, candidate.id)
    if snapshot is None:
        return CandidateDocumentsOut(revision=0)
    states = {
        row.key: row
        for row in db.scalars(
            select(CandidateDocumentItem).where(CandidateDocumentItem.set_id == snapshot.id)
        )
    }
    return CandidateDocumentsOut(
        revision=snapshot.revision,
        set_id=snapshot.id,
        exact_version=VersionOut.model_validate(snapshot.snapshot),
        items=[
            CandidateItemOut(
                **item,
                state=cast(Literal["missing", "received"], states[item["key"]].state),
                version=states[item["key"]].version,
                changed_by=states[item["key"]].changed_by,
                changed_at=states[item["key"]].changed_at,
            )
            for item in snapshot.snapshot["items"]
        ],
        missing_required=[item["key"] for item in missing_items(db, snapshot)],
    )


def apply_version(
    db: Session, candidate: Candidate, user: User, version_id: UUID, expected_revision: int
) -> CandidateDocumentSet:
    previous = current_set(db, candidate.id)
    if (previous.revision if previous else 0) != expected_revision:
        raise HTTPException(409, "Список уже изменён. Обновите данные.")
    version = db.scalar(
        select(DocumentListVersion)
        .where(DocumentListVersion.id == version_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        version is None
        or version.state != "published"
        or version.stage not in ("", candidate.stage.value)
    ):
        raise HTTPException(422, "Опубликованная версия недоступна для этапа кандидата.")
    snapshot = CandidateDocumentSet(
        candidate_id=candidate.id,
        list_version_id=version.id,
        revision=expected_revision + 1,
        snapshot=VersionOut.model_validate(version).model_dump(mode="json"),
        author_id=user.id,
    )
    db.add(snapshot)
    db.flush()
    for item in version.items:
        db.add(CandidateDocumentItem(set_id=snapshot.id, key=item["key"], changed_by=user.id))
    audit(db, user, f"apply set={snapshot.id} version={version.id}", candidate)
    db.flush()
    return snapshot


def queue_documents(
    db: Session,
    *,
    candidate: Candidate,
    user: User,
    snapshot: CandidateDocumentSet,
    message_type: str,
    channel: DeliveryChannel,
    dedupe_key: str,
    settings: Settings,
    rule: DocumentRule | None = None,
) -> NotificationOutbox | None:
    items = missing_items(db, snapshot)
    if not items or channel not in allowed_candidate_channels(
        db, candidate=candidate, settings=settings
    ):
        return None
    message = render_candidate_message(
        message_type, candidate=candidate, documents=[item["name"] for item in items]
    )
    row = queue_candidate_message(
        db,
        candidate=candidate,
        channel=channel,
        message=message,
        message_type_key=message_type,
        source=NotificationSource.RULE if rule else NotificationSource.MANUAL,
        initiator_user_id=user.id,
        dedupe_key=dedupe_key,
        scheduled_at=utc_now(),
    )
    if row:
        row.rule_id = rule.id if rule else None
        row.document_context = {
            "set_id": str(snapshot.id),
            "list_id": snapshot.snapshot["list_id"],
            "version_id": str(snapshot.list_version_id),
            "version_number": snapshot.snapshot["number"],
            "items": items,
            "rule_version": rule.version if rule else None,
        }
        row.object_type = "candidate_documents"
        row.object_id = snapshot.id
        row.object_version = snapshot.revision
        db.flush()
    return row


def revalidate_documents(db: Session, row: NotificationOutbox, candidate: Candidate) -> str | None:
    """Fail closed. Never add new positions to the immutable queued snapshot.

    If any queued document became received, skip the whole stale message. This
    preserves exact immutable text without sending already received positions;
    a new explicit request can render the remaining subset.
    """
    context = row.document_context
    if not context or "set_id" not in context:
        return "documents_stale"
    user = db.get(User, row.initiator_user_id) if row.initiator_user_id else None
    if not can_access(db, user, candidate):
        return "access_revoked"
    if row.rule_id:
        rule = db.get(DocumentRule, row.rule_id, populate_existing=True)
        if rule is None or not rule.enabled or rule.version != context.get("rule_version"):
            return "rule_disabled"
        if rule.params["stage"] != candidate.stage.value:
            return "rule_condition_changed"
    snapshot = current_set(db, candidate.id)
    if snapshot is None or str(snapshot.id) != context["set_id"]:
        return "documents_stale"
    missing = {item["key"] for item in missing_items(db, snapshot)}
    keys = {item["key"] for item in context["items"]}
    if not keys or not keys <= missing:
        return "documents_stale"
    return None


def payload_digest(payload: dict) -> str:
    import json

    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def document_send_time(
    db: Session, row: NotificationOutbox, settings: Settings, now: datetime
) -> datetime:
    """Phase 11 enforces both workdays and quiet hours, including external B0."""
    from app.notification_service import preference_for
    from app.quiet_hours import in_quiet_period, is_workday, next_allowed_time
    from app.worker import _candidate_quiet_preference

    preference = (
        preference_for(db, row.initiator_user_id, settings.notification_default_timezone)
        if row.rule_id and row.initiator_user_id
        else _candidate_quiet_preference(settings)
    )
    at = max(now, row.scheduled_at or now)
    if not preference.workdays:
        raise ValueError("invalid_workdays")
    if is_workday(
        at, timezone=preference.timezone, workdays=preference.workdays
    ) and not in_quiet_period(
        at,
        timezone=preference.timezone,
        quiet_hours_start=preference.quiet_hours_start,
        quiet_hours_end=preference.quiet_hours_end,
    ):
        return at
    return next_allowed_time(
        at,
        timezone=preference.timezone,
        quiet_hours_start=preference.quiet_hours_start,
        quiet_hours_end=preference.quiet_hours_end,
        workdays=preference.workdays,
    )
