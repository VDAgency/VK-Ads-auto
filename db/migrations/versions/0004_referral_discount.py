"""referral and discount tables

Revision ID: 0004_referral_discount
Revises: 0003_stat
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_referral_discount"
down_revision: str | None = "0003_stat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referral",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("referrer_client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=False),
        sa.Column("referred_client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_referral_account_id", "referral", ["account_id"])
    op.create_index("ix_referral_referrer_client_id", "referral", ["referrer_client_id"])
    op.create_index("ix_referral_referred_client_id", "referral", ["referred_client_id"])

    op.create_table(
        "discount",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_discount_account_id", "discount", ["account_id"])
    op.create_index("ix_discount_client_id", "discount", ["client_id"])


def downgrade() -> None:
    op.drop_table("discount")
    op.drop_table("referral")
