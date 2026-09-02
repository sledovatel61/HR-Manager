"""Baseline: стартовая ревизия пустой базы.

Этап 1 (см. ROADMAP.md) поднимает технический каркас и сознательно
не создаёт бизнес-таблиц. Эта ревизия фиксирует начало истории
миграций: она делает ``alembic upgrade head`` / ``downgrade base``
безопасными на пустой базе и проверяет сам конвейер миграций
(cм. tests/backend/test_migrations.py).

Бизнес-схемы появятся отдельными миграциями: пользователи — следующий
этап (идентификация и безопасность), кандидаты — Этап 2.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-02
"""

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Пустая база: бизнес-таблиц на этом этапе нет, схему менять не требуется."""


def downgrade() -> None:
    """Откат baseline: схема не менялась, откатывать нечего."""
