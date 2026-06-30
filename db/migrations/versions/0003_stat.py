"""stat table (metric snapshots)

Revision ID: 0003_stat
Revises: 0002_client_brief
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_stat"
down_revision: str | None = "0002_client_brief"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("shows", sa.Float(), nullable=False),
        sa.Column("clicks", sa.Float(), nullable=False),
        sa.Column("spent", sa.Float(), nullable=False),
        sa.Column("results", sa.Float(), nullable=False),
    )
    op.create_index("ix_stat_account_id", "stat", ["account_id"])
    op.create_index("ix_stat_campaign_id", "stat", ["campaign_id"])


def downgrade() -> None:
    op.drop_table("stat")
