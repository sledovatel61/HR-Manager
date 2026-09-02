"""Настройка логирования приложения.

Важно: персональные данные (ФИО, телефоны, email кандидатов) никогда
не должны попадать в логи. На Этапе 1 логируем только события инфраструктуры.
"""

import logging

from hr_manager.core.config import Settings, get_settings

_CONFIGURED = False


def configure_logging(settings: Settings | None = None) -> None:
    """Настраивает корневой логгер приложения (идемпотентно)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = settings or get_settings()
    level = logging.DEBUG if settings.app_env == "development" else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        root.addHandler(handler)

    # Тихие сторонние логгеры.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _CONFIGURED = True
