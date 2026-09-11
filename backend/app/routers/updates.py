"""API канала обновлений Windows-пилота (Phase 13).

Архитектура минимальной поверхности:
  - бэкенд (контейнер) проверяет подписанный manifest доверенными ключами
    серверной конфигурации и скачивает пакет в bind-mounted staging
    (UPDATE_STAGING_DIR, контейнерный путь /updates → host-каталог из
    pilot.env); распаковку и установку выполняет ТОЛЬКО Windows-движок
    (повторная проверка manifest/пакета и безопасная распаковка на host);
  - движок-наблюдатель (host) опрашивает /updates/engine-state по loopback
    с машинным токеном UPDATE_ENGINE_TOKEN, выполняет команду install
    через существующий Phase 12 update engine и сообщает результат в
    /updates/engine-report; фоновую проверку движок запрашивает через
    /updates/engine-check (сервер троттлит по интервалу; установка фоном
    не запускается никогда);
  - UI работает только с серверными эндпоинтами: клиент не передаёт URL,
    пути, команды, manifest payload или release SHA.

Безопасность: RBAC + подтверждённые scope, CSRF на мутирующих эндпоинтах,
rate limit (per-user для команд, per-IP для движковых), аудит с
безопасными кодами ошибок (без URL/путей/секретов).
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_event
from app.channel import channel_enabled, compare_semver, download_package, verified_manifest
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user
from app.models import AccessGrant, AccessGrantScope, AuditAction, User, UserRole
from app.rate_limiting import SlidingWindowRateLimiter
from app.schemas import (
    UpdateEnginePollResponse,
    UpdateEngineReportRequest,
    UpdateInstallResponse,
    UpdateStatusResponse,
)
from app.update_channel_contract import ChannelError
from app.update_state import (
    ReportOutcome,
    UpdateChannelError,
    UpdateState,
    UpdateStateStore,
)

router = APIRouter(prefix="/updates", tags=["updates"])

# --- Rate limiting ------------------------------------------------------------

_user_limiter: SlidingWindowRateLimiter | None = None
_engine_limiter: SlidingWindowRateLimiter | None = None


def _get_user_limiter() -> SlidingWindowRateLimiter:
    global _user_limiter
    if _user_limiter is None:
        _user_limiter = SlidingWindowRateLimiter(limit=30, window_seconds=300)
    return _user_limiter


def _get_engine_limiter() -> SlidingWindowRateLimiter:
    global _engine_limiter
    if _engine_limiter is None:
        _engine_limiter = SlidingWindowRateLimiter(limit=120, window_seconds=300)
    return _engine_limiter


def reset_update_limiters() -> None:
    global _user_limiter, _engine_limiter
    _user_limiter = None
    _engine_limiter = None


# --- Хранилище/настройки --------------------------------------------------------


def _store(request: Request) -> UpdateStateStore:
    return request.app.state.update_store


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# --- Авторизация -----------------------------------------------------------------


def _has_active_grant(db: Session, user: User, scope: AccessGrantScope) -> bool:
    return (
        db.execute(
            select(AccessGrant).where(
                AccessGrant.user_id == user.id,
                AccessGrant.scope == scope,
                AccessGrant.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        is not None
    )


def _status_viewer(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if _has_active_grant(db, user, AccessGrantScope.PILOT_FULL_ACCESS):
        return user
    if _has_active_grant(db, user, AccessGrantScope.UPDATE_CHANNEL_MANAGE):
        return user
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для просмотра канала.")


def _channel_admin(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Только администратор.")
    if not _has_active_grant(db, user, AccessGrantScope.UPDATE_CHANNEL_MANAGE):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Требуется подтверждённый scope update_channel_manage.",
        )
    return user


def _user_rate_check(user: User) -> None:
    result = _get_user_limiter().check(f"updates:user:{user.id}")
    if not result.allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов.")


def _engine_token_check(request: Request) -> None:
    settings = _settings(request)
    if not settings.update_engine_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Движковый интерфейс отключён.")
    presented = request.headers.get("x-engine-token", "")
    if not presented or presented != settings.update_engine_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный токен движка.")
    ip = request.client.host if request.client else "unknown"
    result = _get_engine_limiter().check(f"updates:engine:{ip}")
    if not result.allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов.")


# --- Политика версий --------------------------------------------------------------


def _version_decision(store: UpdateStateStore, manifest: dict) -> tuple[str, str]:
    """Fail closed: downgrade, конфликт целостности, minimum supported version."""
    snapshot = store.snapshot()
    installed = snapshot.installed_version or "0.0.0"
    available = manifest["version"]
    comparison = compare_semver(available, installed)
    if comparison < 0:
        raise ChannelError(
            UpdateChannelError.DOWNGRADE, f"downgrade запрещён: {available} < {installed}"
        )
    if comparison == 0:
        installed_sha = snapshot.installed_release_sha
        if installed_sha and manifest["release_sha"] != installed_sha:
            raise ChannelError(
                UpdateChannelError.INTEGRITY_CONFLICT,
                "та же версия с другим release_sha — конфликт целостности",
            )
        return UpdateState.UP_TO_DATE, ""
    if compare_semver(installed, manifest["minimum_supported_version"]) < 0:
        raise ChannelError(
            UpdateChannelError.BELOW_MINIMUM,
            f"текущая версия {installed} ниже minimum_supported_version "
            f"{manifest['minimum_supported_version']}",
        )
    return UpdateState.AVAILABLE, ""


def _snapshot_response(store: UpdateStateStore, settings: Settings) -> UpdateStatusResponse:
    snapshot = store.snapshot()
    return UpdateStatusResponse(
        state=snapshot.state,
        installed_version=snapshot.installed_version or settings.update_installed_version,
        installed_release_sha=snapshot.installed_release_sha or settings.update_installed_sha,
        available_version=snapshot.available_version,
        available_release_sha=snapshot.available_release_sha,
        available_published_at=snapshot.available_published_at,
        notes_ru=snapshot.notes_ru,
        download_progress=snapshot.download_progress,
        last_check_at=snapshot.last_check_at,
        last_check_ok=snapshot.last_check_ok,
        error_code=snapshot.error_code,
        last_result=snapshot.last_result,
        channel_configured=channel_enabled(settings),
    )


def _perform_check(
    store: UpdateStateStore,
    settings: Settings,
    db: Session,
    actor: User | None,
) -> UpdateStatusResponse:
    """Общая логика проверки (пользовательская и фоновая от движка)."""
    if not channel_enabled(settings):
        store.set_failed(UpdateChannelError.NOT_CONFIGURED)
        return _snapshot_response(store, settings)
    if not store.begin_check():
        raise HTTPException(status.HTTP_409_CONFLICT, "Действие уже выполняется.")
    if actor is not None:
        record_event(
            db,
            AuditAction.UPDATE_CHECK_STARTED,
            actor=actor,
            details="channel=stable",
            commit=True,
        )
    try:
        manifest = verified_manifest(settings)
        try:
            decision, _reason = _version_decision(store, manifest)
        except ChannelError as exc:
            store.set_failed(exc.code)
            if actor is not None:
                record_event(
                    db,
                    AuditAction.UPDATE_CHECK_FAILED,
                    actor=actor,
                    details=f"code={exc.code}",
                    commit=True,
                )
            return _snapshot_response(store, settings)
        if decision == UpdateState.UP_TO_DATE:
            store.set_up_to_date()
        else:
            store.set_available(
                manifest["version"],
                manifest["release_sha"],
                manifest["published_at"],
                manifest.get("notes_ru"),
            )
        if actor is not None:
            record_event(
                db,
                AuditAction.UPDATE_CHECK_SUCCEEDED,
                actor=actor,
                details=(
                    f"version={manifest['version']} sha={manifest['release_sha'][:12]} "
                    f"state={decision}"
                ),
                commit=True,
            )
        return _snapshot_response(store, settings)
    except ChannelError as exc:
        store.set_failed(exc.code)
        if actor is not None:
            record_event(
                db,
                AuditAction.UPDATE_CHECK_FAILED,
                actor=actor,
                details=f"code={exc.code}",
                commit=True,
            )
        return _snapshot_response(store, settings)
    finally:
        store.finish_action()


# --- Пользовательские эндпоинты ----------------------------------------------------


@router.get("/status", response_model=UpdateStatusResponse, summary="Состояние канала")
def updates_status(
    request: Request,
    user: User = Depends(_status_viewer),
) -> UpdateStatusResponse:
    return _snapshot_response(_store(request), _settings(request))


@router.post("/check", response_model=UpdateStatusResponse, summary="Проверить обновления")
def updates_check(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_channel_admin),
) -> UpdateStatusResponse:
    _user_rate_check(user)
    return _perform_check(_store(request), _settings(request), db, actor=user)


@router.post("/download", response_model=UpdateStatusResponse, summary="Скачать и проверить пакет")
def updates_download(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_channel_admin),
) -> UpdateStatusResponse:
    _user_rate_check(user)
    settings = _settings(request)
    store = _store(request)
    if store.snapshot().state == UpdateState.READY:
        return _snapshot_response(store, settings)
    if store.snapshot().state != UpdateState.AVAILABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Сначала выполните проверку обновлений.")
    if not store.begin_download():
        raise HTTPException(status.HTTP_409_CONFLICT, "Действие уже выполняется.")
    record_event(
        db, AuditAction.UPDATE_DOWNLOAD_STARTED, actor=user, details="channel=stable", commit=True
    )
    try:
        manifest = verified_manifest(settings)  # повторная проверка перед скачиванием
        try:
            decision, _reason = _version_decision(store, manifest)
        except ChannelError as exc:
            store.set_failed(exc.code)
            record_event(
                db,
                AuditAction.UPDATE_DOWNLOAD_FAILED,
                actor=user,
                details=f"code={exc.code}",
                commit=True,
            )
            return _snapshot_response(store, settings)
        if decision == UpdateState.UP_TO_DATE:
            store.set_up_to_date()
            return _snapshot_response(store, settings)
        package_path = download_package(settings, manifest)
        # Подписанный manifest сохраняется рядом с пакетом: движок повторно
        # проверит подпись/хэш перед распаковкой (защита host-стороны).
        manifest_path = package_path.with_suffix(".json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
        store.set_ready(str(package_path), str(manifest_path))
        record_event(
            db,
            AuditAction.UPDATE_DOWNLOAD_SUCCEEDED,
            actor=user,
            details=f"version={manifest['version']} sha={manifest['release_sha'][:12]}",
            commit=True,
        )
        return _snapshot_response(store, settings)
    except ChannelError as exc:
        store.set_failed(exc.code)
        record_event(
            db,
            AuditAction.UPDATE_DOWNLOAD_FAILED,
            actor=user,
            details=f"code={exc.code}",
            commit=True,
        )
        return _snapshot_response(store, settings)
    finally:
        store.finish_action()


@router.post("/install", response_model=UpdateInstallResponse, summary="Запросить установку")
def updates_install(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_channel_admin),
) -> UpdateInstallResponse:
    _user_rate_check(user)
    settings = _settings(request)
    store = _store(request)
    allowed, reason = store.engine_can_install()
    if not allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, reason)
    try:
        manifest = verified_manifest(settings)
        decision, _reason = _version_decision(store, manifest)
        if decision == UpdateState.UP_TO_DATE:
            store.set_up_to_date()
            store.release_install_action()
            return UpdateInstallResponse(state=UpdateState.UP_TO_DATE, message="Уже установлено.")
    except ChannelError as exc:
        store.set_failed(exc.code)
        store.release_install_action()
        return UpdateInstallResponse(state=UpdateState.FAILED, message=exc.code)
    job_id = uuid.uuid4().hex[:16]
    store.mark_installing(job_id)
    store.add_pending_action("install")
    record_event(
        db,
        AuditAction.UPDATE_INSTALL_REQUESTED,
        actor=user,
        details=(
            f"version={manifest['version']} sha={manifest['release_sha'][:12]} job_id={job_id}"
        ),
        commit=True,
    )
    return UpdateInstallResponse(state=UpdateState.INSTALLING, job_id=job_id)


# --- Движковые эндпоинты (loopback + машинный токен) ----------------------------------


@router.get(
    "/engine-state",
    response_model=UpdateEnginePollResponse,
    summary="Опрос движком серверных команд",
)
def engine_state(
    request: Request,
) -> UpdateEnginePollResponse:
    _engine_token_check(request)
    store = _store(request)
    header_version = request.headers.get("x-installed-version", "")
    header_sha = request.headers.get("x-installed-sha", "")
    if header_version:
        store.set_engine_installed(header_version, header_sha)
    actions = store.engine_pending_actions()
    if actions:
        # Доставка с re-delivery: опрос НЕ считается подтверждением —
        # команда с тем же неизменным job_id и server-owned путями выдаётся
        # повторно до terminal report (движок идемпотентен/resume-safe).
        return UpdateEnginePollResponse(
            actions=actions,
            job_id=store.install_job_id(),
            release_dir=store.downloaded_dir(),
            manifest_path=store.manifest_path(),
            error_code=None,
        )
    return UpdateEnginePollResponse(actions=[], job_id=None, release_dir=None, error_code=None)


@router.post(
    "/engine-check",
    response_model=UpdateStatusResponse,
    summary="Фоновая проверка от движка (сервер троттлит по интервалу)",
)
def engine_check(
    request: Request,
    db: Session = Depends(get_db),
) -> UpdateStatusResponse:
    _engine_token_check(request)
    store = _store(request)
    settings = _settings(request)
    snapshot = store.snapshot()
    last = snapshot.last_check_at
    if last:
        from datetime import datetime

        try:
            last_dt = datetime.fromisoformat(last)
            elapsed = (datetime.now(last_dt.tzinfo) - last_dt).total_seconds()
        except ValueError:
            elapsed = float("inf")
        if elapsed < settings.update_check_min_interval_seconds:
            # Троттлинг: повторная проверка раньше интервала — просто текущее состояние.
            return _snapshot_response(store, settings)
    header_version = request.headers.get("x-installed-version", "")
    header_sha = request.headers.get("x-installed-sha", "")
    if header_version:
        store.set_engine_installed(header_version, header_sha)
    return _perform_check(store, settings, db, actor=None)


@router.post(
    "/engine-report",
    response_model=UpdateStatusResponse,
    summary="Отчёт движка после update (result/rollback/fail)",
)
def engine_report(
    request: Request,
    payload: UpdateEngineReportRequest,
    db: Session = Depends(get_db),
) -> UpdateStatusResponse:
    _engine_token_check(request)
    store = _store(request)
    settings = _settings(request)
    outcome = store.apply_engine_report(
        payload.job_id,
        payload.state,
        payload.installed_version,
        payload.installed_release_sha,
        payload.error_code,
    )
    if outcome == ReportOutcome.NO_ACTIVE_JOB:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Нет активной операции установки для отчёта."
        )
    if outcome == ReportOutcome.WRONG_JOB:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Несовпадение job_id с активной операцией."
        )
    if outcome == ReportOutcome.CONFLICT:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Противоречащий повтор отчёта для завершённого job."
        )
    if outcome == ReportOutcome.APPLIED:
        # Lock освобождается ровно один раз и только владельцем активной
        # операции; повторные (DUPLICATE) отчёты его не трогают.
        store.release_install_action()
        record_event(
            db,
            AuditAction.UPDATE_ENGINE_REPORTED,
            details=(
                f"state={payload.state} version={payload.installed_version} "
                f"sha={(payload.installed_release_sha or '')[:12]} "
                f"code={payload.error_code or 'none'}"
            ),
            commit=True,
        )
    return _snapshot_response(store, settings)
