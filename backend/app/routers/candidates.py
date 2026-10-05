"""Candidate database endpoints — the single shared candidate base.

Server-side authorization rules (``PRODUCT_SPEC.md`` §2, §4):

* every endpoint requires an authenticated session (401 otherwise) and a
  valid CSRF token on mutating methods (enforced by ``get_current_user``);
* an HR sees and mutates only their own candidates (``owner_user_id`` ==
  self); foreign candidates return 404 so their existence is not leaked;
* a manager or administrator sees all candidates and may filter by owner;
* deletion is always soft; a deleted candidate is excluded from regular
  lists, can be restored, and can never be physically deleted;
* every change is recorded in the audit trail. Candidate personal data is
  never written to audit details or logs.

Duplicate protection (``PRODUCT_SPEC.md`` §4): on create/update the server
looks for similar non-deleted candidates by normalized phone/email. If
matches exist and ``confirm_duplicate`` is not true, the request is rejected
with 409 and the matches; an explicit confirmation creates/updates anyway
and records a dedicated audit event.
"""

from datetime import date, datetime, time, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from app import queue_dashboard as dashboard
from app.analytics_ledger import record_fact
from app.assignees import resolve_assignee
from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    CANDIDATE_STAGE_ORDER,
    CANDIDATE_STAGE_POSITION,
    AnalyticsFactType,
    AuditAction,
    Candidate,
    CandidateInteraction,
    CandidateSource,
    CandidateStage,
    CandidateTermination,
    CandidateTransfer,
    Event,
    EventStatus,
    ScheduleImportRow,
    User,
    UserRole,
)
from app.notification_service import transfer_notification
from app.schemas import (
    CandidateCreate,
    CandidateList,
    CandidateOut,
    CandidatePositionList,
    CandidatePositionOption,
    CandidateTerminationCreate,
    CandidateTerminationList,
    CandidateTerminationOut,
    CandidateTransferCreate,
    CandidateTransferOut,
    CandidateUpdate,
    DuplicateCandidateDetail,
    InteractionCreate,
    InteractionList,
    InteractionOut,
    QueueDashboard,
    QueueStageCount,
    QueueStuckCandidate,
    QueueSummary,
    QueueUpcomingEvent,
    TransferList,
    TransferOut,
)
from app.utils import (
    client_ip,
    normalize_email,
    normalize_full_name,
    normalize_phone,
    normalize_position,
    user_agent,
    utc_now,
)
from app.work_schedule import START_DATE_FIELDS, START_STAGES, START_TEXT_FIELDS


# Значения блока «Выход на работу» в строке аудита: даты, время и короткие
# строки места выхода; персональные данные кандидата сюда не попадают.
def _schedule_value(value: object | None) -> str:
    """Читаемое значение поля выхода для строки аудита («—» для пустого)."""

    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)


router = APIRouter(prefix="/candidates", tags=["candidates"])

# Server-side sort whitelist: query parameter -> orderable column. Anything
# else falls back to the default (created_at desc).
_SORT_COLUMNS = {
    "created_at": Candidate.created_at,
    "updated_at": Candidate.updated_at,
    "full_name": Candidate.full_name,
    "stage": Candidate.stage_position,
}

_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 50


def _audit_candidate(
    db: Session,
    request: Request,
    action: AuditAction,
    *,
    actor: User,
    candidate: Candidate,
    details: str | None = None,
    commit: bool = True,
) -> None:
    """Record a candidate-scoped audit event.

    ``details`` receives only non-personal context (stage/source values,
    ids, start date/time). Phone/email/name are never passed here.
    """
    record_event(
        db,
        action,
        actor=actor,
        candidate_id=candidate.id,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=commit,
    )


def _get_candidate_or_404(db: Session, candidate_id: str) -> Candidate:
    try:
        parsed = UUID(candidate_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден."
        ) from None
    candidate = db.get(Candidate, parsed)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    return candidate


def _can_see(user: User, candidate: Candidate) -> bool:
    """HRs see only their own candidates; managers/admins see everything."""
    return user.role != UserRole.HR or candidate.owner_user_id == user.id


def _get_visible_candidate(
    db: Session, candidate_id: str, user: User, *, include_deleted: bool = False
) -> Candidate:
    """Resolve a candidate with visibility rules; 404 hides foreign/deleted."""
    candidate = _get_candidate_or_404(db, candidate_id)
    if not _can_see(user, candidate):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    if not include_deleted and candidate.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    return candidate


def _scope_for_user(user: User) -> list:
    """Visibility predicate for list queries (HR -> own queue only)."""
    if user.role == UserRole.HR:
        return [Candidate.owner_user_id == user.id]
    return []


def _build_list_query(
    user: User,
    *,
    query: str | None,
    stage: CandidateStage | None,
    owner_id: UUID | None,
    source: CandidateSource | None,
    position: str | None = None,
    include_deleted: bool = False,
) -> Select[tuple[Candidate]]:
    """Shared filter builder for list/count queries."""
    conditions = _scope_for_user(user)
    if include_deleted:
        # The deleted-candidates view: only soft-deleted rows, same RBAC scope.
        conditions.append(Candidate.deleted_at.is_not(None))
    else:
        conditions.append(Candidate.deleted_at.is_(None))

    if query:
        cleaned = query.strip()
        if cleaned:
            like = f"%{cleaned.casefold()}%"
            search_conditions: list = [
                Candidate.full_name_normalized.like(like),
                Candidate.email_normalized.like(like),
            ]
            phone_like = normalize_phone(cleaned)
            if phone_like:
                # Match on digits only so partial numbers ("222-22-22")
                # still hit stored "+72222222222" values.
                search_conditions.append(
                    Candidate.phone_normalized.like(f"%{phone_like.lstrip('+')}%")
                )
            conditions.append(or_(*search_conditions))
    if stage is not None:
        conditions.append(Candidate.stage == stage)
    if source is not None:
        conditions.append(Candidate.source == source)
    # Должность — свободный текст (справочника вакансий в проекте нет),
    # поэтому сравниваем нормализованные значения: регистр и лишние пробелы
    # не должны менять результат. Приведение делает Python (casefold), а не
    # SQL lower() — иначе фильтр по кириллице зависел бы от локали БД.
    if position is not None:
        cleaned_position = normalize_position(position)
        if cleaned_position:
            conditions.append(Candidate.position_normalized == cleaned_position)
    # HRs are always scoped to themselves; managers/admins may filter by owner.
    if owner_id is not None and user.role != UserRole.HR:
        conditions.append(Candidate.owner_user_id == owner_id)
    return select(Candidate).where(*conditions)


def _find_duplicates(
    db: Session,
    user: User,
    *,
    phone: str | None,
    email: str | None,
    exclude_id: UUID | None = None,
) -> list[Candidate]:
    """Similar non-deleted candidates by normalized phone/email.

    Only candidates the caller may see are considered — an HR must never
    learn (via a 409 body) that a colleague's candidate shares a phone.
    """
    phone_normalized = normalize_phone(phone)
    email_normalized = normalize_email(email)
    duplicate_conditions = []
    if phone_normalized:
        duplicate_conditions.append(Candidate.phone_normalized == phone_normalized)
    if email_normalized:
        duplicate_conditions.append(Candidate.email_normalized == email_normalized)
    if not duplicate_conditions:
        return []
    conditions = [or_(*duplicate_conditions), *_scope_for_user(user)]
    stmt = select(Candidate).where(Candidate.deleted_at.is_(None), *conditions)
    if exclude_id is not None:
        stmt = stmt.where(Candidate.id != exclude_id)
    return list(db.scalars(stmt.limit(10)).all())


def _duplicate_conflict(
    db: Session,
    user: User,
    *,
    phone: str | None,
    email: str | None,
    exclude_id: UUID | None = None,
) -> HTTPException | None:
    """409 with matches, or None when no duplicates found."""
    duplicates = _find_duplicates(db, user, phone=phone, email=email, exclude_id=exclude_id)
    if not duplicates:
        return None
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=DuplicateCandidateDetail(
            message=(
                "Найден похожий кандидат. Подтвердите создание/изменение "
                "повторной отправкой запроса с confirm_duplicate=true."
            ),
            duplicates=[CandidateOut.model_validate(c) for c in duplicates],
        ).model_dump(mode="json"),
    )


def _resolve_owner(db: Session, creator: User, requested_owner_id: UUID | None) -> User:
    """Ownership rules on creation: HR -> self only; managers/admins -> any active user.

    A missing ``owner_user_id`` means the creator becomes the owner.
    """
    if creator.role == UserRole.HR and requested_owner_id not in (None, creator.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HR может создавать кандидатов только в своей очереди.",
        )
    if requested_owner_id is None:
        return creator
    owner = db.get(User, requested_owner_id)
    if owner is None or not owner.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ответственный не найден или неактивен.",
        )
    return owner


@router.get("", response_model=CandidateList, summary="List candidates (server-side filters)")
def list_candidates(
    query: str | None = Query(default=None, max_length=200),
    stage: CandidateStage | None = Query(default=None),
    owner_id: UUID | None = Query(default=None),
    source: CandidateSource | None = Query(default=None),
    position: str | None = Query(default=None, max_length=200),
    include_deleted: bool = Query(default=False),
    sort: str = Query(default="created_at"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateList:
    """Paginated candidate list with search, filters, sorting.

    * search matches full name (case-insensitive) plus normalized phone and
      email;
    * ``position`` filters by the free-text position (case-insensitive exact
      match — there is no vacancy directory, the value comes from the data);
    * HRs always see only their own candidates regardless of ``owner_id``;
      managers/admins may filter by owner;
    * soft-deleted candidates are excluded by default;
      ``include_deleted=true`` scopes the listing to soft-deleted candidates
      only (the deleted-candidates view) — visibility rules stay the same.
    """
    stmt = _build_list_query(
        user,
        query=query,
        stage=stage,
        owner_id=owner_id,
        source=source,
        position=position,
        include_deleted=include_deleted,
    )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    order_column = _SORT_COLUMNS.get(sort, Candidate.created_at)
    if direction == "asc":
        stmt = stmt.order_by(order_column.asc())
    else:
        stmt = stmt.order_by(order_column.desc())
    # Deterministic tiebreaker for stable pagination.
    stmt = stmt.order_by(Candidate.id).limit(limit).offset(offset)

    candidates = db.scalars(stmt).all()
    return CandidateList(
        items=[CandidateOut.model_validate(c) for c in candidates],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- «Должность»: отдельный справочник значений -------------------------------

# Ceiling on the number of distinct positions returned. The option list is
# built server-side (GROUP BY), so the ceiling only bounds the response size:
# a position that exists in row 4 000 is still offered, and the filter still
# matches it. Before this endpoint existed the dropdown was filled from the
# first page of GET /candidates (100 rows) and such a position could never be
# selected at all — the filter is a <select> with no free input.
_MAX_POSITION_OPTIONS = 1000
_DEFAULT_POSITION_OPTIONS = 500


@router.get(
    "/positions",
    response_model=CandidatePositionList,
    summary="Distinct positions within the caller's scope",
)
def list_candidate_positions(
    owner_id: UUID | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=_DEFAULT_POSITION_OPTIONS, ge=1, le=_MAX_POSITION_OPTIONS),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidatePositionList:
    """Distinct free-text positions for the «Должность» filter.

    There is no vacancy directory in the project: ``Candidate.position`` is
    free text, so the options come from the data itself. The scan happens in
    SQL over the **whole** visible scope (``GROUP BY position_normalized``) —
    never from one page of candidates and never in the browser.

    The scope is exactly the scope of ``GET /candidates``: an HR only sees
    positions of their own candidates, a manager/administrator may narrow the
    scope with ``owner_id``, and ``include_deleted`` switches to the
    soft-deleted view. Deduplication uses the same ``normalize_position`` the
    filter uses, so an option and the filter can never disagree about case or
    whitespace. Empty positions are not selectable and are skipped.

    Sorting is done in Python on the normalized value: ``ORDER BY`` on text
    would depend on the database collation (SQLite vs PostgreSQL order
    Cyrillic differently), and the dropdown must look the same on both.
    """
    conditions = _scope_for_user(user)
    conditions.append(
        Candidate.deleted_at.is_not(None) if include_deleted else Candidate.deleted_at.is_(None)
    )
    if owner_id is not None and user.role != UserRole.HR:
        conditions.append(Candidate.owner_user_id == owner_id)
    conditions.append(Candidate.position_normalized != "")

    grouped = (
        select(
            Candidate.position_normalized.label("normalized"),
            func.count(Candidate.id).label("candidates"),
            # Representative spelling (min is deterministic within the group).
            func.min(Candidate.position).label("display"),
        )
        .where(*conditions)
        .group_by(Candidate.position_normalized)
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(grouped)) or 0
    rows = db.execute(
        select(grouped.c.normalized, grouped.c.candidates, grouped.c.display).limit(limit)
    ).all()
    ordered = sorted(rows, key=lambda row: row.normalized)
    items = [
        CandidatePositionOption(position=row.display, count=int(row.candidates)) for row in ordered
    ]
    return CandidatePositionList(
        items=items,
        total=int(total),
        limit=limit,
        truncated=int(total) > len(items),
    )


# --- «Моя очередь»: серверные агрегаты ---------------------------------------

#: Funnel stages that mean «кандидат больше не в работе». Mirrors the closed
#: stages of the «Моя очередь» screen in ``frontend/src/features/queue``;
#: kept here because the aggregates are computed by the server now.
QUEUE_CLOSED_STAGES: tuple[CandidateStage, ...] = (
    CandidateStage.HIRED,
    CandidateStage.STARTED,
    CandidateStage.PROBATION,
    CandidateStage.FIRED,
    CandidateStage.REJECTED,
)
#: Stages that still need recruiter action — the single source of truth for
#: «в работе»: derived from the funnel order, so a stage cannot silently fall
#: out of both sets. Both ``in_work`` and «застрявшие» count through it.
QUEUE_WORK_STAGES: tuple[CandidateStage, ...] = tuple(
    stage for stage in CANDIDATE_STAGE_ORDER if stage not in QUEUE_CLOSED_STAGES
)
#: Days without movement after which an in-work candidate needs attention.
QUEUE_STUCK_DAYS = 3
#: Length of the «Ближайшие события» / «Выходы на неделе» window, in days.
QUEUE_HORIZON_DAYS = 7
#: Default size of the bounded samples (cards), well above what the cards show.
QUEUE_SAMPLE_LIMIT = 6
_MAX_QUEUE_SAMPLE = 100


@router.get(
    "/queue/summary",
    response_model=QueueSummary,
    summary="Personal queue summary (server-side aggregates)",
)
def queue_summary(
    stuck_days: int = Query(default=QUEUE_STUCK_DAYS, ge=1, le=365),
    horizon_days: int = Query(default=QUEUE_HORIZON_DAYS, ge=1, le=31),
    sample_limit: int = Query(default=QUEUE_SAMPLE_LIMIT, ge=1, le=_MAX_QUEUE_SAMPLE),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QueueSummary:
    """«Моя очередь» in one response: full-scope aggregates + small samples.

    Why this endpoint: the screen used to download the 100 most recently
    updated candidates and count everything in the browser. A candidate who
    has been waiting for three weeks is by definition *not* among the 100
    freshest rows, so «Требуют внимания» and «Без движения» silently under-
    reported, and the KPI tiles presented one page as the whole queue. Here
    every counter is a SQL aggregate over all candidates of the caller; the
    browser only receives numbers plus two bounded samples for the cards.

    Personality: the scope is ``owner_user_id == caller`` for **every** role —
    an HR, a manager, an administrator and the pilot account all get *their
    own* queue, which is what the «Моя очередь» heading promises. A request
    without ``owner_id`` used to return the shared base for a manager.

    Semantics:

    * ``total`` — all non-deleted candidates of the caller;
    * ``in_work`` — the same minus the closed funnel stages;
    * ``fresh`` — created within the last 24 hours;
    * ``stuck`` — in work and untouched for ``stuck_days`` days;
    * ``starts`` — ``start_date`` inside ``[today, today + horizon_days)``;
    * ``upcoming_events`` — events of the caller's own candidates starting in
      the same window. Terminal events (``completed`` / ``cancelled``) are
      excluded: a finished event is not something that still needs action.

    Windows are UTC instants; the server never converts to browser-local time
    (the same rule ``app/analytics.py`` documents for the analytics period).
    """
    now = utc_now()
    today = now.date()
    horizon_end = today + timedelta(days=horizon_days)
    stuck_before = now - timedelta(days=stuck_days)

    base_conditions = [
        # «Моя очередь» — личная для всех ролей (см. docstring).
        Candidate.owner_user_id == user.id,
        Candidate.deleted_at.is_(None),
    ]

    stage_rows = db.execute(
        select(Candidate.stage, func.count(Candidate.id))
        .where(*base_conditions)
        .group_by(Candidate.stage)
    ).all()
    per_stage = {stage: int(count) for stage, count in stage_rows}
    total = sum(per_stage.values())
    in_work = sum(per_stage.get(stage, 0) for stage in QUEUE_WORK_STAGES)
    by_stage = [
        QueueStageCount(stage=stage, count=per_stage.get(stage, 0))
        for stage in CANDIDATE_STAGE_ORDER
    ]

    def _count(*extra: ColumnElement[bool]) -> int:
        value = db.scalar(
            select(func.count()).select_from(Candidate).where(*base_conditions, *extra)
        )
        return int(value or 0)

    fresh = _count(Candidate.created_at >= now - timedelta(days=1))
    starts = _count(Candidate.start_date >= today, Candidate.start_date < horizon_end)

    stuck_conditions = [
        *base_conditions,
        Candidate.stage.in_(QUEUE_WORK_STAGES),
        Candidate.updated_at < stuck_before,
    ]
    stuck = int(
        db.scalar(select(func.count()).select_from(Candidate).where(*stuck_conditions)) or 0
    )
    # Sample: the stalest first — they have been waiting the longest.
    stuck_rows = db.scalars(
        select(Candidate)
        .where(*stuck_conditions)
        .order_by(Candidate.updated_at.asc(), Candidate.id.asc())
        .limit(sample_limit)
    ).all()
    stuck_sample = [
        QueueStuckCandidate(
            id=row.id,
            full_name=row.full_name,
            position=row.position,
            stage=row.stage,
            updated_at=row.updated_at,
        )
        for row in stuck_rows
    ]

    event_conditions = [
        Candidate.owner_user_id == user.id,
        Candidate.deleted_at.is_(None),
        Event.starts_at >= datetime.combine(today, time(0, 0), tzinfo=now.tzinfo),
        Event.starts_at < datetime.combine(horizon_end, time(0, 0), tzinfo=now.tzinfo),
        # Завершённое или отменённое событие не «требует действия».
        Event.status.notin_([EventStatus.COMPLETED, EventStatus.CANCELLED]),
    ]
    event_stmt = (
        select(Event).join(Candidate, Event.candidate_id == Candidate.id).where(*event_conditions)
    )
    events_total = int(db.scalar(select(func.count()).select_from(event_stmt.subquery())) or 0)
    event_rows = db.scalars(
        event_stmt.order_by(Event.starts_at.asc(), Event.id.asc()).limit(sample_limit)
    ).all()
    upcoming_events = [
        QueueUpcomingEvent(
            id=row.id,
            candidate_id=row.candidate_id,
            candidate_full_name=row.candidate.full_name if row.candidate is not None else "",
            type=row.type,
            title=row.title,
            status=row.status,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )
        for row in event_rows
    ]

    return QueueSummary(
        owner_id=user.id,
        owner_username=user.username,
        generated_at=now,
        total=total,
        in_work=in_work,
        fresh=fresh,
        stuck=stuck,
        starts=starts,
        stuck_days=stuck_days,
        horizon_days=horizon_days,
        closed_stages=[stage.value for stage in QUEUE_CLOSED_STAGES],
        by_stage=by_stage,
        stuck_sample=stuck_sample,
        stuck_sample_truncated=stuck > len(stuck_sample),
        upcoming_events=upcoming_events,
        upcoming_events_total=events_total,
        upcoming_events_truncated=events_total > len(upcoming_events),
    )


@router.get(
    "/queue/dashboard",
    response_model=QueueDashboard,
    summary="Dashboard aggregates for «Моя очередь» (period, KPI, series)",
)
def queue_dashboard(
    period: str = Query(
        default="week",
        pattern="^(today|week|all)$",
        description="Период: today (по часам), week (по дням), all (по месяцам)",
    ),
    timezone: str = Query(
        default=dashboard.DEFAULT_DASHBOARD_TIMEZONE,
        description="IANA таймзона, в которой считаются границы периода",
    ),
    owner_id: UUID | None = Query(
        default=None, description="Чья очередь (руководитель/администратор); HR — всегда своя"
    ),
    position: str | None = Query(default=None, max_length=200),
    stage: CandidateStage | None = Query(default=None),
    source: CandidateSource | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QueueDashboard:
    """Analytics cards of «Моя очередь»: KPI, series, sources, tasks, funnel.

    Why an endpoint and not a bigger client: every series here is an aggregate
    over the caller's whole scope. Shipping the raw rows to the browser would
    reintroduce exactly the defect the summary fixed — a page of candidates
    presented as the whole queue — and would move period/timezone arithmetic
    into the client, where it cannot be tested by the API suite.

    Scope: personal for every role (HR, manager, administrator, pilot); a
    manager or an administrator may pass ``owner_id`` to look at a colleague,
    an HR cannot widen the scope at all. Filters (``position``/``stage``/
    ``source``) apply to the current attributes of the candidate, i.e. the
    same values the list screen filters on.

    A metric the data model cannot express is returned as ``null`` with a note
    in ``kpi_notes`` (there is no vacancy entity, so «активные вакансии» is
    ``null`` rather than a fabricated zero).
    """
    payload = dashboard.build_dashboard(
        db,
        user=user,
        period=period,
        timezone=timezone,
        owner_id=owner_id,
        position=position,
        stage=stage,
        source=source,
    )
    tasks = payload.pop("tasks")
    payload["tasks_due"] = tasks["due"]
    payload["tasks_overdue"] = tasks["overdue"]
    return QueueDashboard(**payload)


@router.post(
    "",
    response_model=CandidateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a candidate",
)
def create_candidate(
    payload: CandidateCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateOut:
    """Create a candidate with duplicate protection and an audit entry."""
    owner = _resolve_owner(db, user, payload.owner_user_id)

    phone = payload.phone
    email = str(payload.email) if payload.email else None
    conflict = _duplicate_conflict(db, user, phone=phone, email=email)
    if conflict is not None and not payload.confirm_duplicate:
        raise conflict

    candidate = Candidate(
        full_name=payload.full_name,
        full_name_normalized=normalize_full_name(payload.full_name),
        phone=phone,
        phone_normalized=normalize_phone(phone),
        email=email,
        email_normalized=normalize_email(email),
        source=payload.source,
        position=payload.position,
        position_normalized=normalize_position(payload.position),
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=CANDIDATE_STAGE_POSITION[CandidateStage.NEW],
    )
    db.add(candidate)
    db.flush()  # candidate.id / created_at for the ledger fact below

    # Analytics fact, candidate row and audit event commit in ONE transaction:
    # an audit/ledger failure rolls the whole creation back.
    record_fact(
        db,
        fact_type=AnalyticsFactType.CANDIDATE_CREATED,
        candidate_id=candidate.id,
        owner_user_id=owner.id,
        fact_at=candidate.created_at,
        source=payload.source.value,
    )
    is_duplicate = conflict is not None
    _audit_candidate(
        db,
        request,
        AuditAction.DUPLICATE_CANDIDATE_CREATED if is_duplicate else AuditAction.CANDIDATE_CREATED,
        actor=user,
        candidate=candidate,
        details=(
            f"source={candidate.source.value} owner={candidate.owner_user_id}"
            + (" confirmed_duplicate=true" if is_duplicate else "")
        ),
        commit=False,
    )
    db.commit()
    db.refresh(candidate)
    return CandidateOut.model_validate(candidate)


@router.get("/{candidate_id}", response_model=CandidateOut, summary="Get a candidate")
def get_candidate(
    candidate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateOut:
    candidate = _get_visible_candidate(db, candidate_id, user)
    return CandidateOut.model_validate(candidate)


@router.patch("/{candidate_id}", response_model=CandidateOut, summary="Update a candidate")
def update_candidate(
    candidate_id: str,
    payload: CandidateUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateOut:
    """Update editable fields; stage changes are audited separately.

    Duplicate protection applies when phone/email change. Ownership changes
    are deliberately not part of this endpoint (separate transfer operation,
    next phase).
    """
    candidate = _get_visible_candidate(db, candidate_id, user)

    new_phone = payload.phone if payload.phone is not None else candidate.phone
    new_email = str(payload.email) if payload.email is not None else candidate.email
    phone_changed = new_phone != candidate.phone
    email_changed = new_email != candidate.email
    if phone_changed or email_changed:
        conflict = _duplicate_conflict(
            db, user, phone=new_phone, email=new_email, exclude_id=candidate.id
        )
        if conflict is not None and not payload.confirm_duplicate:
            raise conflict

    changes: list[str] = []
    if payload.full_name is not None and payload.full_name != candidate.full_name:
        candidate.full_name = payload.full_name
        candidate.full_name_normalized = normalize_full_name(payload.full_name)
        changes.append("full_name")
    if phone_changed:
        candidate.phone = new_phone
        candidate.phone_normalized = normalize_phone(new_phone)
        changes.append("phone")
    if email_changed:
        candidate.email = new_email
        candidate.email_normalized = normalize_email(new_email)
        changes.append("email")
    if payload.source is not None and payload.source != candidate.source:
        candidate.source = payload.source
        changes.append(f"source={payload.source.value}")
    if payload.position is not None and payload.position != candidate.position:
        candidate.position = payload.position
        candidate.position_normalized = normalize_position(payload.position)
        changes.append("position")

    # --- Phase 18: «Выход на работу» -------------------------------------
    # Дата/время и место выхода пишутся только если поле реально пришло в
    # запросе: null очищает значение, отсутствие поля его не трогает.
    provided = payload.model_fields_set
    start_changes: list[str] = []
    for field_name in (*START_DATE_FIELDS, *START_TEXT_FIELDS):
        if field_name not in provided:
            continue
        old_value = getattr(candidate, field_name)
        new_value = getattr(payload, field_name)
        if old_value == new_value:
            continue
        setattr(candidate, field_name, new_value)
        start_changes.append(
            f"{field_name}: {_schedule_value(old_value)} -> {_schedule_value(new_value)}"
        )

    target_stage = payload.stage if payload.stage is not None else candidate.stage
    date_on_wrong_stage = (
        "start_date" in provided
        and payload.start_date is not None
        and target_stage not in START_STAGES
    )
    if date_on_wrong_stage:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=("Дату выхода можно указать на этапах «Оффер», «Оформлен» и «Вышел»."),
        )
    if "start_time" in provided and payload.start_time is not None and candidate.start_date is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Сначала укажите дату выхода, затем время.",
        )

    stage_changed = False
    if payload.stage is not None and payload.stage != candidate.stage:
        old_stage = candidate.stage
        candidate.stage = payload.stage
        candidate.stage_position = CANDIDATE_STAGE_POSITION[payload.stage]
        stage_changed = True

    if candidate.stage == CandidateStage.STARTED and candidate.start_date is None:
        # «Вышел» без даты выхода невозможен: HR обязана указать дату
        # (сервер отвечает 422, UI открывает окно «Укажите дату выхода»).
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=("Укажите дату выхода: перевод в этап «Вышел» без даты невозможен."),
        )

    if not changes and not start_changes and not stage_changed:
        return CandidateOut.model_validate(candidate)

    candidate.updated_at = utc_now()

    # Stage transitions are analytics facts: recorded in the SAME transaction
    # as the update and the audit events (single commit, all-or-nothing).
    if stage_changed and candidate.owner_user_id is not None:
        record_fact(
            db,
            fact_type=AnalyticsFactType.STAGE_CHANGED,
            candidate_id=candidate.id,
            owner_user_id=candidate.owner_user_id,
            fact_at=candidate.updated_at,
            stage_from=old_stage.value,
            stage_to=candidate.stage.value,
            source=candidate.source.value,
        )
    if stage_changed:
        _audit_candidate(
            db,
            request,
            AuditAction.CANDIDATE_STAGE_CHANGED,
            actor=user,
            candidate=candidate,
            details=f"{old_stage.value} -> {candidate.stage.value}",
            commit=False,
        )
    if changes:
        _audit_candidate(
            db,
            request,
            AuditAction.CANDIDATE_UPDATED,
            actor=user,
            candidate=candidate,
            details="; ".join(changes),
            commit=False,
        )
    if start_changes:
        # Перенос даты/времени и правка места выхода аудируются отдельным
        # событием с прежними и новыми значениями.
        _audit_candidate(
            db,
            request,
            AuditAction.CANDIDATE_START_SCHEDULE_CHANGED,
            actor=user,
            candidate=candidate,
            details="; ".join(start_changes),
            commit=False,
        )
    db.commit()
    db.refresh(candidate)
    return CandidateOut.model_validate(candidate)


@router.delete(
    "/{candidate_id}",
    response_model=CandidateOut,
    summary="Soft-delete a candidate",
)
def delete_candidate(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateOut:
    """Soft delete (physical deletion does not exist)."""
    candidate = _get_visible_candidate(db, candidate_id, user)
    if candidate.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")

    candidate.deleted_at = utc_now()
    candidate.deleted_by_user_id = user.id
    candidate.updated_at = utc_now()
    db.commit()
    db.refresh(candidate)
    _audit_candidate(db, request, AuditAction.CANDIDATE_DELETED, actor=user, candidate=candidate)
    return CandidateOut.model_validate(candidate)


@router.post(
    "/{candidate_id}/restore",
    response_model=CandidateOut,
    summary="Restore a soft-deleted candidate",
)
def restore_candidate(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateOut:
    """Restore a soft-deleted candidate back into the working lists."""
    candidate = _get_visible_candidate(db, candidate_id, user, include_deleted=True)
    if candidate.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Кандидат не был удалён."
        )

    candidate.deleted_at = None
    candidate.deleted_by_user_id = None
    candidate.updated_at = utc_now()
    db.commit()
    db.refresh(candidate)
    _audit_candidate(db, request, AuditAction.CANDIDATE_RESTORED, actor=user, candidate=candidate)
    return CandidateOut.model_validate(candidate)


@router.get(
    "/{candidate_id}/interactions",
    response_model=InteractionList,
    summary="List candidate interactions",
)
def list_interactions(
    candidate_id: str,
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InteractionList:
    candidate = _get_visible_candidate(db, candidate_id, user)

    total = (
        db.scalar(
            select(func.count())
            .select_from(CandidateInteraction)
            .where(CandidateInteraction.candidate_id == candidate.id)
        )
        or 0
    )
    interactions = db.scalars(
        select(CandidateInteraction)
        .where(CandidateInteraction.candidate_id == candidate.id)
        .order_by(CandidateInteraction.created_at.desc(), CandidateInteraction.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return InteractionList(
        items=[InteractionOut.model_validate(i) for i in interactions],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{candidate_id}/interactions",
    response_model=InteractionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a candidate interaction",
)
def add_interaction(
    candidate_id: str,
    payload: InteractionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InteractionOut:
    candidate = _get_visible_candidate(db, candidate_id, user)

    interaction = CandidateInteraction(
        candidate_id=candidate.id,
        author_user_id=user.id,
        type=payload.type,
        comment=payload.comment,
    )
    db.add(interaction)
    db.flush()  # interaction.id / created_at for the ledger fact below

    # The responsible HR at fact time is the candidate owner (not the author,
    # who may be a manager acting on behalf of the HR).
    if candidate.owner_user_id is not None:
        record_fact(
            db,
            fact_type=AnalyticsFactType.INTERACTION_ADDED,
            candidate_id=candidate.id,
            owner_user_id=candidate.owner_user_id,
            fact_at=interaction.created_at,
            fact_subtype=interaction.type.value,
            source=candidate.source.value,
            interaction_id=interaction.id,
        )

    # Interaction comments may contain personal data — they are never logged
    # or written into audit details; only type + candidate id are recorded.
    # The fact, the interaction and the audit event commit in ONE transaction.
    _audit_candidate(
        db,
        request,
        AuditAction.CANDIDATE_INTERACTION_ADDED,
        actor=user,
        candidate=candidate,
        details=f"type={interaction.type.value}",
        commit=False,
    )
    db.commit()
    db.refresh(interaction)
    return InteractionOut.model_validate(interaction)


# --- Ownership transfer ------------------------------------------------------


def _resolve_new_owner(db: Session, candidate: Candidate, requested_id: UUID) -> User:
    """The new owner must be a different, active assignable HR user.

    «Assignable» reuses ``app.assignees`` (an active account with role ``hr``
    or the audited pilot account that PRODUCT_SPEC §2 defines as combining HR
    + manager + administrator powers) — the same set the owner picker offers,
    so a visible option is never rejected by the server
    (UX feedback 2026-09-29, block A).
    """
    if requested_id == candidate.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Передача тому же ответственному невозможна.",
        )
    return resolve_assignee(
        db,
        requested_id,
        missing_detail=("Новый ответственный должен быть активным пользователем с ролью HR."),
        invalid_detail=(
            "Новый ответственный должен быть активным пользователем с ролью HR "
            "либо учётной записью пилота с полным доступом."
        ),
    )


@router.post(
    "/{candidate_id}/transfer",
    response_model=CandidateTransferOut,
    summary="Transfer candidate responsibility to another HR",
)
def transfer_candidate(
    candidate_id: str,
    payload: CandidateTransferCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTransferOut:
    """Transfer a candidate to another HR in one atomic transaction.

    * visibility first (foreign/deleted candidates keep the established 404);
    * the candidate row is locked ``FOR UPDATE`` and re-checked against the
      authorized snapshot, so two concurrent transfers cannot interleave;
    * an HR may transfer only their own candidate; managers/admins may
      transfer any visible candidate;
    * the new owner must be a different, active HR user;
    * one immutable history row is created (initiator, from, to, reason,
      time) alongside a PII-free audit event.
    """
    candidate = _get_visible_candidate(db, candidate_id, user)

    # HRs may only hand over their own candidates; managers/admins may
    # transfer any visible one. Existence stays hidden for foreign HRs (404
    # from _get_visible_candidate above).
    if user.role == UserRole.HR and candidate.owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HR может передавать только своих кандидатов.",
        )

    if candidate.owner_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Кандидат пока не назначен HR-менеджеру; используйте ручное назначение в импорте."
            ),
        )
    new_owner = _resolve_new_owner(db, candidate, payload.new_owner_user_id)

    # Lock the candidate row and re-read its current state (populate_existing
    # defeats the identity-map cache so we see the post-lock row version).
    snapshot_owner = candidate.owner_user_id
    locked = db.execute(
        select(Candidate)
        .where(Candidate.id == candidate.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.deleted_at is not None or not _can_see(user, locked):
        # Ownership changed concurrently and the candidate is no longer
        # accessible to this caller: keep the established no-leak 404.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден.")
    if locked.owner_user_id != snapshot_owner:
        # The row changed while we waited for the lock — never overwrite a
        # concurrent transfer silently.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ответственный кандидата уже изменился; обновите данные и повторите.",
        )
    if locked.owner_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Кандидат пока не назначен HR-менеджеру; обновите список.",
        )

    transfer = CandidateTransfer(
        candidate_id=candidate.id,
        initiator_user_id=user.id,
        from_user_id=candidate.owner_user_id,
        to_user_id=new_owner.id,
        reason=payload.reason,
    )
    locked.owner_user_id = new_owner.id
    locked.updated_at = utc_now()
    for source_row in db.scalars(
        select(ScheduleImportRow).where(
            ScheduleImportRow.candidate_id == locked.id,
            ScheduleImportRow.is_active.is_(True),
        )
    ).all():
        source_row.owner_user_id = new_owner.id
    db.add(transfer)
    db.flush()  # transfer.id for the notification dedupe key
    # Phase 8: the handover notification joins the SAME transaction — a
    # crash can never lose it.
    transfer_notification(
        db, transfer=transfer, new_owner=new_owner, settings=request.app.state.settings
    )

    # The transfer is an analytics fact; the new owner is the responsible HR
    # at fact time (later transfers never rewrite earlier facts).
    record_fact(
        db,
        fact_type=AnalyticsFactType.TRANSFER,
        candidate_id=locked.id,
        owner_user_id=new_owner.id,
        fact_at=locked.updated_at,
        source=locked.source.value,
        transfer_id=transfer.id,
    )

    # The audit event joins the SAME transaction (commit=False): ownership
    # change, immutable history record, analytics fact and audit event commit
    # together with a single db.commit() — if the audit write fails, the
    # whole operation rolls back and nothing is transferred.
    _audit_candidate(
        db,
        request,
        AuditAction.CANDIDATE_TRANSFERRED,
        actor=user,
        candidate=locked,
        details=f"to={new_owner.id} from={transfer.from_user_id}",
        commit=False,
    )
    db.commit()
    db.refresh(transfer)
    db.refresh(locked)

    return CandidateTransferOut(
        transfer=TransferOut.model_validate(transfer),
        candidate=CandidateOut.model_validate(locked),
    )


@router.get(
    "/{candidate_id}/transfers",
    response_model=TransferList,
    summary="List the candidate ownership-transfer history",
)
def list_transfers(
    candidate_id: str,
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TransferList:
    """Paginated transfer history with the same visibility rules as the card.

    After a transfer the previous HR can no longer read the card or the
    history through a direct URL (404); the new owner, managers and admins
    see the current data.
    """
    candidate = _get_visible_candidate(db, candidate_id, user)

    total = (
        db.scalar(
            select(func.count())
            .select_from(CandidateTransfer)
            .where(CandidateTransfer.candidate_id == candidate.id)
        )
        or 0
    )
    transfers = db.scalars(
        select(CandidateTransfer)
        .where(CandidateTransfer.candidate_id == candidate.id)
        .order_by(CandidateTransfer.created_at.asc(), CandidateTransfer.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return TransferList(
        items=[TransferOut.model_validate(t) for t in transfers],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{candidate_id}/termination",
    response_model=CandidateTerminationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a candidate termination (dismissal from the company)",
)
def create_termination(
    candidate_id: str,
    payload: CandidateTerminationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTerminationOut:
    """Register a business termination event with a date and a reason.

    Deliberately separate from the ``fired`` candidate stage: a current
    stage alone cannot prove when or why a dismissal happened, so the
    analytics ``terminated`` metric is derived from these records only.
    The termination record, its analytics fact and the audit event commit
    in ONE transaction — an audit/ledger failure rolls everything back.
    """
    candidate = _get_visible_candidate(db, candidate_id, user)
    if user.role == UserRole.HR and candidate.owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HR может регистрировать увольнение только своих кандидатов.",
        )

    termination = CandidateTermination(
        candidate_id=candidate.id,
        terminated_at=payload.terminated_at,
        reason=payload.reason,
        created_by_user_id=user.id,
    )
    db.add(termination)
    db.flush()  # termination.id for the ledger fact below

    if candidate.owner_user_id is not None:
        record_fact(
            db,
            fact_type=AnalyticsFactType.TERMINATED,
            candidate_id=candidate.id,
            owner_user_id=candidate.owner_user_id,
            fact_at=termination.terminated_at,
            source=candidate.source.value,
            termination_id=termination.id,
        )

    _audit_candidate(
        db,
        request,
        AuditAction.CANDIDATE_TERMINATED,
        actor=user,
        candidate=candidate,
        # Reason may contain free text — it is never written into audit
        # details (only the technical id of the termination record).
        details=f"termination={termination.id}",
        commit=False,
    )
    db.commit()
    db.refresh(termination)
    return CandidateTerminationOut.model_validate(termination)


@router.get(
    "/{candidate_id}/terminations",
    response_model=CandidateTerminationList,
    summary="List the candidate termination records",
)
def list_terminations(
    candidate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CandidateTerminationList:
    """All termination records of a candidate, newest first, with the same
    visibility rules as the candidate card."""
    candidate = _get_visible_candidate(db, candidate_id, user)

    total = (
        db.scalar(
            select(func.count())
            .select_from(CandidateTermination)
            .where(CandidateTermination.candidate_id == candidate.id)
        )
        or 0
    )
    terminations = db.scalars(
        select(CandidateTermination)
        .where(CandidateTermination.candidate_id == candidate.id)
        .order_by(CandidateTermination.terminated_at.desc(), CandidateTermination.id)
    ).all()
    return CandidateTerminationList(
        items=[CandidateTerminationOut.model_validate(t) for t in terminations],
        total=total,
    )
