"""Personal automation rules API (phase 11) — ``/automation-rules``.

* every rule belongs to its creator; a user sees and edits ONLY their own
  rules (a foreign rule is 404 — no existence leak, no ownership transfer);
* creating a rule requires the candidate scope (HR / manager / admin with
  an active pilot grant); without the scope the endpoint answers 403 and
  existing rules simply never execute (the engine re-checks the CURRENT
  scope at execution time);
* rule parameters are validated against closed typed schemas — no
  free-form text, code, recipients or URLs; a referenced document list
  must be published;
* edit/enable/disable use optimistic concurrency (``expected_version``);
  disabling cancels the rule's not-started jobs; deletion is soft (the
  immutable execution history keeps its reference).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import user_has_candidate_scope
from app.audit import record_event
from app.automation_rules import cancel_pending_rule_jobs
from app.db import get_db
from app.deps import get_current_user
from app.document_lists import published_version_for
from app.models import (
    AuditAction,
    AutomationActionType,
    AutomationRule,
    AutomationRuleExecution,
    AutomationTriggerType,
    CandidateStage,
    DeliveryChannel,
    User,
)
from app.schemas import (
    ALLOWED_TRIGGER_ACTIONS,
    MAX_REMINDER_DELAY_DAYS,
    AutomationRuleCreate,
    AutomationRuleExecutionList,
    AutomationRuleExecutionOut,
    AutomationRuleList,
    AutomationRuleOut,
    AutomationRuleToggleRequest,
    AutomationRuleUpdate,
    AutomationVocabularyOut,
    validate_rule_params,
)
from app.utils import client_ip, user_agent, utc_now

router = APIRouter(prefix="/automation-rules", tags=["automation-rules"])

_MAX_RULES_PER_USER = 50
_DEFAULT_EXECUTIONS_LIMIT = 20
_MAX_EXECUTIONS_LIMIT = 100


# --- Helpers ---------------------------------------------------------------------


def _require_scope(db: Session, user: User) -> None:
    if not user_has_candidate_scope(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Правила работают с кандидатами и доступны только в рамках вашего доступа "
                "к кандидатам. Администратору нужно отдельное правило доступа "
                "(пилотный полный доступ)."
            ),
        )


def _own_rule_or_404(
    db: Session, rule_id: str, user: User, *, for_update: bool = False
) -> AutomationRule:
    try:
        parsed = UUID(rule_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Правило не найдено."
        ) from None
    stmt = select(AutomationRule).where(
        AutomationRule.id == parsed,
        AutomationRule.owner_user_id == user.id,
        AutomationRule.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    rule = db.execute(stmt).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правило не найдено.")
    return rule


def _validated_params(
    db: Session,
    *,
    trigger_type: str,
    trigger_params: dict,
    conditions: dict,
    action_type: str,
    action_params: dict,
) -> tuple[dict, dict, dict]:
    try:
        trigger, cond, action = validate_rule_params(
            trigger_type=trigger_type,
            trigger_params=trigger_params,
            conditions=conditions,
            action_type=action_type,
            action_params=action_params,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Некорректные параметры правила: {_safe_error(exc)}",
        ) from None
    for source in (action, cond):
        list_id = source.get("list_id")
        if list_id is not None and published_version_for(db, UUID(str(list_id))) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Указанный список документов не опубликован.",
            )
    return trigger, cond, action


def _safe_error(exc: ValueError) -> str:
    """A short human-readable reason without echoing the payload."""
    text = str(exc)
    if "validation error" in text:
        first = text.splitlines()
        # pydantic: "1 validation error for X\nfield\n  message [type=..., input_value=...]"
        for line in first[2:3]:
            return line.strip().split(" [")[0][:200]
        return "проверьте поля триггера, условий и действия"
    return text[:200]


def _audit(
    db: Session, request: Request, action: AuditAction, *, actor: User, details: str
) -> None:
    record_event(
        db,
        action,
        actor=actor,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=False,
    )


def _conflict_version(expected: int, actual: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Правило уже изменено (ожидалась версия {expected}, актуальная — {actual}). "
            "Обновите данные и повторите."
        ),
    )


def _out(rule: AutomationRule) -> AutomationRuleOut:
    return AutomationRuleOut(
        id=rule.id,
        owner_user_id=rule.owner_user_id,
        name=rule.name,
        is_enabled=rule.is_enabled,
        trigger_type=rule.trigger_type.value,
        trigger_params=dict(rule.trigger_params or {}),
        conditions=dict(rule.conditions or {}),
        action_type=rule.action_type.value,
        action_params=dict(rule.action_params or {}),
        version=rule.version,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


# --- Vocabulary ---------------------------------------------------------------------


@router.get(
    "/vocabulary",
    response_model=AutomationVocabularyOut,
    summary="Closed vocabularies for the rule editor",
)
def get_vocabulary(_user: User = Depends(get_current_user)) -> AutomationVocabularyOut:
    return AutomationVocabularyOut(
        triggers=[member.value for member in AutomationTriggerType],
        actions=[member.value for member in AutomationActionType],
        trigger_actions={
            trigger: sorted(actions) for trigger, actions in ALLOWED_TRIGGER_ACTIONS.items()
        },
        channels=[DeliveryChannel.EMAIL.value, DeliveryChannel.TELEGRAM.value],
        stages=[member.value for member in CandidateStage],
        max_delay_days=MAX_REMINDER_DELAY_DAYS,
    )


# --- CRUD -----------------------------------------------------------------------


@router.get("", response_model=AutomationRuleList, summary="My rules")
def list_rules(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleList:
    rules = list(
        db.execute(
            select(AutomationRule)
            .where(AutomationRule.owner_user_id == user.id, AutomationRule.deleted_at.is_(None))
            .order_by(AutomationRule.created_at.desc(), AutomationRule.id)
        )
        .scalars()
        .all()
    )
    return AutomationRuleList(items=[_out(rule) for rule in rules], total=len(rules))


@router.post(
    "",
    response_model=AutomationRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a rule",
)
def create_rule(
    payload: AutomationRuleCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    _require_scope(db, user)
    trigger, conditions, action = _validated_params(
        db,
        trigger_type=payload.trigger_type,
        trigger_params=payload.trigger_params,
        conditions=payload.conditions,
        action_type=payload.action_type,
        action_params=payload.action_params,
    )
    count = db.execute(
        select(func.count())
        .select_from(AutomationRule)
        .where(AutomationRule.owner_user_id == user.id, AutomationRule.deleted_at.is_(None))
    ).scalar_one()
    if count >= _MAX_RULES_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Достигнут предел правил ({_MAX_RULES_PER_USER}). Удалите неиспользуемые.",
        )
    now = utc_now()
    rule = AutomationRule(
        owner_user_id=user.id,
        name=payload.name,
        is_enabled=payload.is_enabled,
        trigger_type=AutomationTriggerType(payload.trigger_type),
        trigger_params=trigger,
        conditions=conditions,
        action_type=AutomationActionType(payload.action_type),
        action_params=action,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    db.flush()
    _audit(
        db,
        request,
        AuditAction.AUTOMATION_RULE_CREATED,
        actor=user,
        details=(
            f"rule={rule.id} trigger={payload.trigger_type} action={payload.action_type} "
            f"enabled={payload.is_enabled}"
        ),
    )
    db.commit()
    db.refresh(rule)
    return _out(rule)


@router.get("/{rule_id}", response_model=AutomationRuleOut, summary="One of my rules")
def get_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    return _out(_own_rule_or_404(db, rule_id, user))


@router.put("/{rule_id}", response_model=AutomationRuleOut, summary="Edit a rule")
def update_rule(
    rule_id: str,
    payload: AutomationRuleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    _require_scope(db, user)
    rule = _own_rule_or_404(db, rule_id, user, for_update=True)
    if rule.version != payload.expected_version:
        raise _conflict_version(payload.expected_version, rule.version)
    trigger, conditions, action = _validated_params(
        db,
        trigger_type=payload.trigger_type,
        trigger_params=payload.trigger_params,
        conditions=payload.conditions,
        action_type=payload.action_type,
        action_params=payload.action_params,
    )
    rule.name = payload.name
    rule.trigger_type = AutomationTriggerType(payload.trigger_type)
    rule.trigger_params = trigger
    rule.conditions = conditions
    rule.action_type = AutomationActionType(payload.action_type)
    rule.action_params = action
    rule.version += 1
    rule.updated_at = utc_now()
    # An edited rule is a new logical rule for pending jobs: the old plan
    # (built from the old parameters) must never fire.
    cancelled = cancel_pending_rule_jobs(db, rule_id=rule.id)
    _audit(
        db,
        request,
        AuditAction.AUTOMATION_RULE_UPDATED,
        actor=user,
        details=(
            f"rule={rule.id} version={rule.version} trigger={payload.trigger_type} "
            f"action={payload.action_type} cancelled_jobs={cancelled}"
        ),
    )
    db.commit()
    db.refresh(rule)
    return _out(rule)


@router.post(
    "/{rule_id}/toggle",
    response_model=AutomationRuleOut,
    summary="Enable or disable a rule",
)
def toggle_rule(
    rule_id: str,
    payload: AutomationRuleToggleRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleOut:
    rule = _own_rule_or_404(db, rule_id, user, for_update=True)
    if rule.version != payload.expected_version:
        raise _conflict_version(payload.expected_version, rule.version)
    if payload.is_enabled:
        _require_scope(db, user)
    if rule.is_enabled == payload.is_enabled:
        db.rollback()
        return _out(rule)
    rule.is_enabled = payload.is_enabled
    rule.version += 1
    rule.updated_at = utc_now()
    cancelled = 0
    if not payload.is_enabled:
        cancelled = cancel_pending_rule_jobs(db, rule_id=rule.id)
    _audit(
        db,
        request,
        AuditAction.AUTOMATION_RULE_ENABLED
        if payload.is_enabled
        else AuditAction.AUTOMATION_RULE_DISABLED,
        actor=user,
        details=f"rule={rule.id} version={rule.version} cancelled_jobs={cancelled}",
    )
    db.commit()
    db.refresh(rule)
    return _out(rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a rule (history is kept)",
)
def delete_rule(
    rule_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    rule = _own_rule_or_404(db, rule_id, user, for_update=True)
    now = utc_now()
    rule.deleted_at = now
    rule.is_enabled = False
    rule.version += 1
    rule.updated_at = now
    cancelled = cancel_pending_rule_jobs(db, rule_id=rule.id, now=now)
    _audit(
        db,
        request,
        AuditAction.AUTOMATION_RULE_DELETED,
        actor=user,
        details=f"rule={rule.id} cancelled_jobs={cancelled}",
    )
    db.commit()


# --- Execution history --------------------------------------------------------------


@router.get(
    "/{rule_id}/executions",
    response_model=AutomationRuleExecutionList,
    summary="Recent executions of my rule (immutable history)",
)
def list_executions(
    rule_id: str,
    limit: int = Query(default=_DEFAULT_EXECUTIONS_LIMIT, ge=1, le=_MAX_EXECUTIONS_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRuleExecutionList:
    rule = _own_rule_or_404(db, rule_id, user)
    total = db.execute(
        select(func.count())
        .select_from(AutomationRuleExecution)
        .where(AutomationRuleExecution.rule_id == rule.id)
    ).scalar_one()
    rows = list(
        db.execute(
            select(AutomationRuleExecution)
            .where(AutomationRuleExecution.rule_id == rule.id)
            .order_by(AutomationRuleExecution.executed_at.desc(), AutomationRuleExecution.id)
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return AutomationRuleExecutionList(
        items=[
            AutomationRuleExecutionOut(
                id=row.id,
                rule_id=row.rule_id,
                rule_version=row.rule_version,
                trigger_type=row.trigger_type,
                trigger_object_type=row.trigger_object_type,
                trigger_object_id=row.trigger_object_id,
                trigger_object_version=row.trigger_object_version,
                candidate_id=row.candidate_id,
                action_type=row.action_type,
                outcome=row.outcome.value,
                outcome_class=row.outcome_class,
                dedupe_key=row.dedupe_key,
                list_id=row.list_id,
                list_version_id=row.list_version_id,
                executed_at=row.executed_at,
            )
            for row in rows
        ],
        total=int(total),
    )
