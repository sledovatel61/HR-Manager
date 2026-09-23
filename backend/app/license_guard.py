"""License guard — server-side enforcement for offline pilot license.

Allowed without license:
- /api/auth/login, /api/auth/me, /api/auth/logout
- /api/license/* (status + upload)
- /api/health, /api/ops/status, /api/ops/backup-health
- /api/admin/ops/pilot-readiness
- /api/updates/engine-* (host report, state, check, report)
- /api/setup/* (first-run)
- /docs, /openapi.json, /redoc

All other /api/* require valid license when enforcement enabled.
On expiry: normal work stops (403) but data not deleted; admin can login
and upload new license.
No private key, license texts or PII in logs — only redacted fingerprints.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.db import SessionLocal
from app.license import LicenseError, fingerprint_public_key
from app.services.license_service import validate_current_license

logger = logging.getLogger(__name__)

ALLOWED_PREFIXES: tuple[str, ...] = (
    "/api/auth/login",
    "/auth/login",
    "/api/auth/me",
    "/auth/me",
    "/api/auth/logout",
    "/auth/logout",
    "/api/license",
    "/license",
    "/api/health",
    "/health",
    "/api/ops/status",
    "/ops/status",
    "/api/ops/backup-health",
    "/ops/backup-health",
    "/api/admin/ops/pilot-readiness",
    "/admin/ops/pilot-readiness",
    "/api/updates/engine-",
    "/updates/engine-",
    "/api/setup",
    "/setup",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def _is_allowed(path: str) -> bool:
    candidates = [path]
    if path.startswith("/api/"):
        candidates.append(path[4:])
    else:
        candidates.append("/api" + path)

    for cand in candidates:
        for pref in ALLOWED_PREFIXES:
            if cand == pref or cand.startswith(pref):
                return True
    return False


class LicenseGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path

        if _is_allowed(path):
            return await call_next(request)

        protected_roots = (
            "/candidates",
            "/events",
            "/admin/",
            "/admin",
            "/users",
            "/audit",
            "/analytics",
            "/notifications",
            "/reminders",
            "/notification-preferences",
            "/integrations",
            "/documents",
            "/document-",
            "/updates",
            "/license",
        )
        is_api_like = path.startswith("/api/") or any(
            path == r.rstrip("/") or path.startswith(r) for r in protected_roots
        )
        if not is_api_like:
            if not path.startswith("/api/") and not path.startswith("/"):
                return await call_next(request)
            if path in ("/", "/index.html") or "/assets/" in path:
                return await call_next(request)
            if path.startswith("/static/"):
                return await call_next(request)
            if not is_api_like:
                return await call_next(request)

        try:
            state_settings = getattr(request.app.state, "settings", None)
            settings = state_settings if state_settings is not None else get_settings()
        except Exception:
            settings = get_settings()
        public_b64 = (settings.license_public_key or "").strip()
        if not public_b64:
            return await call_next(request)

        try:
            with SessionLocal() as db:
                validate_current_license(db, settings)
        except LicenseError as exc:
            logger.warning(
                "license guard blocked %s code=%s fp=%s",
                path,
                exc.code,
                fingerprint_public_key(public_b64),
            )
            from fastapi.responses import JSONResponse

            if exc.code == "no_license":
                detail = (
                    "Лицензия не установлена. Загрузите файл лицензии "
                    "в разделе Лицензия. Администратор может войти."
                )
            elif exc.code == "expired" or exc.code == "clock_rollback":
                detail = str(exc)
            else:
                detail = f"Лицензия недействительна ({exc.code}): {exc}"

            return JSONResponse(
                status_code=403,
                content={"detail": detail, "code": exc.code},
                headers={"X-License-Status": exc.code},
            )
        except Exception:
            logger.exception("license guard unexpected error for %s", path)
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=403,
                content={
                    "detail": ("Ошибка проверки лицензии. Обратитесь к администратору."),
                    "code": "check_failed",
                },
            )

        return await call_next(request)
