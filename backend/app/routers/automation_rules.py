"""Automation rules API (Phase 11).

Personal rules: each user creates, edits and manages their own rules.
Rules are evaluated against the user's accessible candidates only.
Disabling a rule cancels pending outbox jobs of that rule but does not
rewrite immutable execution history.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    AuditAction,
    AutomationRule,
    AutomationRuleExecution,
    DeliveryStatus,
    DocumentList,
    NotificationOutbox,
    User,
)
from app.schemas import (
    AutomationRuleCreate,
    AutomationRuleExecutionList,
    AutomationRuleExecutionOut,
    AutomationRuleList,
    AutomationRuleOut,
    AutomationRuleUpdate,
)
from app.utils import client_ip, utc_now, user_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rules", tags=["automation-rules"])


def _validate_rule_params(payload: AutomationRuleCreate | AutomationRuleUpdate, db: Session) -> None:
    """Cross-validate trigger/action params against closed vocabularies and
    existence of referenced entities."""
    action_type = getattr(payload, "action_type", None)
    action_params = getattr(payload, "action_params", None)
    if action_type == "apply_list" and action_params:
        list_id = action_params.get("list_id")
        if list_id:
            try:
                parsed = UUID(list_id) if isinstance(list_id, str) else list_id
            except ValueError:
                raise HTTPException(
                    status_code=422, detail="Недопустимый идентификатор списка."
                ) from None
            dl = db.get(DocumentList, parsed)
            if dl is None:
                raise HTTPException(status_code=404, detail="Список документов не найден.")

    conditions = getattr(payload, "conditions", None)
    if conditions is not None:
        conds = conditions if isinstance(conditions, dict) else conditions.model_dump()
        list_id = conds.get("list_id")
        if list_id:
            try:
                parsed = UUID(list_id) if isinstance(list_id, str) else list_id
            except ValueError:
                raise HTTPException(
                    status_code=422, detail="Недопустимый идентификатор списка."
                ) from None
            dl = db.get(DocumentList, parsed)
            if dl is None:
                raise HTTPException(status_code=404, detail="Список документов не найден.")


def _rule_to_out(rule: AutomationRule) -> AutomationRuleOut:
    conds = rule.conditions
    if conds is not None and not isinstance(conds, dict):
        conds = dict(conds) if conds else None
    return AutomationRuleOut(
        id=rule.id,
        owner_user_id=rule.owner_user_id,
        title=rule.title,
        enabled=rule.enabled,
        trigger_type=rule.trigger_type.value,
        trigger_params=rule.trigger_params or {},
        conditions=conds,
        action_type=rule.action_type.value,
        action_params=rule.action_params or {},
        version=rule.version,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.get(
    "",
    response_model=AutomationRuleList,
    summary="List own automation rules",
)
def list_rules(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleList:
    rules = list(
        db.scalars(
            select(AutomationRule)
            .where(AutomationRule.owner_user_id == user.id)
            .order_by(AutomationRule.created_at.desc())
        ).all()
    )
    return AutomationRuleList(items=[_rule_to_out(r) for r in rules])


@router.post(
    "",
    response_model=AutomationRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an automation rule",
)
def create_rule(
    payload: AutomationRuleCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    _validate_rule_params(payload, db)
    conds_dict = payload.conditions.model_dump() if payload.conditions else None
    rule = AutomationRule(
        owner_user_id=user.id,
        title=payload.title,
        trigger_type=payload.trigger_type,
        trigger_params=payload.trigger_params,
        conditions=conds_dict,
        action_type=payload.action_type,
        action_params=payload.action_params,
    )
    db.add(rule)
    record_event(
        db,
        AuditAction.AUTOMATION_RULE_CREATED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"rule={rule.id} trigger={payload.trigger_type} action={payload.action_type}",
        commit=False,
    )
    db.commit()
    db.refresh(rule)
    return _rule_to_out(rule)


@router.get("/{rule_id}", response_model=AutomationRuleOut, summary="Get own rule")
def get_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    try:
        parsed = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Правило не найдено.") from None
    rule = db.get(AutomationRule, parsed)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Правило не найдено.")
    return _rule_to_out(rule)


@router.patch("/{rule_id}", response_model=AutomationRuleOut, summary="Update own rule")
def update_rule(
    rule_id: str,
    payload: AutomationRuleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    try:
        parsed = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Правило не найдено.") from None
    rule = db.get(AutomationRule, parsed)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Правило не найдено.")
    if rule.version != payload.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Конфликт версий: ожидали {payload.expected_version}, текущая {rule.version}.",
        )
    _validate_rule_params(payload, db)
    if payload.title is not None:
        rule.title = payload.title
    if payload.trigger_type is not None:
        rule.trigger_type = payload.trigger_type
    if payload.trigger_params is not None:
        rule.trigger_params = payload.trigger_params
    if payload.conditions is not None:
        rule.conditions = payload.conditions.model_dump()
    if payload.action_type is not None:
        rule.action_type = payload.action_type
    if payload.action_params is not None:
        rule.action_params = payload.action_params
    rule.version += 1
    record_event(
        db,
        AuditAction.AUTOMATION_RULE_UPDATED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"rule={rule.id}",
        commit=False,
    )
    db.commit()
    db.refresh(rule)
    return _rule_to_out(rule)


@router.post(
    "/{rule_id}/toggle",
    response_model=AutomationRuleOut,
    summary="Enable or disable own rule",
)
def toggle_rule(
    rule_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    try:
        parsed = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Правило не найдено.") from None
    rule = db.get(AutomationRule, parsed)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Правило не найдено.")
    rule.enabled = not rule.enabled
    rule.version += 1
    # Cancel pending outbox rows for this rule when disabling.
    if not rule.enabled:
        now = utc_now()
        result = db.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.rule_id == rule.id,
                NotificationOutbox.status == DeliveryStatus.QUEUED,
            )
        )
        for row in result.scalars().all():
            row.status = DeliveryStatus.CANCELLED
            row.cancelled_at = now
    record_event(
        db,
        AuditAction.AUTOMATION_RULE_TOGGLED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"rule={rule.id} enabled={rule.enabled}",
        commit=False,
    )
    db.commit()
    db.refresh(rule)
    return _rule_to_out(rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete own rule (immutable execution history preserved)",
)
def delete_rule(
    rule_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    try:
        parsed = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Правило не найдено.") from None
    rule = db.get(AutomationRule, parsed)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Правило не найдено.")
    record_event(
        db,
        AuditAction.AUTOMATION_RULE_DELETED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=f"rule={rule.id}",
        commit=False,
    )
    db.delete(rule)
    db.commit()


@router.get(
    "/{rule_id}/executions",
    response_model=AutomationRuleExecutionList,
    summary="Rule execution history (immutable)",
)
def list_rule_executions(
    rule_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleExecutionList:
    try:
        parsed = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Правило не найдено.") from None
    rule = db.get(AutomationRule, parsed)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Правило не найдено.")
    total = int(
        db.execute(
            select(func.count())
            .select_from(AutomationRuleExecution)
            .where(AutomationRuleExecution.rule_id == parsed)
        ).scalar() or 0
    )
    rows = list(
        db.scalars(
            select(AutomationRuleExecution)
            .where(AutomationRuleExecution.rule_id == parsed)
            .order_by(AutomationRuleExecution.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return AutomationRuleExecutionList(
        items=[
            AutomationRuleExecutionOut(
                id=r.id,
                rule_id=r.rule_id,
                rule_version=r.rule_version,
                trigger_object_type=r.trigger_object_type,
                trigger_object_id=r.trigger_object_id,
                candidate_id=r.candidate_id,
                action_type=r.action_type,
                outcome=r.outcome.value,
                error_class=r.error_class,
                dedupe_key=r.dedupe_key,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
    )
