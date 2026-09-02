"""Administrative user management endpoints (admin role only).

Every endpoint is protected server-side by :func:`app.deps.require_roles`;
hiding UI elements is never the security boundary. All changes are recorded
in the audit trail. A password is mandatory when creating a user and must
satisfy the password policy.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.db import get_db
from app.deps import require_roles
from app.models import AuditAction, User, UserRole
from app.schemas import UserCreate, UserList, UserOut, UserUpdate
from app.security import WeakPasswordError, hash_password, validate_password_policy
from app.utils import client_ip, user_agent, utc_now

router = APIRouter(prefix="/admin/users", tags=["admin-users"])

_admin_only = require_roles(UserRole.ADMIN)


def _audit(
    db: Session,
    request: Request,
    action: AuditAction,
    *,
    actor: User,
    subject: User | None = None,
    username: str | None = None,
    details: str | None = None,
    commit: bool = True,
) -> None:
    record_event(
        db,
        action,
        actor=actor,
        subject=subject,
        username=username or (subject.username if subject else None),
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=details,
        commit=commit,
    )


def _get_user_or_404(db: Session, user_id: str) -> User:
    try:
        parsed = UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден."
        ) from None
    user = db.get(User, parsed)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден.")
    return user


@router.get("", response_model=UserList, summary="List users (admin only)")
def list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(_admin_only),
) -> UserList:
    """Paginated list of users, newest first."""
    total = db.scalar(select(func.count()).select_from(User)) or 0
    users = db.scalars(
        select(User).order_by(User.created_at.desc(), User.username).limit(limit).offset(offset)
    ).all()
    return UserList(
        items=[UserOut.model_validate(u) for u in users], total=total, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user (admin only)",
)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
) -> UserOut:
    """Create a user with a mandatory password."""
    try:
        validate_password_policy(payload.password, username=payload.username)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    existing = db.scalar(select(User).where(func.lower(User.username) == payload.username.lower()))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким именем уже существует.",
        )

    user = User(
        username=payload.username,
        full_name=payload.full_name,
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _audit(
        db,
        request,
        AuditAction.USER_CREATED,
        actor=actor,
        subject=user,
        details=f"role={user.role.value}",
    )
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut, summary="Get a user (admin only)")
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(_admin_only),
) -> UserOut:
    user = _get_user_or_404(db, user_id)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut, summary="Update a user (admin only)")
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
) -> UserOut:
    """Update full name, role, active flag and/or password.

    An administrator cannot deactivate themselves, which would lock the last
    usable administrator out mid-operation.
    """
    user = _get_user_or_404(db, user_id)

    if payload.is_active is False and user.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя отключить собственную учётную запись.",
        )

    changes: list[str] = []

    if payload.full_name is not None and payload.full_name != user.full_name:
        user.full_name = payload.full_name
        changes.append("full_name")

    if payload.role is not None and payload.role != user.role:
        old_role = user.role
        user.role = payload.role
        changes.append(f"role: {old_role.value} -> {payload.role.value}")
        _audit(
            db,
            request,
            AuditAction.ROLE_CHANGED,
            actor=actor,
            subject=user,
            details=f"{old_role.value} -> {payload.role.value}",
            commit=False,
        )

    if payload.password is not None:
        try:
            validate_password_policy(payload.password, username=user.username)
        except WeakPasswordError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        user.password_hash = hash_password(payload.password)
        changes.append("password")

    if payload.is_active is not None and payload.is_active != user.is_active:
        user.is_active = payload.is_active
        changes.append(f"is_active: {payload.is_active}")
        if payload.is_active:
            # Reactivation also clears lockout state.
            user.failed_login_count = 0
            user.locked_until = None

    if not changes:
        return UserOut.model_validate(user)

    user.updated_at = utc_now()
    db.commit()
    db.refresh(user)
    _audit(
        db,
        request,
        AuditAction.USER_UPDATED,
        actor=actor,
        subject=user,
        details="; ".join(changes),
    )
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/unlock",
    response_model=UserOut,
    summary="Clear a login lockout (admin only)",
)
def unlock_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
) -> UserOut:
    """Reset failed-login counter and lockout for a user."""
    user = _get_user_or_404(db, user_id)
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_at = utc_now()
    db.commit()
    db.refresh(user)
    _audit(db, request, AuditAction.USER_UNLOCKED, actor=actor, subject=user)
    return UserOut.model_validate(user)
