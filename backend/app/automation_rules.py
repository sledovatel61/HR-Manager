"""Personal automation rules (phase 11) — evaluation and execution.

A rule is NOT a program: a closed trigger, closed conditions and one closed
action with typed parameters (see ``schemas.validate_rule_params``). This
module turns business events into outbox jobs through the existing
phase-10 candidate-message pipeline.

Triggers
--------
* ``stage_entered`` — evaluated synchronously inside the candidate update
  transaction (``routers.candidates.update_candidate``). Each rule's
  evaluation runs in its own SAVEPOINT: a rule failure rolls back only
  that rule's work, is recorded as a ``failed`` execution row and never
  breaks the HTTP operation (documented failure model, see below).
* ``documents_missing_due`` — evaluated by the worker pass
  (``scan_due_rules``): «N days after the list was applied the required
  documents are still missing». Durable and idempotent: the execution
  table is the barrier.

Idempotency / dedupe
--------------------
Every evaluation first inserts an ``automation_rule_executions`` row with a
deterministic dedupe key (rule + trigger object + object version, or
rule + assignment + day). The unique index ``(rule_id, dedupe_key)`` makes
a repeated event, an HTTP retry or two parallel workers collapse into ONE
logical action: the loser sees a duplicate key, rolls back its SAVEPOINT
and does nothing. The outbox idempotency key (derived from the same dedupe
key) is the second, independent barrier.

Scope and rights
----------------
A rule executes only inside the owner's CURRENT candidate scope
(``access.candidate_in_scope``): an inactive owner, a revoked pilot grant
or a candidate outside the scope makes the rule non-executable at once
(recorded as ``skipped`` with a safe class).

Quiet hours / workdays / timezone
---------------------------------
Rule sends are scheduled at the owner's first allowed minute
(``quiet_hours.next_allowed_time`` with the owner's timezone, quiet hours
and workdays); the worker additionally re-applies the effective send time
for candidate rows. Rules can never bypass quiet hours.

Failure model
-------------
* synchronous trigger: SAVEPOINT per rule; on an unexpected exception the
  savepoint is rolled back, a ``failed`` execution row (type name of the
  exception only — no message) is written OUTSIDE the failed savepoint
  and the caller's transaction continues; the HTTP operation succeeds;
* scheduled trigger: each (rule, assignment) pair is processed in its own
  transaction; a failure is rolled back, logged by type and retried on
  the next pass (the dedupe key is per day, so a retry the same day is
  still a single logical action).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access import candidate_in_scope
from app.config import Settings
from app.document_lists import (
    DocumentListError,
    apply_published_list,
    current_assignment,
    has_missing_required,
    published_version_for,
    queue_document_message,
)
from app.models import (
    AutomationActionType,
    AutomationExecutionOutcome,
    AutomationRule,
    AutomationRuleExecution,
    AutomationTriggerType,
    Candidate,
    CandidateDocumentAssignment,
    CandidateDocumentItem,
    CandidateDocumentStatus,
    CandidateStage,
    DeliveryChannel,
    DeliveryStatus,
    DocumentList,
    NotificationOutbox,
    NotificationSource,
    User,
)
from app.notification_service import is_duplicate_key_error, preference_for
from app.quiet_hours import next_allowed_time
from app.utils import ensure_aware, utc_now

logger = logging.getLogger(__name__)

# Safe outcome classes (closed vocabulary, mirrored in the UI).
SKIP_OWNER_INACTIVE = "owner_inactive"
SKIP_OUT_OF_SCOPE = "out_of_scope"
SKIP_CANDIDATE_DELETED = "candidate_deleted"
SKIP_CONDITION_STAGE = "condition_stage"
SKIP_CONDITION_LIST = "condition_list"
SKIP_CONDITION_MISSING = "condition_missing_required"
SKIP_CONDITION_CHANNEL = "condition_channel"
SKIP_ALREADY_APPLIED = "already_applied"
SKIP_LIST_NOT_PUBLISHED = "list_not_published"
SKIP_NO_ASSIGNMENT = "no_assignment"
SKIP_NOTHING_MISSING = "nothing_missing"
SKIP_NO_CHANNEL = "no_allowed_channel"
SKIP_DUPLICATE = "duplicate"
FAIL_INTERNAL = "internal_error"

TRIGGER_OBJECT_CANDIDATE = "candidate"
TRIGGER_OBJECT_ASSIGNMENT = "document_assignment"


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: UUID
    outcome: AutomationExecutionOutcome
    outcome_class: str | None = None


# --- Execution history -----------------------------------------------------------


def _record_execution(
    db: Session,
    *,
    rule: AutomationRule,
    trigger_object_type: str,
    trigger_object_id: UUID | None,
    trigger_object_version: int | None,
    candidate_id: UUID | None,
    outcome: AutomationExecutionOutcome,
    outcome_class: str | None,
    dedupe_key: str,
    outbox_ids: list[str] | None = None,
    list_id: UUID | None = None,
    list_version_id: UUID | None = None,
    now: datetime,
) -> AutomationRuleExecution:
    row = AutomationRuleExecution(
        rule_id=rule.id,
        rule_version=rule.version,
        trigger_type=rule.trigger_type.value,
        trigger_object_type=trigger_object_type,
        trigger_object_id=trigger_object_id,
        trigger_object_version=trigger_object_version,
        candidate_id=candidate_id,
        action_type=rule.action_type.value,
        outcome=outcome,
        outcome_class=outcome_class,
        dedupe_key=dedupe_key,
        outbox_ids=outbox_ids,
        list_id=list_id,
        list_version_id=list_version_id,
        executed_at=now,
    )
    db.add(row)
    db.flush()
    return row


# --- Conditions ------------------------------------------------------------------


def _conditions_hold(
    db: Session,
    *,
    rule: AutomationRule,
    candidate: Candidate,
    assignment: CandidateDocumentAssignment | None,
    settings: Settings,
) -> str | None:
    """Return a skip class when a closed condition fails, else None."""
    conditions = rule.conditions or {}
    stage = conditions.get("stage")
    if stage is not None and candidate.stage.value != stage:
        return SKIP_CONDITION_STAGE
    list_id = conditions.get("list_id")
    if list_id is not None and (assignment is None or str(assignment.list_id) != str(list_id)):
        return SKIP_CONDITION_LIST
    wants_missing = conditions.get("has_missing_required")
    if wants_missing is not None and has_missing_required(assignment) != bool(wants_missing):
        return SKIP_CONDITION_MISSING
    channel = conditions.get("channel")
    if channel is not None:
        from app.candidate_messages import allowed_candidate_channels

        allowed = allowed_candidate_channels(db, candidate=candidate, settings=settings)
        if DeliveryChannel(channel) not in allowed:
            return SKIP_CONDITION_CHANNEL
    return None


# --- Scheduling in the owner's quiet-hours model -----------------------------------------


def owner_allowed_time(
    db: Session, *, owner_user_id: UUID, wanted: datetime, settings: Settings
) -> datetime:
    """First minute at/after ``wanted`` allowed by the OWNER's timezone,
    quiet hours and workdays (rules never bypass quiet hours)."""
    preference = preference_for(db, owner_user_id, settings.notification_default_timezone)
    workdays = [int(day) for day in (preference.workdays or [])]
    return next_allowed_time(
        wanted,
        timezone=preference.timezone,
        quiet_hours_start=preference.quiet_hours_start,
        quiet_hours_end=preference.quiet_hours_end,
        workdays=workdays or None,
    )


def _delay_in_owner_timezone(
    db: Session, *, owner_user_id: UUID, base: datetime, days: int, settings: Settings
) -> datetime:
    """``base`` + N calendar days at the same local wall-clock time of the
    owner's timezone (DST-safe through zoneinfo)."""
    preference = preference_for(db, owner_user_id, settings.notification_default_timezone)
    zone = ZoneInfo(preference.timezone)
    local = ensure_aware(base).astimezone(zone)
    shifted = (local.replace(tzinfo=None) + timedelta(days=days)).replace(tzinfo=zone)
    return shifted.astimezone(ZoneInfo("UTC"))


# --- Actions ---------------------------------------------------------------------


def _run_action(
    db: Session,
    *,
    rule: AutomationRule,
    owner: User,
    candidate: Candidate,
    assignment: CandidateDocumentAssignment | None,
    dedupe_key: str,
    settings: Settings,
    now: datetime,
) -> tuple[AutomationExecutionOutcome, str | None, list[str], UUID | None, UUID | None]:
    """Execute the rule's action; returns (outcome, class, outbox ids, list, version)."""
    params = rule.action_params or {}
    if rule.action_type == AutomationActionType.APPLY_DOCUMENT_LIST:
        list_id = UUID(str(params["list_id"]))
        if assignment is not None:
            # «if the list is not applied yet» — any current list counts:
            # rules never silently replace a candidate's current list.
            return (
                AutomationExecutionOutcome.SKIPPED,
                SKIP_ALREADY_APPLIED,
                [],
                assignment.list_id,
                assignment.version_id,
            )
        document_list = db.get(DocumentList, list_id)
        version = published_version_for(db, list_id) if document_list is not None else None
        if version is None:
            return AutomationExecutionOutcome.SKIPPED, SKIP_LIST_NOT_PUBLISHED, [], list_id, None
        try:
            new_assignment, _ = apply_published_list(
                db,
                candidate=candidate,
                list_id=list_id,
                actor_user_id=None,
                rule_id=rule.id,
                replace=False,
                now=now,
            )
        except DocumentListError as exc:
            if exc.code in ("already_applied", "exists", "conflict"):
                return AutomationExecutionOutcome.SKIPPED, SKIP_ALREADY_APPLIED, [], list_id, None
            if exc.code == "not_published":
                return (
                    AutomationExecutionOutcome.SKIPPED,
                    SKIP_LIST_NOT_PUBLISHED,
                    [],
                    list_id,
                    None,
                )
            raise
        return (
            AutomationExecutionOutcome.APPLIED,
            None,
            [],
            new_assignment.list_id,
            new_assignment.version_id,
        )

    if assignment is None:
        return AutomationExecutionOutcome.SKIPPED, SKIP_NO_ASSIGNMENT, [], None, None
    if rule.action_type == AutomationActionType.SEND_DOCUMENT_REQUEST:
        message_type = "document_request"
        wanted = now
    else:
        message_type = "document_reminder"
        delay = int(params.get("delay_days", 0))
        wanted = (
            _delay_in_owner_timezone(
                db, owner_user_id=owner.id, base=now, days=delay, settings=settings
            )
            if delay > 0
            else now
        )
    channel_value = params.get("channel")
    only_channel = DeliveryChannel(channel_value) if channel_value else None
    scheduled_at = owner_allowed_time(db, owner_user_id=owner.id, wanted=wanted, settings=settings)
    rows, plan = queue_document_message(
        db,
        candidate=candidate,
        assignment=assignment,
        message_type_key=message_type,
        source=NotificationSource.RULE,
        dedupe_key=f"rule:{rule.id}:{dedupe_key}",
        settings=settings,
        scheduled_at=scheduled_at,
        initiator_user_id=owner.id,
        rule_id=rule.id,
        only_channel=only_channel,
    )
    if plan is None:
        return (
            AutomationExecutionOutcome.SKIPPED,
            SKIP_NOTHING_MISSING,
            [],
            assignment.list_id,
            assignment.version_id,
        )
    if not rows:
        return (
            AutomationExecutionOutcome.SKIPPED,
            SKIP_NO_CHANNEL,
            [],
            assignment.list_id,
            assignment.version_id,
        )
    return (
        AutomationExecutionOutcome.QUEUED,
        None,
        [str(row.id) for row in rows],
        assignment.list_id,
        assignment.version_id,
    )


# --- One rule evaluation (inside a SAVEPOINT) -----------------------------------------


def _evaluate_rule(
    db: Session,
    *,
    rule: AutomationRule,
    candidate: Candidate,
    trigger_object_type: str,
    trigger_object_id: UUID,
    trigger_object_version: int | None,
    dedupe_key: str,
    settings: Settings,
    now: datetime,
) -> RuleOutcome:
    """Evaluate one rule for one candidate inside its own SAVEPOINT.

    The execution row is inserted FIRST (the durable dedupe barrier); a
    duplicate key means the same logical event was already handled and
    the savepoint is rolled back untouched.
    """
    try:
        with db.begin_nested():
            execution = _record_execution(
                db,
                rule=rule,
                trigger_object_type=trigger_object_type,
                trigger_object_id=trigger_object_id,
                trigger_object_version=trigger_object_version,
                candidate_id=candidate.id,
                outcome=AutomationExecutionOutcome.SKIPPED,
                outcome_class=None,
                dedupe_key=dedupe_key,
                now=now,
            )
            owner = db.get(User, rule.owner_user_id)
            if owner is None or not owner.is_active:
                execution.outcome_class = SKIP_OWNER_INACTIVE
                return RuleOutcome(rule.id, execution.outcome, execution.outcome_class)
            if candidate.deleted_at is not None:
                execution.outcome_class = SKIP_CANDIDATE_DELETED
                return RuleOutcome(rule.id, execution.outcome, execution.outcome_class)
            if not candidate_in_scope(db, owner, candidate):
                execution.outcome_class = SKIP_OUT_OF_SCOPE
                return RuleOutcome(rule.id, execution.outcome, execution.outcome_class)
            assignment = current_assignment(db, candidate.id)
            skip = _conditions_hold(
                db, rule=rule, candidate=candidate, assignment=assignment, settings=settings
            )
            if skip is not None:
                execution.outcome_class = skip
                return RuleOutcome(rule.id, execution.outcome, execution.outcome_class)
            outcome, outcome_class, outbox_ids, list_id, version_id = _run_action(
                db,
                rule=rule,
                owner=owner,
                candidate=candidate,
                assignment=assignment,
                dedupe_key=dedupe_key,
                settings=settings,
                now=now,
            )
            execution.outcome = outcome
            execution.outcome_class = outcome_class
            execution.outbox_ids = outbox_ids or None
            execution.list_id = list_id
            execution.list_version_id = version_id
            return RuleOutcome(rule.id, outcome, outcome_class)
    except IntegrityError as exc:
        if is_duplicate_key_error(exc):
            return RuleOutcome(rule.id, AutomationExecutionOutcome.SKIPPED, SKIP_DUPLICATE)
        raise


def _evaluate_rule_guarded(
    db: Session,
    *,
    rule: AutomationRule,
    candidate: Candidate,
    trigger_object_type: str,
    trigger_object_id: UUID,
    trigger_object_version: int | None,
    dedupe_key: str,
    settings: Settings,
    now: datetime,
) -> RuleOutcome:
    """Failure model: a rule error is contained to its SAVEPOINT, recorded
    as a ``failed`` execution (exception type only) and never propagates."""
    try:
        return _evaluate_rule(
            db,
            rule=rule,
            candidate=candidate,
            trigger_object_type=trigger_object_type,
            trigger_object_id=trigger_object_id,
            trigger_object_version=trigger_object_version,
            dedupe_key=dedupe_key,
            settings=settings,
            now=now,
        )
    except Exception as exc:  # containment is the whole point of this guard
        logger.warning(
            "automation rule %s failed type=%s (contained; main operation continues)",
            rule.id,
            type(exc).__name__,
        )
        try:
            with db.begin_nested():
                _record_execution(
                    db,
                    rule=rule,
                    trigger_object_type=trigger_object_type,
                    trigger_object_id=trigger_object_id,
                    trigger_object_version=trigger_object_version,
                    candidate_id=candidate.id,
                    outcome=AutomationExecutionOutcome.FAILED,
                    outcome_class=f"{FAIL_INTERNAL}:{type(exc).__name__}"[:64],
                    dedupe_key=dedupe_key,
                    now=now,
                )
        except IntegrityError as inner:
            if not is_duplicate_key_error(inner):
                logger.warning("automation rule %s: failure record not written", rule.id)
        except Exception:
            logger.warning("automation rule %s: failure record not written", rule.id)
        return RuleOutcome(rule.id, AutomationExecutionOutcome.FAILED, FAIL_INTERNAL)


# --- Trigger: stage entered (synchronous, same transaction) ----------------------------------


def _active_rules(db: Session, trigger: AutomationTriggerType) -> list[AutomationRule]:
    return list(
        db.execute(
            select(AutomationRule)
            .where(
                AutomationRule.trigger_type == trigger,
                AutomationRule.is_enabled.is_(True),
                AutomationRule.deleted_at.is_(None),
            )
            .order_by(AutomationRule.created_at, AutomationRule.id)
        )
        .scalars()
        .all()
    )


def run_stage_entered_rules(
    db: Session,
    *,
    candidate: Candidate,
    new_stage: CandidateStage,
    transition_at: datetime,
    settings: Settings,
    now: datetime | None = None,
) -> list[RuleOutcome]:
    """Evaluate every enabled ``stage_entered`` rule for this transition.

    ``transition_at`` identifies the business event durably (the
    candidate's ``updated_at`` of the transition, set by the caller); it
    becomes part of the dedupe key so a retried evaluation of the same
    transition is one logical action while a later re-entry of the stage
    is a new one. Never raises for rule errors (module failure model).
    """
    now = now or utc_now()
    if candidate.deleted_at is not None:
        return []
    marker = ensure_aware(transition_at).isoformat(timespec="microseconds")
    outcomes: list[RuleOutcome] = []
    for rule in _active_rules(db, AutomationTriggerType.STAGE_ENTERED):
        params = rule.trigger_params or {}
        if params.get("stage") != new_stage.value:
            continue
        dedupe_key = f"stage:{candidate.id}:{new_stage.value}:{marker}"
        outcomes.append(
            _evaluate_rule_guarded(
                db,
                rule=rule,
                candidate=candidate,
                trigger_object_type=TRIGGER_OBJECT_CANDIDATE,
                trigger_object_id=candidate.id,
                trigger_object_version=None,
                dedupe_key=dedupe_key,
                settings=settings,
                now=now,
            )
        )
    return outcomes


# --- Trigger: documents missing and due (worker pass) -------------------------------------


def scan_due_rules(
    db: Session, *, settings: Settings, now: datetime | None = None, batch_size: int = 100
) -> int:
    """Worker pass for ``documents_missing_due`` rules.

    For every enabled rule and every current assignment whose age (in the
    owner's timezone) reached ``days_after`` and that still has missing
    required items, one evaluation runs per (rule, assignment, local day).
    Each evaluation commits on its own; the execution table's unique key
    makes parallel workers converge on one logical action. Returns the
    number of evaluations performed (skips included).
    """
    now = now or utc_now()
    processed = 0
    for rule in _active_rules(db, AutomationTriggerType.DOCUMENTS_MISSING_DUE):
        params = rule.trigger_params or {}
        days_after = int(params.get("days_after", 0))
        if days_after < 1:
            continue
        preference = preference_for(db, rule.owner_user_id, settings.notification_default_timezone)
        zone = ZoneInfo(preference.timezone)
        local_today = now.astimezone(zone).date()
        # Assignments old enough: assigned before (today - days_after) in the
        # owner's local calendar, i.e. their local date + N days <= today.
        cutoff_local = datetime.combine(
            local_today - timedelta(days=days_after - 1), datetime.min.time()
        ).replace(tzinfo=zone)
        cutoff_utc = cutoff_local.astimezone(ZoneInfo("UTC"))
        missing_exists = (
            select(CandidateDocumentItem.id)
            .where(
                CandidateDocumentItem.assignment_id == CandidateDocumentAssignment.id,
                CandidateDocumentItem.status == CandidateDocumentStatus.MISSING,
                CandidateDocumentItem.is_required.is_(True),
            )
            .exists()
        )
        assignments = (
            db.execute(
                select(CandidateDocumentAssignment)
                .join(Candidate, Candidate.id == CandidateDocumentAssignment.candidate_id)
                .where(
                    CandidateDocumentAssignment.replaced_at.is_(None),
                    CandidateDocumentAssignment.assigned_at < cutoff_utc,
                    Candidate.deleted_at.is_(None),
                    missing_exists,
                )
                .order_by(CandidateDocumentAssignment.assigned_at)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )
        for assignment in assignments:
            candidate = db.get(Candidate, assignment.candidate_id)
            if candidate is None:
                continue
            dedupe_key = f"due:{assignment.id}:{days_after}:{local_today.isoformat()}"
            already = db.execute(
                select(AutomationRuleExecution.id).where(
                    AutomationRuleExecution.rule_id == rule.id,
                    AutomationRuleExecution.dedupe_key == dedupe_key,
                )
            ).scalar()
            if already is not None:
                continue
            try:
                _evaluate_rule_guarded(
                    db,
                    rule=rule,
                    candidate=candidate,
                    trigger_object_type=TRIGGER_OBJECT_ASSIGNMENT,
                    trigger_object_id=assignment.id,
                    trigger_object_version=None,
                    dedupe_key=dedupe_key,
                    settings=settings,
                    now=now,
                )
                db.commit()
                processed += 1
            except Exception:  # retried on the next pass
                db.rollback()
                logger.warning(
                    "due-rule evaluation failed rule=%s; will retry on the next pass", rule.id
                )
    return processed


# --- Disabling a rule cancels its not-started jobs -------------------------------------


def cancel_pending_rule_jobs(db: Session, *, rule_id: UUID, now: datetime | None = None) -> int:
    """Cancel queued (not yet claimed) outbox rows of a rule.

    Rows already ``sending`` finish with their own outcome; the immutable
    execution history is never rewritten. Returns the count.
    """
    now = now or utc_now()
    result = db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.rule_id == rule_id,
            NotificationOutbox.status == DeliveryStatus.QUEUED,
        )
        .values(status=DeliveryStatus.CANCELLED, cancelled_at=now)
    )
    return result.rowcount if result.rowcount is not None else 0  # type: ignore[attr-defined]


def rule_execution_count(db: Session, rule_id: UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(AutomationRuleExecution)
            .where(AutomationRuleExecution.rule_id == rule_id)
        ).scalar_one()
    )
