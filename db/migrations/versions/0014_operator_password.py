"""operator password columns

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-31

Пароль веб-админки оператора (spec 2026-08-31), по образцу 0008_client_password:
те же две колонки, но на таблице `operator`. Nullable — существующие операторы
(заведённые ботом лениво, `get_or_create_operator`) продолжают входить только по
одноразовой ссылке из Telegram, пока не поставят пароль явно.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_operator_password"
down_revision: str | None = "0013_ad_account_client"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operator", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "operator", sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("operator", "password_set_at")
    op.drop_column("operator", "password_hash")
