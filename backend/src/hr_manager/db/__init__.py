"""Пакет БД приложения.

Импорт пакета регистрирует все ORM-модели в Base.metadata (цепочка
db/__init__ -> db/schema -> модели). На Этапе 1 моделей ещё нет,
поэтому метаданные содержат только базовый класс и naming convention.
"""

from hr_manager.db.schema import Base

__all__ = ["Base"]
