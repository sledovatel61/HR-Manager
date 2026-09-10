"""Server-owned состояние канала обновлений Windows-пилота (Phase 13).

Один процесс (pilot), один host: состояние хранится в памяти приложения и
не требует миграции БД (промпт: Phase 13 по умолчанию без новой миграции;
серверное состояние после перезапуска честно возвращается в ``idle``).

Бэкенд НЕ выполняет команды и НЕ трогает Docker/host: двигатель —
Windows-движок. Контракт минимальной поверхности:
  - движок по машинному токену опрашивает серверные задания/конфигурацию
    канала (GET /api/updates/engine-state);
  - движок сообщает результат после update (POST /api/updates/engine-report);
  - пользовательский UI читает состояние и подаёт команды
    (check/download/install) только через серверные эндпоинты.

Всё состояние проверяется функциями этого модуля (инварианты переходов,
идемпотентность, блокировка одновременных действий). Никаких URL, путей
или команд от клиента — только значения из серверной конфигурации.
"""

from __future__ import annotations

import threading
from enum import StrEnum

from app.utils import utc_now

_INSTALLING_STATES = ("installing", "restart_required", "failed", "rolled_back")


class UpdateState(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    READY = "ready"
    INSTALLING = "installing"
    RESTART_REQUIRED = "restart_required"
    FAILED = "failed"
    MANUAL_ACTION_REQUIRED = "manual_action_required"


class UpdateChannelError(StrEnum):
    """Безопасные коды ошибок канала (без URL, путей и секретов)."""

    NOT_CONFIGURED = "channel_not_configured"
    OFFLINE = "channel_offline"
    BAD_SIGNATURE = "manifest_bad_signature"
    UNKNOWN_KEY = "manifest_unknown_key"
    REVOKED_KEY = "manifest_revoked_key"
    BAD_MANIFEST = "manifest_invalid"
    DOWNLOAD_FAILED = "package_download_failed"
    PACKAGE_MISMATCH = "package_hash_mismatch"
    PACKAGE_EXTRACT = "package_extract_failed"
    STAGING_FAILED = "staging_failed"
    UPGRADE_FAILED = "update_failed"
    DOWNGRADE = "downgrade_blocked"
    INTEGRITY_CONFLICT = "version_conflict"
    BELOW_MINIMUM = "manual_action_required"
    BUSY = "action_in_progress"
    ENGINE_FAILED = "engine_failed"


class UpdateStateSnapshot:
    """Итоговое состояние канала, отдаваемое UI и движку."""

    def __init__(
        self,
        state: str = UpdateState.IDLE,
        installed_version: str = "",
        installed_release_sha: str = "",
        available_version: str | None = None,
        available_release_sha: str | None = None,
        available_published_at: str | None = None,
        notes_ru: str | None = None,
        download_progress: int | None = None,
        last_check_at: str | None = None,
        last_check_ok: bool | None = None,
        error_code: str | None = None,
        last_result: str | None = None,
        engine_state: str | None = None,
    ) -> None:
        self.state = state
        self.installed_version = installed_version
        self.installed_release_sha = installed_release_sha
        self.available_version = available_version
        self.available_release_sha = available_release_sha
        self.available_published_at = available_published_at
        self.notes_ru = notes_ru
        self.download_progress = download_progress
        self.last_check_at = last_check_at
        self.last_check_ok = last_check_ok
        self.error_code = error_code
        self.last_result = last_result
        self.engine_state = engine_state


class UpdateStateStore:
    """Потокобезопасное хранилище состояния канала (один экземпляр на app)."""

    def __init__(self, installed_version: str = "", installed_release_sha: str = "") -> None:
        self._lock = threading.Lock()
        self._status = UpdateStateSnapshot(
            installed_version=installed_version,
            installed_release_sha=installed_release_sha,
        )
        self._pending_actions: list[str] = []
        self._downloaded_dir: str | None = None
        self._manifest_path: str | None = None
        self._engine_job_id: str | None = None
        self._action_lock = threading.Lock()

    # --- Чтение --------------------------------------------------------------

    def snapshot(self) -> UpdateStateSnapshot:
        with self._lock:
            return self._copy()

    def _copy(self) -> UpdateStateSnapshot:
        current = self._status
        return UpdateStateSnapshot(
            state=current.state,
            installed_version=current.installed_version,
            installed_release_sha=current.installed_release_sha,
            available_version=current.available_version,
            available_release_sha=current.available_release_sha,
            available_published_at=current.available_published_at,
            notes_ru=current.notes_ru,
            download_progress=current.download_progress,
            last_check_at=current.last_check_at,
            last_check_ok=current.last_check_ok,
            error_code=current.error_code,
            last_result=current.last_result,
            engine_state=current.engine_state,
        )

    # --- Мутации (только серверные вызовы) ------------------------------------

    def _update(self, **fields: object) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._status, key, value)

    def begin_check(self) -> bool:
        """Начало проверки: отказ, если уже идёт действие (идемпотентность)."""
        if not self._action_lock.acquire(blocking=False):
            return False
        self._update(
            state=UpdateState.CHECKING,
            error_code=None,
            last_result=None,
        )
        return True

    def begin_download(self) -> bool:
        if not self._action_lock.acquire(blocking=False):
            return False
        self._update(
            state=UpdateState.DOWNLOADING,
            download_progress=0,
            error_code=None,
            last_result=None,
        )
        return True

    def finish_action(self) -> None:
        self._action_lock.release()

    def set_up_to_date(self) -> None:
        self._update(
            state=UpdateState.UP_TO_DATE,
            last_check_at=utc_now().isoformat(),
            last_check_ok=True,
            error_code=None,
        )

    def set_available(
        self,
        version: str,
        release_sha: str,
        published_at: str,
        notes_ru: str | None,
    ) -> None:
        self._update(
            state=UpdateState.AVAILABLE,
            available_version=version,
            available_release_sha=release_sha,
            available_published_at=published_at,
            notes_ru=notes_ru,
            last_check_at=utc_now().isoformat(),
            last_check_ok=True,
            error_code=None,
        )

    def set_ready(self, package_path: str, manifest_path: str) -> None:
        with self._lock:
            self._status.state = UpdateState.READY
            self._status.download_progress = 100
            self._status.last_check_ok = True
            self._status.error_code = None
            self._downloaded_dir = package_path
            self._manifest_path = manifest_path

    def manifest_path(self) -> str | None:
        return self._manifest_path

    def set_failed(self, error_code: str, state: str | None = None) -> None:
        self._update(
            state=state or UpdateState.FAILED,
            error_code=error_code,
            last_check_ok=False,
        )

    def set_manual_required(self, version: str, release_sha: str, notes_ru: str | None) -> None:
        self._update(
            state=UpdateState.MANUAL_ACTION_REQUIRED,
            available_version=version,
            available_release_sha=release_sha,
            notes_ru=notes_ru,
            error_code=UpdateChannelError.BELOW_MINIMUM,
            last_check_ok=True,
        )

    # --- Установка ------------------------------------------------------------

    def engine_can_install(self) -> tuple[bool, str]:
        """Разрешение на установку: только после READY, без параллельных действий."""
        if self._status.state != UpdateState.READY:
            return False, "пакет не готов к установке"
        if not self._action_lock.acquire(blocking=False):
            return False, "действие уже выполняется"
        return True, ""

    def set_engine_installed(self, version: str, sha: str) -> None:
        with self._lock:
            if version:
                self._status.installed_version = version
            if sha:
                self._status.installed_release_sha = sha

    def mark_installing(self, job_id: str) -> None:
        self._engine_job_id = job_id
        self._update(
            state=UpdateState.INSTALLING,
            error_code=None,
            last_result=None,
        )

    def install_job_id(self) -> str | None:
        return self._engine_job_id

    def set_installed(self, version: str, release_sha: str) -> None:
        self._downloaded_dir = None
        self._manifest_path = None
        self._engine_job_id = None
        self._update(
            state=UpdateState.UP_TO_DATE,
            installed_version=version,
            installed_release_sha=release_sha,
            available_version=None,
            available_release_sha=None,
            available_published_at=None,
            notes_ru=None,
            last_check_at=utc_now().isoformat(),
            last_check_ok=True,
            last_result="updated",
            error_code=None,
        )

    def set_rolled_back(self, error_code: str) -> None:
        self._downloaded_dir = None
        self._manifest_path = None
        self._engine_job_id = None
        self._update(
            state=UpdateState.FAILED,
            error_code=error_code,
            last_result="rolled_back",
        )

    def set_engine_restart_required(self, version: str, release_sha: str) -> None:
        self._engine_job_id = None
        self._update(
            state=UpdateState.RESTART_REQUIRED,
            installed_version=version or self._status.installed_version,
            installed_release_sha=release_sha or self._status.installed_release_sha,
            last_result="restart_required",
            error_code=None,
        )

    def engine_failed(self, error_code: str) -> None:
        self._engine_job_id = None
        self._update(
            state=UpdateState.FAILED,
            error_code=error_code,
            last_result="failed",
        )

    def release_install_action(self) -> None:
        self._action_lock.release()

    # --- Движок ---------------------------------------------------------------

    def engine_pending_actions(self) -> list[str]:
        return list(self._pending_actions)

    def add_pending_action(self, action: str) -> bool:
        """Команда UI -> движку (install). Возвращает False при дубликате."""
        with self._lock:
            if action in self._pending_actions:
                return False
            self._pending_actions.append(action)
            return True

    def clear_pending_action(self, action: str) -> None:
        with self._lock:
            if action in self._pending_actions:
                self._pending_actions.remove(action)

    def clear_pending_actions(self) -> None:
        with self._lock:
            self._pending_actions = []

    def downloaded_dir(self) -> str | None:
        return self._downloaded_dir


def is_installing_state(state: str) -> bool:
    return state in _INSTALLING_STATES
