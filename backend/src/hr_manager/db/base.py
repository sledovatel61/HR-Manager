from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Единая naming convention для всех миграций и отражений схемы.
# Позволяет Alembic генерировать стабильные имена ограничений и
# пересоздавать сущности в тестах без ручного дропа таблиц.
NAMING_CONVENTION: dict[str, Any] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей приложения."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Папка с моделями. На Этапе 1 моделей ещё нет — при появлении
# импортируйте их здесь, чтобы Alembic autogenerate их видел:
# from hr_manager.db import models  # noqa: F401
