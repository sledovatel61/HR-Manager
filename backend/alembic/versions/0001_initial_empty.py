"""initial schema placeholder

Этап 1 не вводит бизнес-сущностей, но требует рабочей Alembic-миграции,
на которую смогут опираться все последующие. Миграция безопасна:
она ничего не меняет в схеме, только фиксирует точку начала цепочки
ревизий (down_revision=None).

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Заглушка: схема появится на следующих этапах."""
    # Намеренно пусто: безопасная стартовая ревизия без DDL.
    pass


def downgrade() -> None:
    """Откат заглушки также ничего не делает."""
    pass
