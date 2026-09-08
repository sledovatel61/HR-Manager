"""Personal rules only. Disabling cancels unstarted work; nothing is deleted."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.document_rules import execution, validate_rule_access
from app.document_schemas import ExecutionOut, RuleInput, RuleOut, RuleUpdate
from app.documents import advisory, audit, can_access
from app.models import (
    Candidate,
    DeliveryStatus,
    DocumentRule,
    DocumentRuleExecution,
    NotificationOutbox,
    User,
)
from app.utils import utc_now

router = APIRouter(prefix="/document-rules", tags=["document-rules"])


def own(db: Session, rule_id: UUID, user: User) -> DocumentRule:
    row = db.get(DocumentRule, rule_id, populate_existing=True)
    if row is None or row.owner_id != user.id:
        raise HTTPException(404, "Правило не найдено.")
    return row


@router.get("", response_model=list[RuleOut])
def rules(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[RuleOut]:
    return [
        RuleOut.model_validate(row)
        for row in db.scalars(
            select(DocumentRule)
            .where(DocumentRule.owner_id == user.id)
            .order_by(DocumentRule.created_at.desc(), DocumentRule.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.post("", response_model=RuleOut, status_code=201)
def create(
    payload: RuleInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> RuleOut:
    validate_rule_access(db, user, payload.params)
    row = DocumentRule(owner_id=user.id, **payload.model_dump(mode="json"))
    db.add(row)
    db.flush()
    audit(db, user, f"create rule={row.id} version={row.version}", rule=True)
    db.commit()
    return RuleOut.model_validate(row)


@router.put("/{rule_id}", response_model=RuleOut)
def update(
    rule_id: UUID,
    payload: RuleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RuleOut:
    advisory(db, f"document-rule:{rule_id}")
    row = own(db, rule_id, user)
    if row.version != payload.expected_version:
        raise HTTPException(409, "Правило изменилось. Обновите данные.")
    if payload.enabled:
        validate_rule_access(db, user, payload.params)
    # Any edit invalidates pending work of the previous version. Advisory lock
    # also spans provider I/O, so a disable cannot overtake an in-flight send.
    for job in db.scalars(
        select(NotificationOutbox)
        .where(
            NotificationOutbox.rule_id == row.id,
            NotificationOutbox.status.in_([DeliveryStatus.QUEUED, DeliveryStatus.SENDING]),
        )
        .with_for_update()
    ):
        if job.object_type == "document_rule_job":
            execution(db, job, "cancelled")
        job.status = DeliveryStatus.CANCELLED
        job.cancelled_at = utc_now()
        job.lease_expires_at = None
    row.name, row.enabled, row.params = (
        payload.name,
        payload.enabled,
        payload.params.model_dump(mode="json"),
    )
    row.version += 1
    row.updated_at = utc_now()
    audit(db, user, f"update rule={row.id} version={row.version} enabled={row.enabled}", rule=True)
    db.commit()
    return RuleOut.model_validate(row)


@router.get("/{rule_id}/history", response_model=list[ExecutionOut])
def history(
    rule_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[ExecutionOut]:
    own(db, rule_id, user)
    rows = db.scalars(
        select(DocumentRuleExecution)
        .where(DocumentRuleExecution.rule_id == rule_id)
        .order_by(DocumentRuleExecution.created_at.desc(), DocumentRuleExecution.id)
        .limit(limit)
        .offset(offset)
    )
    # Historical membership never confers CURRENT candidate access.
    result = []
    for row in rows:
        candidate = db.get(Candidate, row.candidate_id)
        if candidate and can_access(db, user, candidate):
            result.append(ExecutionOut.model_validate(row))
    return result
