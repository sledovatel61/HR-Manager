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

# Exact paths or directory prefixes that are allowed without license.
# For directory prefixes, we allow exact match and any subpath with "/".
# For dash prefixes (engine-), we allow any path starting with that prefix.
ALLOWED_EXACT_OR_DIR: tuple[str, ...] = (
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
    "/api/setup",
    "/setup",
    "/docs",
    "/openapi.json",
    "/redoc",
)

ALLOWED_DASH_PREFIXES: tuple[str, ...] = (
    "/api/updates/engine-",
    "/updates/engine-",
)


def _normalize_path(p: str) -> str:
    # Collapse multiple slashes, ensure leading slash
    if not p.startswith("/"):
        p = "/" + p
    while "//" in p:
        p = p.replace("//", "/")
    # Remove trailing slash for comparison, but keep root "/"
    # We keep trailing slash handling in _is_allowed via prefix+ "/"
    return p


def _is_allowed(path: str) -> bool:
    # Normalize incoming path
    path = _normalize_path(path)
    candidates = [path]
    if path.startswith("/api/"):
        # Also check without /api prefix (for frontend calling /candidates etc)
        candidates.append(_normalize_path(path[4:]))
    else:
        # Also check with /api prefix
        candidates.append(_normalize_path("/api" + path))

    for cand in candidates:
        # Check dash prefixes first (intentional prefix match)
        for pref in ALLOWED_DASH_PREFIXES:
            if cand.startswith(pref):
                return True
        # Check exact or dir prefixes with slash boundary
        for pref in ALLOWED_EXACT_OR_DIR:
            if cand == pref or cand.startswith(pref + "/"):
                return True
            # Special: allow /docs and /docs/*, /openapi.json exact only?
            # For openapi.json and redoc, exact match is enough, but also allow with trailing slash? No.
            # For /docs, we already allow via pref + "/" -> /docs/...
    return False


class LicenseGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        # Normalize for allowed check, but keep original for logging
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
        # For blocking, we are intentionally permissive: any path that looks like protected should be checked.
        # Use slash boundary for most, but also allow exact.
        normalized = _normalize_path(path)
        is_api_like = normalized.startswith("/api/") or any(
            normalized == r.rstrip("/") or normalized.startswith(r.rstrip("/") + "/") or normalized.startswith(r)
            for r in protected_roots
        )
        # The above includes both strict and permissive checks to ensure we don't miss protected paths.
        # For extra safety, also consider any path that starts with protected root even without slash (to block /administer etc as protected)
        # Actually for blocking, we want to block /administer as well if it looks like admin.
        # So we keep permissive startswith for blocking.
        if not is_api_like:
            # Re-evaluate with permissive startswith for blocking unknown but similar paths
            permissive_block = any(
                normalized.startswith(pr) for pr in ("/candidates", "/events", "/admin", "/users", "/documents", "/analytics", "/license", "/auth", "/setup")
            )
            # But we already allowed safe paths via _is_allowed, so if not allowed and looks like api, block.
            if normalized.startswith("/api/"):
                is_api_like = True
            elif permissive_block and not _is_allowed(normalized):
                # If it looks like a protected area but not explicitly allowed, treat as protected
                is_api_like = True

        if not is_api_like:
            if not normalized.startswith("/api/") and not normalized.startswith("/"):
                return await call_next(request)
            if normalized in ("/", "/index.html") or "/assets/" in normalized:
                return await call_next(request)
            if normalized.startswith("/static/"):
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
