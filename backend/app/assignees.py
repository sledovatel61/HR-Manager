"""Assignable-HR directory shared by the event and transfer owners.

Why this module exists
----------------------
UX feedback of 2026-09-29 (block A) reported that a manager/administrator
creating a calendar event saw an **empty «Исполнитель»** picker and could
not save the event at all («Выберите исполнителя — активного пользователя с
ролью HR»).

The cause was not a broken request but a role rule that did not fit the pilot
installation described in ``PRODUCT_SPEC.md`` §2: *«Начальная учётная запись
должна совмещать полномочия HR, руководителя и администратора»*.  Such an
account carries the server role ``admin`` plus an explicit, audited
``pilot_full_access`` grant — so on a single-seat stand there is simply **no
account with role ``hr``** and the directory was empty by construction.

The rule implemented here stays narrow and fully server-side:

* an assignee is a user who may legitimately *hold the HR working role*: an
  active account with role ``hr``, or an active account holding an active
  ``pilot_full_access`` grant (the pilot account that PRODUCT_SPEC defines as
  combining HR + manager + administrator powers);
* nothing here widens *visibility*.  An assignee still only sees the
  candidates the existing visibility rules allow (``_can_see_event``,
  ``_can_see``);
* the previous rule is unchanged for everyone else: a non-HR without the
  pilot grant must still explicitly pick an active assignee, and a plain HR
  may only assign events to itself.

The same predicate backs the directory endpoint (``GET /admin/users/hr``), the
event assignee resolution (create + update) and the candidate transfer target,
so the picker and the validation can never disagree again.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.orm import Session

from app.models import AccessGrant, AccessGrantScope, User, UserRole

_PILOT_SCOPE = AccessGrantScope.PILOT_FULL_ACCESS

#: Message used wherever a non-HR must explicitly choose an assignee.  Kept
#: stable because the frontend matches on it to offer the recovery action.
NO_ASSIGNEE_DETAIL = (
    "Руководитель и администратор должны явно указать исполнителя — "
    "активного пользователя с ролью HR."
)

#: Rejection message for an assignee id that does not resolve to an active,
#: assignable account.  Shared by events and candidate transfers.
INVALID_ASSIGNEE_DETAIL = (
    "Исполнитель должен быть активным пользователем с ролью HR "
    "либо учётной записью пилота с полным доступом."
)


def _pilot_grant_exists() -> ColumnElement[bool]:
    """``EXISTS`` subquery: an active pilot grant held by the ``users`` row."""
    return (
        select(AccessGrant.id)
        .where(
            AccessGrant.user_id == User.id,
            AccessGrant.scope == _PILOT_SCOPE,
            AccessGrant.revoked_at.is_(None),
        )
        .exists()
    )


def assignable_condition() -> ColumnElement[bool]:
    """SQL predicate: active users that may legitimately be an assignee.

    ``EXISTS`` (not a join) keeps the predicate composable with any user
    query and avoids row duplication when more than one grant ever exists.
    """
    return and_(
        User.is_active.is_(True),
        or_(User.role == UserRole.HR, _pilot_grant_exists()),
    )


def apply_assignable(stmt: Select) -> Select:
    """Restrict a statement over ``users`` to assignable accounts."""
    return stmt.where(assignable_condition())


def list_assignable_users(db: Session) -> list[User]:
    """Active users that may be picked as an assignee, ordered by login."""
    return list(db.scalars(apply_assignable(select(User)).order_by(User.username, User.id)).all())


def is_assignable(db: Session, user: User) -> bool:
    """True when ``user`` may hold the HR working role (active account)."""
    if not user.is_active:
        return False
    if user.role == UserRole.HR:
        return True
    return (
        db.scalar(
            select(AccessGrant.id).where(
                AccessGrant.user_id == user.id,
                AccessGrant.scope == _PILOT_SCOPE,
                AccessGrant.revoked_at.is_(None),
            )
        )
        is not None
    )


def resolve_assignee(
    db: Session,
    requested_id: UUID | None,
    *,
    missing_detail: str = NO_ASSIGNEE_DETAIL,
    invalid_detail: str = INVALID_ASSIGNEE_DETAIL,
) -> User:
    """Load the requested assignee or raise 422 with a recoverable message.

    Callers decide *whether* an assignee is required (a plain HR defaults to
    itself); this helper only validates that an explicitly requested assignee
    is a real, active, assignable account.
    """
    if requested_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=missing_detail)
    assignee = db.get(User, requested_id)
    if assignee is None or not is_assignable(db, assignee):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=invalid_detail)
    return assignee
