"""sessions_valid_from for operator and client

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-01

Граница отзыва доступа (аудит 2026-09-01, пункт 4). Токены сессий и magic-ссылок
несут отметку выпуска; всё выпущенное раньше этой границы перестаёт приниматься.

Nullable и без значения по умолчанию намеренно: пустое поле = проверка не
применяется, поэтому накатывание миграции никого не разлогинивает.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_sessions_valid_from"
down_revision: str | None = "0014_operator_password"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator", sa.Column("sessions_valid_from", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "client", sa.Column("sessions_valid_from", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("client", "sessions_valid_from")
    op.drop_column("operator", "sessions_valid_from")
