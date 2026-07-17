"""client password columns + unique (account_id, email)

Revision ID: 0008_client_password
Revises: 0007_creative_campaign
Create Date: 2026-07-17

Реализует C2 из docs/superpowers/specs/2026-07-17-client-cabinet-design.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_client_password"
down_revision: str | None = "0007_creative_campaign"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("client", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("client", sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=True))
    # Уникальность email в рамках тенанта. NULL-email не конфликтуют (стандартная
    # SQL-семантика NULL ≠ NULL), поэтому клиенты без email допускаются.
    op.create_index("uq_client_account_email", "client", ["account_id", "email"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_client_account_email", table_name="client")
    op.drop_column("client", "password_set_at")
    op.drop_column("client", "password_hash")
