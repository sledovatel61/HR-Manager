"""Analytics endpoints (Phase 6).

RBAC: analytics is a team-level report — accessible to manager/admin ONLY.
An authenticated HR gets 403 (never silently filtered team statistics); an
anonymous request gets 401 via ``get_current_user``.

All endpoints take the same period/filter parameters:

* ``from`` / ``to`` — ISO 8601 datetimes with an offset/Z (naive = UTC),
  ``from <= fact_at < to`` (half-open, from inclusive, to exclusive);
* ``timezone`` — IANA name, default ``UTC``; validated and echoed back.
  The server never converts facts with it — the client uses it to compute
  reproducible preset boundaries;
* ``hr_id`` / ``source`` — optional team filters (manager/admin only).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app import analytics
from app.audit import record_event
from app.db import get_db
from app.deps import get_current_user
from app.models import AuditAction, CandidateSource, User, UserRole
from app.schemas import AnalyticsFunnelResponse, AnalyticsKpiResponse
from app.utils import client_ip, user_agent

router = APIRouter(prefix="/analytics", tags=["analytics"])

_PERIOD_HELP = "Начало периода (ISO 8601, полуинтервал [from, to))"
_TO_HELP = "Окончание периода (ISO 8601, полуинтервал [from, to))"


def _require_analytics_access(user: User) -> None:
    """403 for authenticated non-managers — never filtered team stats."""
    if user.role not in (UserRole.MANAGER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ к аналитике разрешён только менеджеру или администратору.",
        )


def _resolve_query(
    db: Session,
    from_dt: datetime,
    to_dt: datetime,
    timezone: str,
    hr_id: uuid.UUID | None,
    source: CandidateSource | None,
) -> analytics.PeriodQuery:
    return analytics.resolve_period_query(
        db,
        from_dt=from_dt,
        to_dt=to_dt,
        timezone=timezone,
        hr_id=hr_id,
        source=source,
    )


@router.get(
    "/kpi",
    response_model=AnalyticsKpiResponse,
    summary="KPI отчёты команды за период",
)
def kpi(
    request: Request,
    from_: datetime = Query(alias="from", description=_PERIOD_HELP),
    to: datetime = Query(description=_TO_HELP),
    timezone: str = Query(default="UTC", description="IANA таймзона (по умолчанию UTC)"),
    hr_id: uuid.UUID | None = Query(default=None, description="Фильтр по ответственному HR"),
    source: CandidateSource | None = Query(default=None, description="Фильтр по источнику"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_analytics_access(user)
    pq = _resolve_query(db, from_, to, timezone, hr_id, source)
    return analytics.build_kpi_report(db, pq)


@router.get(
    "/funnel",
    response_model=AnalyticsFunnelResponse,
    summary="Воронка найма и конверсии между этапами",
)
def funnel(
    request: Request,
    from_: datetime = Query(alias="from", description=_PERIOD_HELP),
    to: datetime = Query(description=_TO_HELP),
    timezone: str = Query(default="UTC", description="IANA таймзона (по умолчанию UTC)"),
    hr_id: uuid.UUID | None = Query(default=None, description="Фильтр по ответственному HR"),
    source: CandidateSource | None = Query(default=None, description="Фильтр по источнику"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_analytics_access(user)
    pq = _resolve_query(db, from_, to, timezone, hr_id, source)
    return analytics.build_funnel_report(db, pq)


@router.get(
    "/export",
    response_class=Response,
    summary="Экспорт текущего отчёта в CSV",
)
def export(
    request: Request,
    format: str = Query(description="Формат экспорта (только csv)"),
    from_: datetime = Query(alias="from", description=_PERIOD_HELP),
    to: datetime = Query(description=_TO_HELP),
    timezone: str = Query(default="UTC", description="IANA таймзона (по умолчанию UTC)"),
    hr_id: uuid.UUID | None = Query(default=None, description="Фильтр по ответственному HR"),
    source: CandidateSource | None = Query(default=None, description="Фильтр по источнику"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    _require_analytics_access(user)
    if format != "csv":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Поддерживается только формат экспорта csv.",
        )
    pq = _resolve_query(db, from_, to, timezone, hr_id, source)
    body, filename = analytics.build_csv(db, pq)
    content = "\ufeff" + body  # UTF-8 BOM for Excel

    # Audit the export itself — parameters only, never report content. The
    # audit write commits here; if it fails, no success response is produced.
    record_event(
        db,
        AuditAction.ANALYTICS_EXPORTED,
        actor=user,
        ip_address=client_ip(request),
        user_agent=user_agent(request.headers),
        details=(
            f"from={pq.from_dt.isoformat()} to={pq.to_dt.isoformat()} "
            f"timezone={pq.timezone.key} "
            f"hr_id={pq.hr_id if pq.hr_id else ''} "
            f"source={pq.source.value if pq.source else ''}"
        ),
        commit=True,
    )

    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
