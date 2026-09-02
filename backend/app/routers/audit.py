"""Audit log endpoints (admin role only).

Read-only access to the append-only security audit trail. Supports server-side
pagination and optional filtering by event action and username.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_roles
from app.models import AuditAction, AuditEvent, User, UserRole
from app.schemas import AuditEventOut, AuditList

router = APIRouter(prefix="/admin/audit", tags=["admin-audit"])

_admin_only = require_roles(UserRole.ADMIN)


@router.get("", response_model=AuditList, summary="Audit trail (admin only)")
def list_audit_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: AuditAction | None = Query(default=None),
    username: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    _: User = Depends(_admin_only),
) -> AuditList:
    """Paginated audit events, newest first."""
    filters = []
    if action is not None:
        filters.append(AuditEvent.action == action)
    if username:
        filters.append(AuditEvent.username == username)

    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*filters)) or 0
    events = db.scalars(
        select(AuditEvent)
        .where(*filters)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return AuditList(
        items=[AuditEventOut.model_validate(event) for event in events],
        total=total,
        limit=limit,
        offset=offset,
    )
