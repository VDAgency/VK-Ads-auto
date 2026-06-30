"""client and brief tables

Revision ID: 0002_client_brief
Revises: 0001_initial
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_client_brief"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("telegram", sa.String(length=64), nullable=True),
        sa.Column("is_self", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_client_account_id", "client", ["account_id"])
    op.create_index("ix_client_email", "client", ["email"])
    op.create_index("ix_client_phone", "client", ["phone"])
    op.create_index("ix_client_telegram", "client", ["telegram"])

    op.create_table(
        "brief",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=True),
        sa.Column("variant", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_brief_account_id", "brief", ["account_id"])
    op.create_index("ix_brief_client_id", "brief", ["client_id"])

    # Сид дефолтного тенанта (единственный аккаунт на текущем этапе).
    op.execute("INSERT INTO account (id, name) VALUES (1, 'default')")


def downgrade() -> None:
    op.drop_table("brief")
    op.drop_table("client")
