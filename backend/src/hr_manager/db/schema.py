"""Схема БД и её версия (Alembic).

Заглушка пустой схемы для Этапа 1. С появлением ORM-моделей они
импортируются здесь, чтобы:
  * Alembic autogenerate корректно сравнивал состояние метаданных со схемой;
  * pytest-fixture create/drop мог создавать схему из моделей.
"""

from hr_manager.db.base import Base

__all__ = ["Base"]
