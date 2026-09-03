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

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    CANDIDATE_STAGE_POSITION,
    AuditAction,
    Candidate,
    CandidateInteraction,
    CandidateSource,
    CandidateStage,
    User,
    UserRole,
)
from app.schemas import (
    CandidateCreate,
    CandidateList,
    CandidateOut,
    CandidateUpdate,
    DuplicateCandidateDetail,
    InteractionCreate,
    InteractionList,
    InteractionOut,
)
from app.utils import (
    client_ip,
    normalize_email,
    normalize_full_name,
    normalize_phone,
    user_agent,
    utc_now,
)

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
    ids). Phone/email/name are never passed here.
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
    include_deleted: bool = False,
) -> Select[tuple[Candidate]]:
    """Shared filter builder for list/count queries."""
    conditions = _scope_for_user(user)
    if not include_deleted:
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
    * HRs always see only their own candidates regardless of ``owner_id``;
      managers/admins may filter by owner;
    * soft-deleted candidates are excluded.
    """
    stmt = _build_list_query(user, query=query, stage=stage, owner_id=owner_id, source=source)

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
        owner_user_id=owner.id,
        stage=CandidateStage.NEW,
        stage_position=CANDIDATE_STAGE_POSITION[CandidateStage.NEW],
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)

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
    )
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
        changes.append("position")

    stage_changed = False
    if payload.stage is not None and payload.stage != candidate.stage:
        old_stage = candidate.stage
        candidate.stage = payload.stage
        candidate.stage_position = CANDIDATE_STAGE_POSITION[payload.stage]
        stage_changed = True

    if not changes and not stage_changed:
        return CandidateOut.model_validate(candidate)

    candidate.updated_at = utc_now()
    db.commit()
    db.refresh(candidate)

    if stage_changed:
        _audit_candidate(
            db,
            request,
            AuditAction.CANDIDATE_STAGE_CHANGED,
            actor=user,
            candidate=candidate,
            details=f"{old_stage.value} -> {candidate.stage.value}",
        )
    if changes:
        _audit_candidate(
            db,
            request,
            AuditAction.CANDIDATE_UPDATED,
            actor=user,
            candidate=candidate,
            details="; ".join(changes),
        )
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
    db.commit()
    db.refresh(interaction)

    # Interaction comments may contain personal data — they are never logged
    # or written into audit details; only type + candidate id are recorded.
    _audit_candidate(
        db,
        request,
        AuditAction.CANDIDATE_INTERACTION_ADDED,
        actor=user,
        candidate=candidate,
        details=f"type={interaction.type.value}",
    )
    return InteractionOut.model_validate(interaction)
