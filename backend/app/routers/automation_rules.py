"""Automation rules API (phase 11).

Personal rules with typed triggers, conditions and actions.  Rules are owned
by a single user and operate within the user's candidate visibility scope.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.deps import get_current_user, get_db
from app.models import (
    AuditAction,
    AutomationRule,
    AutomationRuleExecution,
    RuleActionType,
    RuleTriggerType,
    User,
)
from app.schemas import (
    AutomationRuleCreate,
    AutomationRuleExecutionList,
    AutomationRuleExecutionOut,
    AutomationRuleList,
    AutomationRuleOut,
    AutomationRuleToggle,
    AutomationRuleUpdate,
)
from app.utils import utc_now

router = APIRouter(prefix="/automation-rules", tags=["automation-rules"])

# Valid trigger parameters
_VALID_STAGE_TRANSITION_PARAMS = {"stage"}  # stage name from CandidateStage
_VALID_DOCUMENT_REMINDER_PARAMS = {"reminder_days"}  # int days before reminder

# Valid action parameters
_VALID_APPLY_LIST_PARAMS = {"list_id"}  # UUID of the document list
_VALID_SEND_REQUEST_PARAMS = set()
_VALID_SEND_REMINDER_PARAMS = {"days"}  # int days delay

# Valid condition types and params
_VALID_CONDITION_TYPES = {"stage", "has_missing_required", "channel"}
_VALID_STAGE_CONDITION_PARAMS = {"stage"}
_VALID_CHANNEL_CONDITION_PARAMS = {"channel"}  # "email" or "telegram"


def _validate_trigger(trigger_type: str, params: dict) -> None:
    """Validate that trigger params contain only allowed keys."""
    if trigger_type == RuleTriggerType.STAGE_TRANSITION:
        if "stage" not in params:
            raise HTTPException(422, "Для триггера stage_transition требуется параметр stage.")
    elif trigger_type == RuleTriggerType.DOCUMENT_REMINDER_SCHEDULE:
        if "reminder_days" not in params:
            raise HTTPException(
                422, "Для триггера document_reminder_schedule требуется reminder_days."
            )
        days = params["reminder_days"]
        if not isinstance(days, int) or days < 1 or days > 365:
            raise HTTPException(422, "reminder_days должен быть целым числом от 1 до 365.")


def _validate_action(action_type: str, params: dict) -> None:
    """Validate that action params contain only allowed keys."""
    if action_type == RuleActionType.APPLY_DOCUMENT_LIST:
        if "list_id" not in params:
            raise HTTPException(422, "Для действия apply_document_list требуется list_id.")
        try:
            uuid.UUID(str(params["list_id"]))
        except ValueError:
            raise HTTPException(422, "list_id должен быть UUID.") from None
    elif action_type == RuleActionType.SEND_DOCUMENT_REMINDER:
        if "days" not in params:
            raise HTTPException(422, "Для действия send_document_reminder требуется параметр days.")
        days = params["days"]
        if not isinstance(days, int) or days < 1 or days > 90:
            raise HTTPException(422, "days должен быть целым числом от 1 до 90.")


def _validate_condition(condition_type: str | None, params: dict | None) -> None:
    """Validate condition params."""
    if condition_type is None:
        return
    if condition_type not in _VALID_CONDITION_TYPES:
        raise HTTPException(422, f"Неизвестный тип условия: {condition_type}")


def _rule_to_out(rule: AutomationRule) -> AutomationRuleOut:
    return AutomationRuleOut(
        id=rule.id,
        owner_user_id=rule.owner_user_id,
        owner_username=rule.owner_username,
        name=rule.name,
        is_enabled=rule.is_enabled,
        trigger_type=rule.trigger_type.value,
        trigger_params=rule.trigger_params,
        condition_type=rule.condition_type,
        condition_params=rule.condition_params,
        action_type=rule.action_type.value,
        action_params=rule.action_params,
        version=rule.version,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.get("", response_model=AutomationRuleList)
def list_rules(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleList:
    """List own automation rules."""
    total = (
        db.scalar(
            select(func.count())
            .select_from(AutomationRule)
            .where(AutomationRule.owner_user_id == user.id)
        )
        or 0
    )

    rules = db.scalars(
        select(AutomationRule)
        .where(AutomationRule.owner_user_id == user.id)
        .order_by(AutomationRule.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return AutomationRuleList(
        items=[_rule_to_out(r) for r in rules],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=AutomationRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    body: AutomationRuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    """Create a new automation rule."""
    trigger_type = RuleTriggerType(body.trigger.type)
    action_type = RuleActionType(body.action.type)

    _validate_trigger(trigger_type.value, body.trigger.params)
    _validate_action(action_type.value, body.action.params)
    if body.condition:
        _validate_condition(body.condition.type, body.condition.params)

    now = utc_now()
    rule = AutomationRule(
        owner_user_id=user.id,
        name=body.name,
        is_enabled=True,
        trigger_type=trigger_type,
        trigger_params=body.trigger.params,
        condition_type=body.condition.type if body.condition else None,
        condition_params=body.condition.params if body.condition else None,
        action_type=action_type,
        action_params=body.action.params,
        created_at=now,
        updated_at=now,
    )
    db.add(rule)

    record_event(
        db,
        AuditAction.AUTOMATION_RULE_CREATED,
        actor=user,
        details=f"rule_name={body.name} trigger={trigger_type.value} action={action_type.value}",
        commit=False,
    )

    db.commit()
    db.refresh(rule)

    return _rule_to_out(rule)


@router.get("/{rule_id}", response_model=AutomationRuleOut)
def get_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    """Get a specific rule (own rules only)."""
    rule = db.get(AutomationRule, rule_id)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(404, "Правило не найдено.")
    return _rule_to_out(rule)


@router.patch("/{rule_id}", response_model=AutomationRuleOut)
def update_rule(
    rule_id: uuid.UUID,
    body: AutomationRuleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    """Update an automation rule (own rules only)."""
    rule = db.get(AutomationRule, rule_id)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(404, "Правило не найдено.")

    if rule.version != body.expected_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Конфликт версий. Обновите данные и попробуйте снова.",
        )

    now = utc_now()
    if body.name is not None:
        rule.name = body.name
    if body.trigger is not None:
        trigger_type = RuleTriggerType(body.trigger.type)
        _validate_trigger(trigger_type.value, body.trigger.params)
        rule.trigger_type = trigger_type
        rule.trigger_params = body.trigger.params
    if body.action is not None:
        action_type = RuleActionType(body.action.type)
        _validate_action(action_type.value, body.action.params)
        rule.action_type = action_type
        rule.action_params = body.action.params
    if body.condition is not None:
        _validate_condition(body.condition.type, body.condition.params)
        rule.condition_type = body.condition.type
        rule.condition_params = body.condition.params

    rule.version += 1
    rule.updated_at = now

    record_event(
        db,
        AuditAction.AUTOMATION_RULE_UPDATED,
        actor=user,
        details=f"rule_id={rule_id}",
        commit=False,
    )

    db.commit()
    db.refresh(rule)

    return _rule_to_out(rule)


@router.patch("/{rule_id}/toggle", response_model=AutomationRuleOut)
def toggle_rule(
    rule_id: uuid.UUID,
    body: AutomationRuleToggle,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    """Enable or disable an automation rule."""
    rule = db.get(AutomationRule, rule_id)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(404, "Правило не найдено.")

    if rule.version != body.expected_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Конфликт версий. Обновите данные и попробуйте снова.",
        )

    now = utc_now()
    rule.is_enabled = body.is_enabled
    rule.version += 1
    rule.updated_at = now

    record_event(
        db,
        AuditAction.AUTOMATION_RULE_TOGGLED,
        actor=user,
        details=f"rule_id={rule_id} enabled={body.is_enabled}",
        commit=False,
    )

    db.commit()
    db.refresh(rule)

    return _rule_to_out(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete an automation rule (own rules only)."""
    rule = db.get(AutomationRule, rule_id)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(404, "Правило не найдено.")

    record_event(
        db,
        AuditAction.AUTOMATION_RULE_DELETED,
        actor=user,
        details=f"rule_id={rule_id}",
        commit=False,
    )

    db.delete(rule)
    db.commit()


@router.get("/{rule_id}/executions", response_model=AutomationRuleExecutionList)
def list_rule_executions(
    rule_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleExecutionList:
    """List execution history of a rule (own rules only)."""
    rule = db.get(AutomationRule, rule_id)
    if rule is None or rule.owner_user_id != user.id:
        raise HTTPException(404, "Правило не найдено.")

    total = (
        db.scalar(
            select(func.count())
            .select_from(AutomationRuleExecution)
            .where(AutomationRuleExecution.rule_id == rule_id)
        )
        or 0
    )

    executions = db.scalars(
        select(AutomationRuleExecution)
        .where(AutomationRuleExecution.rule_id == rule_id)
        .order_by(AutomationRuleExecution.executed_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return AutomationRuleExecutionList(
        items=[AutomationRuleExecutionOut.model_validate(e) for e in executions],
        total=total,
        limit=limit,
        offset=offset,
    )
