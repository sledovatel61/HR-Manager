"""Phase 14: server-owned отчёт «Проверить готовность пилота».

Backend — граница безопасности: список проверок, их коды, состояния и
формулировки принадлежат СЕРВЕРУ. Клиент не может прислать свой список,
изменить статус или подсунуть URL/путь/секрет: он только читает результат
(admin + подтверждённый scope ``update_channel_manage``).

Модель состояния одной проверки:

* ``pass``    — подтверждено серверными данными/свежим отчётом движка;
* ``warning`` — не блокирует запуск, но требует внимания владельца;
* ``fail``    — запуск пилота запрещён (вердикт «запуск запрещён»).

Данные для host-проверок (Windows/Docker/Compose/порты/диск) приходят
ТОЛЬКО от движка отдельным отчётом (``PilotHostEvidenceStore``) с ограниченным
временем жизни: устаревший отчёт честно деградирует до ``warning``, а не
выдаётся за свежий. В отчёт не попадают: URL канала с credentials, пути
StateDir/staging, токены, приватные ключи, содержимое бэкапов и PII —
только коды, статусы и короткие пояснения.
"""

from __future__ import annotations

import socket
import ssl
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import __version__
from app.backup import freshness_ok, load_state
from app.channel import describe_trusted_keys, parse_trusted_keys
from app.config import Settings
from app.db import probe_database
from app.utils import ensure_aware, utc_now

PASS = "pass"
WARNING = "warning"
FAIL = "fail"

VERDICT_READY = "готово"
VERDICT_WITH_WARNINGS = "готово с предупреждениями"
VERDICT_BLOCKED = "запуск запрещён"

# Служебные константы порогов (не секреты).
MIN_FREE_DISK_MB = 2048
HOST_EVIDENCE_MAX_AGE_SECONDS = 24 * 3600
CHANNEL_PROBE_TIMEOUT_SECONDS = 3.0
REQUIRED_WINDOWS_BUILD = 19041  # Windows 10 2004+; Windows 11 — выше.
SUPPORTED_COMPOSE_MAJOR = 2

CHECK_TITLES = {
    "platform": "Поддерживаемая версия Windows",
    "docker_daemon": "Docker Desktop и daemon",
    "compose_version": "Docker Compose v2",
    "published_ports": "Порты опубликованы только на loopback",
    "database": "PostgreSQL доступен",
    "migrations": "Миграции применены до head",
    "worker": "Worker уведомлений жив",
    "host_evidence": "Свежий отчёт движка о хосте",
    "staging": "Каталог staging обновлений",
    "state_dir": "Каталог состояния защищён",
    "secrets": "Секреты не утекают в диагностику",
    "backup_freshness": "Свежий зашифрованный бэкап",
    "backup_drill": "Восстановление из бэкапа проверено",
    "release_version": "Версия и SHA релиза совпадают",
    "trust_store": "Trust store канала доверия",
    "channel_availability": "Канал обновлений доступен по HTTPS",
    "disk_space": "Свободное место на диске",
    "rollback": "Возможность безопасного отката",
    "smtp": "SMTP (необязательно)",
    "telegram": "Telegram (необязательно)",
}


def _check(
    code: str,
    state: str,
    detail_ru: str,
    action_ru: str,
    evidence: dict | None = None,
) -> dict:
    payload: dict = {
        "code": code,
        "title": CHECK_TITLES.get(code, code),
        "state": state,
        "detail": detail_ru,
        "action": action_ru,
    }
    if evidence:
        payload["evidence"] = evidence
    return payload


def _migration_state(engine: Engine) -> tuple[str, str | None, str | None]:
    """``(state, current, expected)`` — состояние миграций без PII."""
    current: str | None = None
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
            current = str(row[0]) if row is not None else None
    except Exception:
        return "unknown", None, None
    expected: str | None = None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        expected = script.get_current_head()
    except Exception:
        expected = None
    if current is None or expected is None:
        return "unknown", current, expected
    return ("ok" if current == expected else "drift"), current, expected


def _probe_channel_https(settings: Settings) -> dict:
    """Короткая проверка доступности HTTPS-хоста канала (без скачивания).

    Офлайн — это НЕ блокер: канал не мешает основной работе приложения.
    Возвращаются только безопасные факты (никаких URL с параметрами).
    """
    url = (settings.update_channel_url or "").strip()
    if not url:
        return {"configured": False, "reachable": False, "error": "not_configured"}
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or 443
        if not host:
            return {"configured": True, "reachable": False, "error": "bad_url"}
        context = ssl.create_default_context()
        with (
            socket.create_connection((host, port), timeout=CHANNEL_PROBE_TIMEOUT_SECONDS) as raw,
            context.wrap_socket(raw, server_hostname=host),
        ):
            pass
        return {"configured": True, "reachable": True, "error": None}
    except TimeoutError:
        return {"configured": True, "reachable": False, "error": "timeout"}
    except ssl.SSLError:
        return {"configured": True, "reachable": False, "error": "tls_error"}
    except OSError:
        return {"configured": True, "reachable": False, "error": "network_error"}


def _disk_evidence(settings: Settings, host: dict | None) -> tuple[str, str, str, dict]:
    """Свободное место: сначала факт от движка, иначе — staging-каталог."""
    if host and isinstance(host.get("free_space_mb"), int):
        free_mb = int(host["free_space_mb"])
        if free_mb < MIN_FREE_DISK_MB:
            return (
                FAIL,
                f"На диске хоста меньше {MIN_FREE_DISK_MB} МБ свободно.",
                "Освободите место на системном диске перед запуском пилота.",
                {"free_space_mb": free_mb},
            )
        if free_mb < MIN_FREE_DISK_MB * 2:
            return (
                WARNING,
                "Свободного места меньше удвоенного минимума.",
                "Освободите место: обновления и бэкапы требуют запаса.",
                {"free_space_mb": free_mb},
            )
        return (
            PASS,
            "Свободного места достаточно.",
            "Действий не требуется.",
            {"free_space_mb": free_mb},
        )
    try:
        usage = __import__("shutil").disk_usage(settings.update_staging_dir or "/")
        free_mb = int(usage.free / (1024 * 1024))
    except Exception:
        return (
            WARNING,
            "Нет данных о свободном месте (движок ещё не присылал отчёт).",
            "Запустите диагностику движка: hr-manager.ps1 -Action diagnostics.",
            {},
        )
    if free_mb < MIN_FREE_DISK_MB:
        return (
            FAIL,
            f"Свободно {free_mb} МБ — меньше минимума.",
            "Освободите место перед запуском пилота.",
            {"free_space_mb": free_mb},
        )
    return (
        WARNING,
        f"Свободно {free_mb} МБ (данные серверного контура; хост не подтверждён).",
        "Запустите диагностику движка на Windows-хосте.",
        {"free_space_mb": free_mb},
    )


def build_readiness_report(
    *,
    settings: Settings,
    engine: Engine,
    db: Session,
    host: dict | None,
    host_age_seconds: float | None,
    now: datetime | None = None,
) -> dict:
    """Собрать отчёт о готовности пилота (серверный, без PII и секретов)."""
    now = ensure_aware(now or utc_now())
    checks: list[dict] = []

    # --- Host-факты от движка ------------------------------------------------
    host_fresh = host is not None and (
        host_age_seconds is not None and host_age_seconds <= HOST_EVIDENCE_MAX_AGE_SECONDS
    )
    if host and not host_fresh:
        checks.append(
            _check(
                "host_evidence",
                WARNING,
                "Последний отчёт движка о хосте устарел.",
                "Запустите диагностику движка: hr-manager.ps1 -Action diagnostics.",
                {"age_seconds": int(host_age_seconds or 0)},
            )
        )
    elif host:
        checks.append(
            _check(
                "host_evidence",
                PASS,
                "Свежий отчёт движка о хосте получен.",
                "Действий не требуется.",
                {"age_seconds": int(host_age_seconds or 0)},
            )
        )
    else:
        checks.append(
            _check(
                "host_evidence",
                WARNING,
                "Отчёт движка о Windows-хосте ещё не получен.",
                "Установите/запустите пилот и выполните hr-manager.ps1 -Action diagnostics.",
                {},
            )
        )

    windows = (host or {}).get("windows") or {}
    host_ready = host_fresh and bool(windows)
    build = windows.get("build")
    if not host_ready:
        checks.append(
            _check(
                "platform",
                WARNING,
                "Версия Windows не подтверждена (нет свежего отчёта движка).",
                "Выполните диагностику на пилотной машине.",
                {},
            )
        )
    elif isinstance(build, int) and build >= REQUIRED_WINDOWS_BUILD:
        checks.append(
            _check(
                "platform",
                PASS,
                f"Windows сборка {build} поддерживается.",
                "Действий не требуется.",
                {"windows_build": build},
            )
        )
    else:
        checks.append(
            _check(
                "platform",
                FAIL,
                "Сборка Windows старше поддерживаемой (нужна 10 2004+/Windows 11).",
                "Обновите Windows 10/11 до поддерживаемой сборки.",
                {"windows_build": build},
            )
        )

    docker = (host or {}).get("docker") or {}
    if not host_ready:
        checks.append(
            _check(
                "docker_daemon",
                WARNING,
                "Docker daemon не подтверждён (нет свежего отчёта движка).",
                "Запустите Docker Desktop и выполните диагностику движка.",
                {},
            )
        )
    elif docker.get("daemon_ok"):
        checks.append(
            _check(
                "docker_daemon",
                PASS,
                "Docker daemon отвечает.",
                "Действий не требуется.",
                {"server_version": docker.get("server_version") or ""},
            )
        )
    else:
        checks.append(
            _check(
                "docker_daemon",
                FAIL,
                "Docker daemon недоступен: контейнеры не запустятся.",
                "Запустите Docker Desktop и повторите проверку.",
                {},
            )
        )

    compose = (host or {}).get("compose") or {}
    if not host_ready:
        checks.append(
            _check(
                "compose_version",
                WARNING,
                "Docker Compose v2 не подтверждён.",
                "Проверьте установку Docker Desktop.",
                {},
            )
        )
    elif compose.get("ok"):
        checks.append(
            _check(
                "compose_version",
                PASS,
                "Docker Compose v2 доступен.",
                "Действий не требуется.",
                {"version": compose.get("version") or ""},
            )
        )
    else:
        checks.append(
            _check(
                "compose_version",
                FAIL,
                "Docker Compose v2 недоступен.",
                "Установите Docker Desktop с официального сайта docker.com.",
                {},
            )
        )

    ports = (host or {}).get("published_ports")
    ports_observed = bool((host or {}).get("ports_observed"))
    if not host_ready or not ports_observed or not isinstance(ports, list):
        checks.append(
            _check(
                "published_ports",
                WARNING,
                "Публикуемые порты не подтверждены: движок не передал список публикаций.",
                "Выполните диагностику: hr-manager.ps1 -Action diagnostics.",
                {},
            )
        )
    else:
        exposed = [
            item
            for item in ports
            if isinstance(item, dict) and str(item.get("host_ip") or "") not in {"127.0.0.1"}
        ]
        services = sorted({str(item.get("service")) for item in ports if isinstance(item, dict)})
        if exposed:
            checks.append(
                _check(
                    "published_ports",
                    FAIL,
                    "Есть порты, опубликованные не на loopback.",
                    "Остановите пилот и приведите compose к loopback-публикации.",
                    {"services": sorted({str(item.get("service")) for item in exposed})},
                )
            )
        else:
            checks.append(
                _check(
                    "published_ports",
                    PASS,
                    "Все публикуемые порты привязаны к 127.0.0.1.",
                    "Действий не требуется.",
                    {"services": services},
                )
            )

    free_state, free_detail, free_action, free_evidence = _disk_evidence(
        settings, host if host_fresh else None
    )
    checks.append(
        _check("disk_space", free_state, free_detail, free_action, evidence=free_evidence)
    )

    rollback = (host or {}).get("previous_images_present")
    if not host_ready:
        checks.append(
            _check(
                "rollback",
                WARNING,
                "Наличие образа предыдущей версии не подтверждено.",
                "Выполните диагностику движка перед обновлением.",
                {},
            )
        )
    elif rollback:
        checks.append(
            _check(
                "rollback",
                PASS,
                "Образ предыдущей версии сохранён: откат возможен.",
                "Действий не требуется.",
                {},
            )
        )
    else:
        checks.append(
            _check(
                "rollback",
                WARNING,
                "Образа предыдущей версии нет — откат потребует переустановки.",
                "Выполните штатное обновление: движок сохранит образ previous.",
                {},
            )
        )

    staging = (host or {}).get("staging") or {}
    staging_dir = Path(settings.update_staging_dir or ".")
    if not staging_dir.exists() or not staging_dir.is_dir():
        checks.append(
            _check(
                "staging",
                FAIL,
                "Каталог staging обновлений недоступен контейнеру.",
                "Проверьте проброс HRM_STAGING_DIR и права каталога.",
                {},
            )
        )
    elif staging.get("inside_state_dir"):
        checks.append(
            _check(
                "staging",
                WARNING,
                "Staging находится внутри каталога состояния.",
                "Разнесите staging и StateDir (рекомендация Phase 13/14).",
                {},
            )
        )
    else:
        checks.append(
            _check(
                "staging",
                PASS,
                "Каталог staging доступен и отделён от StateDir.",
                "Действий не требуется.",
                {},
            )
        )

    state_dir = (host or {}).get("state_dir") or {}
    if not host_ready:
        checks.append(
            _check(
                "state_dir",
                WARNING,
                "Права каталога состояния не подтверждены.",
                "Выполните диагностику движка.",
                {},
            )
        )
    elif state_dir.get("acl_restricted") is True:
        checks.append(
            _check(
                "state_dir",
                PASS,
                "Доступ к каталогу состояния ограничен владельцем.",
                "Действий не требуется.",
                {},
            )
        )
    elif state_dir.get("acl_restricted") is False:
        checks.append(
            _check(
                "state_dir",
                WARNING,
                "Каталог состояния доступен шире, чем требуется.",
                "Ограничьте права каталога состояния текущим пользователем.",
                {},
            )
        )
    else:
        checks.append(
            _check(
                "state_dir",
                WARNING,
                "Ограничение прав каталога состояния не подтверждено движком.",
                "Проверьте ACL каталога состояния (icacls) и повторите диагностику.",
                {},
            )
        )

    checks.append(
        _check(
            "secrets",
            PASS if settings.secret_key and settings.update_engine_token else WARNING,
            "Секреты заданы конфигурацией; в диагностику они не попадают.",
            "Проверьте, что .env и StateDir не публикуются.",
            {"engine_token_configured": bool(settings.update_engine_token)},
        )
    )

    # --- Серверные проверки ---------------------------------------------------
    probe = probe_database(engine)
    checks.append(
        _check(
            "database",
            PASS if probe.ok else FAIL,
            "PostgreSQL отвечает." if probe.ok else "PostgreSQL недоступен.",
            "Действий не требуется." if probe.ok else "Проверьте контейнер db и том pgdata.",
            {"latency_ms": probe.latency_ms} if probe.ok else {},
        )
    )

    migration_state, current, expected = _migration_state(engine)
    if migration_state == "ok":
        checks.append(
            _check(
                "migrations",
                PASS,
                "Схема БД соответствует head миграций.",
                "Действий не требуется.",
                {"current": current},
            )
        )
    elif migration_state == "drift":
        checks.append(
            _check(
                "migrations",
                FAIL,
                "Схема БД не соответствует head миграций.",
                "Примените миграции: make prod-migrate.",
                {"current": current, "expected": expected},
            )
        )
    else:
        checks.append(
            _check(
                "migrations",
                WARNING,
                "Состояние миграций определить не удалось.",
                "Проверьте доступ к alembic_version и контейнер backend.",
                {},
            )
        )

    try:
        from app.worker import queue_counts

        queue = queue_counts(db, now=now)
        worker = queue.get("worker") or {}
        if worker.get("alive"):
            checks.append(
                _check(
                    "worker",
                    PASS,
                    "Worker уведомлений отвечает heartbeat.",
                    "Действий не требуется.",
                    {"processed_total": int(worker.get("processed_total") or 0)},
                )
            )
        else:
            checks.append(
                _check(
                    "worker",
                    FAIL,
                    "Worker уведомлений не отвечает.",
                    "Перезапустите worker: make worker-restart.",
                    {},
                )
            )
    except Exception:
        checks.append(
            _check(
                "worker",
                WARNING,
                "Состояние worker определить не удалось.",
                "Проверьте контейнер worker в docker compose ps.",
                {},
            )
        )

    # --- Бэкапы и проверенное восстановление --------------------------------
    try:
        backup_state = load_state(Path(settings.backup_state_file))
    except Exception:
        backup_state = None
    if backup_state is None or backup_state.last_backup is None:
        checks.append(
            _check(
                "backup_freshness",
                FAIL,
                "Успешный зашифрованный бэкап ещё не создавался.",
                "Создайте бэкап: make backup-now (или дождитесь планировщика).",
                {},
            )
        )
    else:
        record = backup_state.last_backup
        fresh, age_seconds = freshness_ok(
            backup_state, now=now, max_age_hours=settings.backup_max_age_hours
        )
        if record.status != "ok":
            checks.append(
                _check(
                    "backup_freshness",
                    FAIL,
                    "Последний бэкап завершился ошибкой.",
                    "Проверьте журнал backup: make logs.",
                    {"age_seconds": int(age_seconds) if age_seconds is not None else None},
                )
            )
        elif fresh:
            checks.append(
                _check(
                    "backup_freshness",
                    PASS,
                    "Свежий зашифрованный бэкап есть.",
                    "Действий не требуется.",
                    {"age_seconds": int(age_seconds or 0), "size_bytes": int(record.size)},
                )
            )
        else:
            checks.append(
                _check(
                    "backup_freshness",
                    WARNING,
                    "Последний бэкап старше допустимого возраста (RPO).",
                    "Запустите make backup-now и проверьте планировщик backup.",
                    {"age_seconds": int(age_seconds) if age_seconds is not None else None},
                )
            )
    drill = (backup_state.last_drill if backup_state else None) or {}
    if not drill:
        checks.append(
            _check(
                "backup_drill",
                WARNING,
                "Восстановление из бэкапа ещё не проверялось.",
                "Выполните make backup-drill перед запуском пилота.",
                {},
            )
        )
    elif drill.get("ok") is True:
        checks.append(
            _check(
                "backup_drill",
                PASS,
                "Restore drill проходил успешно.",
                "Повторяйте drill по регламенту.",
                {"tables": drill.get("tables"), "migration_ok": drill.get("migration_ok")},
            )
        )
    else:
        checks.append(
            _check(
                "backup_drill",
                FAIL,
                "Последний restore drill провален: восстановление не подтверждено.",
                "Разберите ошибку и повторите make backup-drill.",
                {"error": str(drill.get("error") or "unknown")},
            )
        )

    # Release/версия: backend знает установленную версию из конфигурации.
    installed_version = settings.update_installed_version or ""
    installed_sha = settings.update_installed_sha or ""
    if not installed_version:
        checks.append(
            _check(
                "release_version",
                WARNING,
                "Установленная версия не задана конфигурацией (UPDATE_INSTALLED_VERSION).",
                "Пропишите версию и SHA релиза в pilot.env.",
                {},
            )
        )
    elif settings.release_sha and installed_sha and settings.release_sha != installed_sha:
        checks.append(
            _check(
                "release_version",
                WARNING,
                "SHA контейнера не совпадает с заявленным SHA релиза.",
                "Пересоберите/переустановите контейнеры из тега релиза.",
                {"installed_version": installed_version},
            )
        )
    else:
        checks.append(
            _check(
                "release_version",
                PASS,
                f"Версия {installed_version} заявлена корректно.",
                "Действий не требуется.",
                {"installed_version": installed_version, "installed_sha12": installed_sha[:12]},
            )
        )

    try:
        trusted = parse_trusted_keys(settings)
        described = describe_trusted_keys(settings)
    except Exception:
        checks.append(
            _check(
                "trust_store",
                FAIL,
                "Trust store канала некорректен: доверие к обновлениям не установлено.",
                "Примените публичный trust store релиза (channel-config).",
                {},
            )
        )
    else:
        active = [item for item in described if not item["revoked"]]
        if not trusted:
            checks.append(
                _check(
                    "trust_store",
                    WARNING,
                    "Trust store пуст: канал обновлений не настроен.",
                    "Примените trust-store.json из релиза: hr-manager.ps1 -Action channel-config.",
                    {"keys": []},
                )
            )
        elif not active:
            checks.append(
                _check(
                    "trust_store",
                    FAIL,
                    "Все ключи trust store отозваны: обновления невозможны.",
                    "Примените актуальный trust store с активным ключом.",
                    {"keys": described},
                )
            )
        else:
            checks.append(
                _check(
                    "trust_store",
                    PASS,
                    f"Активных ключей канала: {len(active)}.",
                    "Действий не требуется.",
                    {"keys": described},
                )
            )

    channel = _probe_channel_https(settings)
    if not channel["configured"]:
        checks.append(
            _check(
                "channel_availability",
                WARNING,
                "URL канала обновлений не настроен (офлайн-режим допустим).",
                "Настройте канал, когда пилот будет готов к обновлениям.",
                {},
            )
        )
    elif channel["reachable"]:
        checks.append(
            _check(
                "channel_availability",
                PASS,
                "HTTPS-хост канала отвечает.",
                "Действий не требуется.",
                {},
            )
        )
    else:
        checks.append(
            _check(
                "channel_availability",
                WARNING,
                "Канал недоступен (офлайн): это не мешает основной работе.",
                "Проверьте сеть/прокси; обновление можно поставить позже.",
                {"error": channel["error"]},
            )
        )

    # --- Необязательные интеграции (никогда не блокируют) ---------------------
    for code, label, flag in (
        ("smtp", "SMTP", bool(settings.smtp_host)),
        ("telegram", "Telegram", bool(settings.telegram_bot_token)),
    ):
        checks.append(
            _check(
                code,
                PASS if flag else WARNING,
                f"{label} настроен." if flag else f"{label} не настроен (необязательно).",
                "Действий не требуется."
                if flag
                else f"Настройте {label} только при необходимости.",
                {"configured": flag},
            )
        )

    failed = [item for item in checks if item["state"] == FAIL]
    warned = [item for item in checks if item["state"] == WARNING]
    if failed:
        verdict = VERDICT_BLOCKED
    elif warned:
        verdict = VERDICT_WITH_WARNINGS
    else:
        verdict = VERDICT_READY
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "counts": {
            "pass": len(checks) - len(failed) - len(warned),
            "warning": len(warned),
            "fail": len(failed),
        },
        "host_evidence_age_seconds": int(host_age_seconds)
        if host_age_seconds is not None
        else None,
        "host_evidence_fresh": host_fresh,
        "server_version": __version__,
        "checks": checks,
    }
