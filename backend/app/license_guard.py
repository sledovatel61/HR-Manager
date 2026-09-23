# -*- coding: utf-8 -*-
"""License guard — server-side enforcement for offline pilot license.

Allowed without license (even when expired/missing):
- /api/auth/login, /api/auth/me, /api/auth/logout
- /api/license/* (status + upload)
- /api/health, /api/ops/status, /api/ops/backup-health, /api/admin/ops/pilot-readiness
- /api/updates/engine-*
- /api/setup/* (first-run owner creation)
- /docs, /openapi.json, /redoc (dev convenience, never secret)

All other /api/* endpoints require a valid license when enforcement is enabled
(LICENSE_PUBLIC_KEY set, i.e. pilot/production). Enforcement disabled when
public key empty (dev/test).

On expiry: normal work stops (403) but data not deleted; admin can still login
and upload new license.

No private key, license texts or PII in logs — only redacted fingerprints.
"""

from __future__ import annotations

import logging
from typing import Iterable

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
    # Exact match or prefix, handling both /api/* and /* (nginx strips /api/)
    # Normalize: if path starts with /api/, also check without /api prefix
    candidates = [path]
    if path.startswith("/api/"):
        candidates.append(path[4:])  # strip /api
    else:
        candidates.append("/api" + path)

    for cand in candidates:
        for pref in ALLOWED_PREFIXES:
            if cand == pref or cand.startswith(pref):
                return True
    return False


class LicenseGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Allow docs, openapi, etc. without license check
        if _is_allowed(path):
            return await call_next(request)

        # For non-API paths (frontend static), allow
        # We guard only API-like paths: those that look like backend routes
        # To avoid breaking frontend, we allow anything not starting with /api/ and not a known API prefix
        # Known API prefixes without /api: /auth, /candidates, /events, /admin, /license, /setup, /updates, /ops, /health, etc.
        # If path is exactly / or /index.html or static assets, allow.
        # For simplicity, we guard any path that is not allowed and not docs/root.
        # But to keep first-run no deadlock, we already allowed /setup and /license and /auth via _is_allowed.
        # So remaining paths (e.g. /candidates, /events, /admin/users) should be guarded when enforcement enabled.
        # We also guard /api/* paths that are not allowed.
        # If path does not look like API (e.g. /assets/*, /static/*, /), allow.
        # Heuristic: if path starts with /api/ and not allowed, guard; if path starts with / and contains no dot and not root, treat as potential API and guard if enforcement enabled?
        # Simpler: guard if path starts with /api/ OR path starts with one of protected API roots.
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
        # Note: /license already allowed, but keep for completeness
        is_api_like = path.startswith("/api/") or any(path == r.rstrip("/") or path.startswith(r) for r in protected_roots)
        # Also allow root and frontend assets
        if not is_api_like:
            # If not API-like, allow (e.g. /, /index.html, /assets/*)
            # But also guard /api/* already handled by _is_allowed above, so if we reach here with /api/* not allowed, is_api_like True
            if not path.startswith("/api/") and not path.startswith("/"):
                return await call_next(request)
            # For frontend routes that are not API, allow
            if path in ("/", "/index.html") or "/assets/" in path or path.startswith("/static/"):
                return await call_next(request)
            # If path is API-like but not allowed, continue to license check below
            # If path is not API-like and not allowed, allow
            if not is_api_like:
                return await call_next(request)

        # At this point, path is API-like and not in allowed list -> enforce license

        # Use app.state.settings for test injection; fallback to global get_settings()
        try:
            settings = request.app.state.settings  # type: ignore[attr-defined]
        except Exception:
            settings = get_settings()
        public_b64 = (settings.license_public_key or "").strip()
        if not public_b64:
            # Enforcement disabled
            return await call_next(request)

        # Check license validity via DB
        try:
            with SessionLocal() as db:
                validate_current_license(db, settings)
        except LicenseError as exc:
            # Log redacted, not full license
            logger.warning(
                "license guard blocked %s code=%s fp=%s",
                path,
                exc.code,
                fingerprint_public_key(public_b64),
            )
            from fastapi.responses import JSONResponse

            # RU messages per spec
            if exc.code == "no_license":
                detail = "Лицензия не установлена. Загрузите файл лицензии в разделе Лицензия. Администратор может войти в систему для загрузки."
            elif exc.code == "expired":
                detail = str(exc)  # already RU with instruction for admin
            elif exc.code == "clock_rollback":
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
            # Fail open? No, fail closed but allow diagnostics? We fail closed with 403 and generic message
            # To avoid breaking health, we already allowed health. So 403.
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=403,
                content={"detail": "Ошибка проверки лицензии. Обратитесь к администратору.", "code": "check_failed"},
            )

        return await call_next(request)
