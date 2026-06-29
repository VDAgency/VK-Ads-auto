"""initial schema: account, operator, integration_config

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
    )
    op.create_table(
        "operator",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_operator_account_id", "operator", ["account_id"])
    op.create_index("ix_operator_telegram_id", "operator", ["telegram_id"], unique=True)
    op.create_table(
        "integration_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("default_channel", sa.String(length=32), nullable=False),
        sa.Column("channel_healthy", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_integration_config_account_id", "integration_config", ["account_id"])


def downgrade() -> None:
    op.drop_table("integration_config")
    op.drop_table("operator")
    op.drop_table("account")
