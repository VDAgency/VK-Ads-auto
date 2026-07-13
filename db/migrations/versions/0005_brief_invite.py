"""brief_invite table + Brief.invite_id column

Revision ID: 0005_brief_invite
Revises: 0004_referral_discount
Create Date: 2026-07-13

Реализует spec docs/superpowers/specs/2026-07-13-brief-userbot-delivery-design.md §4.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_brief_invite"
down_revision: str | None = "0004_referral_discount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brief_invite",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=False),
        sa.Column("contact_type", sa.String(length=16), nullable=False),
        sa.Column("contact_value", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operator.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_brief_invite_token", "brief_invite", ["token"], unique=True)
    op.create_index("ix_brief_invite_account_id", "brief_invite", ["account_id"])
    op.create_index("ix_brief_invite_operator_id", "brief_invite", ["operator_id"])
    op.create_index("ix_brief_invite_client_id", "brief_invite", ["client_id"])
    op.create_index("ix_brief_invite_contact_value", "brief_invite", ["contact_value"])

    op.add_column(
        "brief",
        sa.Column(
            "invite_id",
            sa.Integer(),
            sa.ForeignKey("brief_invite.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_brief_invite_id", "brief", ["invite_id"])


def downgrade() -> None:
    op.drop_index("ix_brief_invite_id", table_name="brief")
    op.drop_column("brief", "invite_id")
    op.drop_index("ix_brief_invite_contact_value", table_name="brief_invite")
    op.drop_index("ix_brief_invite_client_id", table_name="brief_invite")
    op.drop_index("ix_brief_invite_operator_id", table_name="brief_invite")
    op.drop_index("ix_brief_invite_account_id", table_name="brief_invite")
    op.drop_index("ix_brief_invite_token", table_name="brief_invite")
    op.drop_table("brief_invite")
