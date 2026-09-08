"""Candidate access scope shared by the phase-11 features (documents, rules).

The scope mirrors the phase-10 candidate-communication rules (the document
flow feeds candidate messages, so the same boundary applies):

* HR — only their own candidates;
* manager — the whole responsible area (all candidates);
* admin — NOT by role alone: only an explicit, active ``pilot_full_access``
  grant opens the candidate scope (the pilot keeps the accepted powers; a
  revoked grant closes the scope immediately);
* an inactive user has no scope at all; a soft-deleted candidate is never
  in anyone's scope.

Everything here is evaluated server-side from the CURRENT state — rules
re-check it at execution time and the worker re-checks it at send time.
"""

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.models import AccessGrant, AccessGrantScope, Candidate, User, UserRole


def has_active_pilot_grant(db: Session, user_id: object) -> bool:
    return (
        db.execute(
            select(AccessGrant.id).where(
                AccessGrant.user_id == user_id,
                AccessGrant.scope == AccessGrantScope.PILOT_FULL_ACCESS,
                AccessGrant.revoked_at.is_(None),
            )
        ).scalar()
        is not None
    )


def user_has_candidate_scope(db: Session, user: User) -> bool:
    """Whether the user may work with candidate documents/rules at all."""
    if not user.is_active:
        return False
    if user.role in (UserRole.HR, UserRole.MANAGER):
        return True
    return has_active_pilot_grant(db, user.id)


def candidate_scope_filters(user: User) -> list[ColumnElement[bool]]:
    """Query predicates restricting candidates to the user's scope.

    The caller must have checked :func:`user_has_candidate_scope` first
    (an admin without a grant has NO scope; these filters alone would show
    everything).
    """
    filters: list[ColumnElement[bool]] = [Candidate.deleted_at.is_(None)]
    if user.role == UserRole.HR:
        filters.append(Candidate.owner_user_id == user.id)
    return filters


def candidate_in_scope(db: Session, user: User, candidate: Candidate) -> bool:
    """Whether ``candidate`` is inside the user's CURRENT scope."""
    if candidate.deleted_at is not None:
        return False
    if not user_has_candidate_scope(db, user):
        return False
    if user.role == UserRole.HR:
        return candidate.owner_user_id == user.id
    return True
