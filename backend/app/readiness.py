"""Предпусковая проверка готовности Windows-пилота (Phase 14).

Read-only агрегат поверх уже существующих сигналов диагностики:

* server-side: health/миграции/worker/бэкапы (как ``/ops/status``), версия и
  release SHA, доверенные ключи канала, доступность подписанного HTTPS-канала;
* host-side: факты от Windows-движка (Docker/Compose/loopback-порты/место/
  ACL каталога состояния) — только через закрытую схему ``engine-facts``,
  никакого выполнения команд с backend-контейнера.

Правила честности:

* проверка никогда ничего не «чинит» и не мутирует установку;
* неизвестное состояние — ``warning`` (не выдуманный pass), нарушенный
* fail-closed контракт — ``fail``;
* SMTP/Telegram — необязательные интеграции: их отсутствие — ``warning``,
  но никогда не блокирует работу приложения;
* вердикт: ``ready`` | ``ready_with_warnings`` | ``blocked``;
* в ответе нет URL, путей, секретов и PII — только коды, русские объяснения
  и безопасные значения (версии, счётчики, key_id доверенных ключей).
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import __version__
from app.backup import freshness_ok, load_state
from app.channel import (
    channel_enabled,
    fetch_manifest_text,
    parse_trusted_keys,
)
from app.config import Settings
from app.db import probe_database
from app.schemas import (
    ReadinessCheckOut,
    ReadinessReportOut,
    UpdateEngineFactsRequest,
)
from app.update_channel_contract import (
    ChannelError,
    parse_manifest_json,
    validate_manifest_fields,
    verify_signature,
)
from app.utils import utc_now

# Минимум свободного места для безопасного обновления/rollback пилота
# (движок требует 5 ГБ при установке — readiness использует ту же планку).
MIN_FREE_MB = 5120

# Таймаут проверки доступности канала (readiness не должен висеть минуту).
CHANNEL_PROBE_TIMEOUT_SECONDS = 6.0

VERDICT_LABELS = {
    "ready": "Готово к запуску пилота",
    "ready_with_warnings": "Готово с предупреждениями",
    "blocked": "Запуск запрещён: устраните блокирующие проблемы",
}


@dataclass
class _Facts:
    """Факты движка или честное «неизвестно»."""

    facts: UpdateEngineFactsRequest | None = None
    fresh: bool = False
    received_at: str | None = None


@dataclass
class _Builder:
    checks: list[ReadinessCheckOut] = field(default_factory=list)

    def add(
        self,
        code: str,
        title_ru: str,
        state: str,
        explanation_ru: str,
        action_ru: str,
        details: list[str] | None = None,
    ) -> None:
        self.checks.append(
            ReadinessCheckOut(
                code=code,
                title_ru=title_ru,
                state=state,  # type: ignore[arg-type]
                explanation_ru=explanation_ru,
                action_ru=action_ru,
                details=details or [],
            )
        )


def _trusted_key_summary(settings: Settings) -> tuple[list[dict], str | None]:
    """Сводка доверенных ключей: key_id, fingerprint, статус — и только это."""
    try:
        trusted = parse_trusted_keys(settings)
    except ChannelError as exc:
        return [], exc.code
    summary: list[dict] = []
    for key_id, entry in trusted.items():
        fingerprint = ""
        key_value = entry.get("key", "")
        try:
            raw = base64.b64decode(key_value, validate=True)
            if len(raw) == 32:
                fingerprint = hashlib.sha256(raw).hexdigest()
        except Exception:
            fingerprint = ""
        summary.append(
            {
                "key_id": key_id,
                "fingerprint": fingerprint[:16],
                "revoked": bool(entry.get("revoked")),
            }
        )
    return summary, None


def _probe_channel(settings: Settings) -> tuple[str, str]:
    """Проверка канала без изменения состояния установки.

    Возвращает (error_code|"", version|""). Сетевые ошибки — warning-класс
    (приложение продолжает работать), криптографические/контрактные — fail.
    """
    if not settings.update_channel_url:
        return "not_configured", ""
    try:
        text = fetch_manifest_text(settings, timeout=CHANNEL_PROBE_TIMEOUT_SECONDS)
        manifest = parse_manifest_json(text)
        validate_manifest_fields(manifest)
        trusted = parse_trusted_keys(settings)
        signature = manifest.get("signature") or {}
        key_id = signature.get("key_id") if isinstance(signature, dict) else None
        entry = trusted.get(key_id) if isinstance(key_id, str) else None
        if entry is None:
            return "unknown_key", str(manifest.get("version", ""))
        if entry.get("revoked"):
            return "revoked_key", str(manifest.get("version", ""))
        verify_signature(manifest, entry["key"])
        return "", str(manifest.get("version", ""))
    except ChannelError as exc:
        return exc.code, ""


def _migration_state(db_engine: object) -> tuple[str, str, str]:
    """Текущая/ожидаемая ревизии и состояние (ok|drift|unknown)."""
    current: str | None = None
    try:
        with db_engine.connect() as connection:  # type: ignore[attr-defined]
            row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
            current = str(row[0]) if row is not None else None
    except Exception:
        current = None
    expected: str | None = None
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        repo_root = Path(__file__).resolve().parent.parent
        alembic_cfg = Config(str(repo_root / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(repo_root / "alembic"))
        expected = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    except Exception:
        expected = None
    if current is not None and expected is not None:
        return ("ok" if current == expected else "drift"), current, expected or ""
    return "unknown", current or "", expected or ""


def _backup_state(
    settings: Settings,
) -> tuple[str, float | None, bool | None, bool | None]:
    """(missing|failed|stale|ok, возраст_сек, последний_deep_check, drill)."""
    try:
        state = load_state(__import__("pathlib").Path(settings.backup_state_file))
    except Exception:
        state = None
    if state is None or state.last_backup is None:
        return "missing", None, None, None
    record = state.last_backup
    if record.status != "ok":
        return "failed", None, None, None
    _, age = freshness_ok(state, now=utc_now(), max_age_hours=settings.backup_max_age_hours)
    check = state.last_check or {}
    drill = state.last_drill or {}
    if age is None or age > settings.backup_max_age_hours * 3600.0:
        return "stale", age, check.get("ok"), drill.get("ok")
    return "ok", age, check.get("ok"), drill.get("ok")


def _host_checks(builder: _Builder, facts: _Facts) -> None:
    report = facts.facts
    unknown = report is None or not facts.fresh
    hint = (
        "Данные Windows-движка недоступны или устарели (наблюдатель не работает?)."
        if unknown
        else ""
    )

    T = TypeVar("T")

    def fact_or_none(getter: Callable[[UpdateEngineFactsRequest], T]) -> T | None:
        if unknown or report is None:
            return None
        return getter(report)

    # 1. Поддерживаемая ОС.
    supported = fact_or_none(lambda r: r.windows_supported)
    if supported is None:
        builder.add(
            "os_supported",
            "Поддерживаемая операционная система",
            "warning",
            hint or "Состояние ОС неизвестно.",
            "Запустите приложение и убедитесь, что наблюдатель канала обновлений активен.",
        )
    elif supported:
        builder.add(
            "os_supported",
            "Поддерживаемая операционная система",
            "pass",
            "Windows 10/11 подтверждён движком.",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "os_supported",
            "Поддерживаемая операционная система",
            "fail",
            "Операционная система не поддерживается (требуется Windows 10/11 x64).",
            "Используйте поддерживаемую машину для пилота.",
        )

    # 2. Docker daemon.
    docker_state = fact_or_none(lambda r: r.docker_state)
    if docker_state is None:
        builder.add(
            "docker_daemon",
            "Docker и демон",
            "warning",
            hint or "Состояние Docker неизвестно.",
            "Запустите Docker Desktop и повторите проверку.",
        )
    elif docker_state == "ok":
        builder.add(
            "docker_daemon",
            "Docker и демон",
            "pass",
            "Docker CLI и демон доступны.",
            "Действий не требуется.",
        )
    else:
        label = "демон Docker не запущен" if docker_state == "daemon_down" else "Docker не найден"
        builder.add(
            "docker_daemon",
            "Docker и демон",
            "fail",
            f"Проблема с Docker: {label}.",
            "Запустите Docker Desktop (или установите его с официального сайта docker.com).",
        )

    # 3. Compose.
    compose_ok = fact_or_none(lambda r: r.compose_ok)
    if compose_ok is None:
        builder.add(
            "compose_version",
            "Docker Compose",
            "warning",
            hint or "Версия Compose неизвестна.",
            "Повторите проверку после запуска наблюдателя.",
        )
    elif compose_ok:
        builder.add(
            "compose_version",
            "Docker Compose",
            "pass",
            "Версия Docker Compose соответствует требованию (v2.24+).",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "compose_version",
            "Docker Compose",
            "fail",
            "Docker Compose ниже требуемой версии v2.24.",
            "Обновите Docker Desktop до актуальной версии.",
        )

    # 4. Loopback-публикация портов.
    ports = fact_or_none(lambda r: r.published_ports)
    if ports is None:
        builder.add(
            "loopback_binding",
            "Публикация только на 127.0.0.1",
            "warning",
            hint or "Список опубликованных портов неизвестен.",
            "Повторите проверку после запуска наблюдателя.",
        )
    else:
        exposed = [p for p in ports if p.host_ip not in ("127.0.0.1", "::1")]
        if not exposed:
            builder.add(
                "loopback_binding",
                "Публикация только на 127.0.0.1",
                "pass",
                f"Опубликован только пилотный порт на 127.0.0.1 ({len(ports)} порт(ов)).",
                "Действий не требуется.",
            )
        else:
            names = ", ".join(sorted({p.container for p in exposed}))[:120]
            builder.add(
                "loopback_binding",
                "Публикация только на 127.0.0.1",
                "fail",
                f"Обнаружены порты, опубликованные вне 127.0.0.1: {names}.",
                "Проверьте конфигурацию Compose: БД и backend не должны публиковаться наружу.",
            )

    # 5. Защищённость StateDir/staging.
    acl_ok = fact_or_none(lambda r: r.state_dir_acl_ok)
    staging_ok = fact_or_none(lambda r: r.staging_writable and r.staging_outside_state)
    if acl_ok is None or staging_ok is None:
        builder.add(
            "state_dir_secured",
            "Защищённость каталога состояния и staging",
            "warning",
            hint or "Не удалось проверить ACL каталога состояния и staging.",
            "Повторите проверку после запуска наблюдателя.",
        )
    elif acl_ok and staging_ok:
        builder.add(
            "state_dir_secured",
            "Защищённость каталога состояния и staging",
            "pass",
            "Каталог состояния защищён ACL, staging доступен и отделён от данных и бэкапов.",
            "Действий не требуется.",
        )
    else:
        problems = []
        if not acl_ok:
            problems.append("ACL каталога состояния не ограничен текущим пользователем")
        if not staging_ok:
            problems.append("staging недоступен для записи или находится внутри данных")
        builder.add(
            "state_dir_secured",
            "Защищённость каталога состояния и staging",
            "fail",
            "Проблемы: " + "; ".join(problems) + ".",
            "Переустановите приложение движком, чтобы восстановить защищённую конфигурацию.",
        )

    # 6. Свободное место.
    free_mb = fact_or_none(lambda r: r.disk_free_mb)
    if free_mb is None:
        builder.add(
            "disk_free",
            "Свободное место на диске",
            "warning",
            hint or "Свободное место неизвестно.",
            "Повторите проверку после запуска наблюдателя.",
        )
    elif free_mb >= MIN_FREE_MB:
        builder.add(
            "disk_free",
            "Свободное место на диске",
            "pass",
            f"Доступно {free_mb} МБ (минимум {MIN_FREE_MB} МБ).",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "disk_free",
            "Свободное место на диске",
            "fail",
            f"Недостаточно места: доступно {free_mb} МБ, требуется минимум {MIN_FREE_MB} МБ.",
            "Освободите место на диске перед запуском пилота.",
        )

    # 7. Возможность отката.
    previous = fact_or_none(lambda r: r.previous_images_present)
    if previous is None:
        builder.add(
            "rollback_ready",
            "Возможность безопасного отката",
            "warning",
            hint or "Наличие предыдущих образов неизвестно.",
            "Повторите проверку после запуска наблюдателя.",
        )
    elif previous:
        builder.add(
            "rollback_ready",
            "Возможность безопасного отката",
            "pass",
            "Предыдущие образы закреплены тегом :previous — автоматический откат доступен.",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "rollback_ready",
            "Возможность безопасного отката",
            "warning",
            "Это первая установка: предыдущих образов нет, откат заменяется "
            "восстановлением из бэкапа.",
            "После первого обновления движок закрепит предыдущие образы автоматически.",
        )


def run_readiness_checks(
    settings: Settings,
    db: Session,
    db_engine: Engine,
    host_facts_store: object,
) -> ReadinessReportOut:
    """Собрать полный отчёт готовности (read-only, без мутаций установки)."""
    builder = _Builder()

    # --- Факты движка -------------------------------------------------------
    facts_value, received_at = host_facts_store.snapshot()  # type: ignore[attr-defined]
    facts = _Facts(
        facts=facts_value,
        fresh=host_facts_store.fresh(),  # type: ignore[attr-defined]
        received_at=received_at.isoformat() if received_at else None,
    )
    if facts.fresh:
        builder.add(
            "engine_watcher",
            "Наблюдатель обновлений",
            "pass",
            "Windows-движок регулярно сообщает состояние машины.",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "engine_watcher",
            "Наблюдатель обновлений",
            "warning",
            "Свежие данные движка отсутствуют — часть проверок будет неполной.",
            "Запустите приложение: наблюдатель стартует автоматически вместе с ним.",
        )

    _host_checks(builder, facts)

    # --- Server-side: health, миграции, worker ------------------------------
    probe = probe_database(db_engine)
    if probe.ok:
        builder.add(
            "api_health",
            "Работоспособность API и базы",
            "pass",
            f"База данных отвечает (задержка {probe.latency_ms} мс).",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "api_health",
            "Работоспособность API и базы",
            "fail",
            "База данных недоступна или отвечает с ошибкой.",
            "Проверьте состояние контейнеров (диагностика приложения) и повторите проверку.",
        )

    migration, current_rev, expected_rev = _migration_state(db_engine)
    if migration == "ok":
        builder.add(
            "migrations_state",
            "Состояние миграций",
            "pass",
            f"Схема базы актуальна (ревизия {current_rev}).",
            "Действий не требуется.",
        )
    elif migration == "drift":
        builder.add(
            "migrations_state",
            "Состояние миграций",
            "fail",
            f"Дрейф миграций: в базе {current_rev}, ожидается {expected_rev}.",
            "Не запускайте пилот: выполните обновление приложения штатным путём.",
        )
    else:
        builder.add(
            "migrations_state",
            "Состояние миграций",
            "warning",
            "Состояние миграций не удалось определить.",
            "Повторите проверку; при повторении ошибки обратитесь к диагностике.",
        )

    try:
        from app.worker import queue_counts

        worker_signal = queue_counts(db, now=utc_now())
    except Exception:
        worker_signal = None
    worker = (worker_signal or {}).get("worker") or {}
    if worker.get("alive") is True:
        builder.add(
            "worker_health",
            "Фоновый обработчик уведомлений",
            "pass",
            "Worker отвечает и обрабатывает очередь.",
            "Действий не требуется.",
        )
    elif worker.get("alive") is False:
        builder.add(
            "worker_health",
            "Фоновый обработчик уведомлений",
            "fail",
            "Worker не отвечает (heartbeat устарел).",
            "Перезапустите приложение; при повторении — соберите диагностику.",
        )
    else:
        builder.add(
            "worker_health",
            "Фоновый обработчик уведомлений",
            "warning",
            "Состояние worker неизвестно.",
            "Повторите проверку после запуска приложения.",
        )

    # --- Бэкапы и restore drill ----------------------------------------------
    backup, age, _check_ok, drill_ok = _backup_state(settings)
    if backup == "ok":
        age_hours = round((age or 0) / 3600.0, 1)
        builder.add(
            "backup_fresh",
            "Свежий зашифрованный бэкап",
            "pass",
            f"Последний бэкап создан {age_hours} ч назад и прошёл проверку целостности.",
            "Действий не требуется.",
        )
    elif backup == "stale":
        age_hours = round((age or 0) / 3600.0, 1)
        builder.add(
            "backup_fresh",
            "Свежий зашифрованный бэкап",
            "warning",
            f"Бэкап старше допустимого порога ({age_hours} ч назад).",
            "Дождитесь планового бэкапа или запустите его вручную в разделе администрирования.",
        )
    elif backup == "failed":
        builder.add(
            "backup_fresh",
            "Свежий зашифрованный бэкап",
            "fail",
            "Последний бэкап завершился ошибкой или не прошёл проверку целостности.",
            "Проверьте сервис бэкапов и свободное место; создайте бэкап вручную.",
        )
    else:
        builder.add(
            "backup_fresh",
            "Свежий зашифрованный бэкап",
            "fail",
            "Зашифрованный бэкап отсутствует.",
            "Перед запуском пилота обязателен хотя бы один проверенный бэкап.",
        )

    if drill_ok is True:
        builder.add(
            "restore_drill",
            "Проверка восстановления (restore drill)",
            "pass",
            "Последний restore drill успешно восстановил бэкап в изолированную БД.",
            "Действий не требуется.",
        )
    elif drill_ok is False:
        builder.add(
            "restore_drill",
            "Проверка восстановления (restore drill)",
            "fail",
            "Последняя проверка восстановления завершилась неудачно.",
            "Не запускайте пилот: бэкапы должны быть проверяемыми. Повторите drill.",
        )
    else:
        builder.add(
            "restore_drill",
            "Проверка восстановления (restore drill)",
            "warning",
            "Restore drill ещё не выполнялся.",
            "Запустите проверку восстановления перед первым рабочим днём пилота.",
        )

    # --- Версия и канал -------------------------------------------------------
    installed_sha = settings.update_installed_sha or settings.release_sha
    installed_version = settings.update_installed_version or __version__
    if installed_sha and settings.release_sha and installed_sha != settings.release_sha:
        builder.add(
            "release_identity",
            "Версия и release SHA",
            "warning",
            "Версия установленного снимка и работающего релиза расходятся.",
            "Завершите обновление или выполните откат, затем повторите проверку.",
        )
    elif installed_sha:
        builder.add(
            "release_identity",
            "Версия и release SHA",
            "pass",
            f"Работает версия {installed_version} (commit {installed_sha[:12]}).",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "release_identity",
            "Версия и release SHA",
            "warning",
            "Release SHA работающего релиза неизвестен.",
            "Проверьте конфигурацию окружения (RELEASE_SHA).",
        )

    summary, key_error = _trusted_key_summary(settings)
    active = [item for item in summary if not item["revoked"]]
    if key_error:
        builder.add(
            "trust_store",
            "Доверенные ключи канала обновлений",
            "fail",
            "Набор доверенных ключей задан некорректно.",
            "Исправьте конфигурацию канала (UPDATE_CHANNEL_PUBLIC_KEYS).",
        )
    elif not channel_enabled(settings):
        builder.add(
            "trust_store",
            "Доверенные ключи канала обновлений",
            "warning",
            "Канал обновлений не настроен — обновления будут недоступны.",
            "Настройте канал до первого обновления (см. runbook).",
        )
    elif not active:
        builder.add(
            "trust_store",
            "Доверенные ключи канала обновлений",
            "fail",
            "Все доверенные ключи отозваны — проверенные обновления невозможны.",
            "Выполните ротацию ключей по процедуре из runbook.",
        )
    else:
        details = [
            f"key_id={item['key_id']} fingerprint={item['fingerprint']} "
            f"{'отозван' if item['revoked'] else 'активен'}"
            for item in summary
        ]
        builder.add(
            "trust_store",
            "Доверенные ключи канала обновлений",
            "pass",
            f"Активных ключей: {len(active)} (из {len(summary)}).",
            "Действий не требуется.",
            details=details,
        )

    error_code, available_version = _probe_channel(settings)
    if not error_code:
        builder.add(
            "channel_https",
            "Доступность канала обновлений",
            "pass",
            f"Канал доступен по HTTPS, manifest подписан доверенным ключом "
            f"(версия {available_version}).",
            "Действий не требуется.",
        )
    elif error_code in (
        "not_configured",
        "channel_offline",
        "manifest_offline",
    ):
        builder.add(
            "channel_https",
            "Доступность канала обновлений",
            "warning",
            "Канал не настроен или недоступен. Приложение продолжает работать; "
            "обновления недоступны.",
            "Проверьте сеть и конфигурацию канала, если планируете обновления.",
        )
    else:
        builder.add(
            "channel_https",
            "Доступность канала обновлений",
            "fail",
            f"Канал отвечает, но manifest не прошёл проверку (код {error_code}).",
            "Не обновляйтесь до выяснения причины; сравните доверенные ключи с релизом.",
        )

    # --- Необязательные интеграции --------------------------------------------
    if settings.smtp_enabled:
        builder.add(
            "smtp_optional",
            "Email (SMTP) — необязательная интеграция",
            "pass",
            "SMTP настроен и включён.",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "smtp_optional",
            "Email (SMTP) — необязательная интеграция",
            "warning",
            "SMTP не настроен: email-уведомления отключены, работа приложения не блокируется.",
            "Настройте интеграцию позже, если она понадобится пилоту.",
        )
    if settings.telegram_enabled:
        builder.add(
            "telegram_optional",
            "Telegram — необязательная интеграция",
            "pass",
            "Telegram настроен и включён.",
            "Действий не требуется.",
        )
    else:
        builder.add(
            "telegram_optional",
            "Telegram — необязательная интеграция",
            "warning",
            "Telegram не настроен: уведомления в Telegram отключены, работа "
            "приложения не блокируется.",
            "Настройте интеграцию позже, если она понадобится пилоту.",
        )

    # --- Вердикт ---------------------------------------------------------------
    states = {check.state for check in builder.checks}
    if "fail" in states:
        verdict = "blocked"
    elif "warning" in states:
        verdict = "ready_with_warnings"
    else:
        verdict = "ready"
    return ReadinessReportOut(
        verdict=verdict,  # type: ignore[arg-type]
        verdict_ru=VERDICT_LABELS[verdict],
        generated_at=utc_now().isoformat(),
        release_version=installed_version,
        release_sha=installed_sha,
        checks=builder.checks,
    )
