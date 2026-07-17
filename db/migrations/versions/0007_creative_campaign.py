"""creative + campaign tables

Revision ID: 0007_creative_campaign
Revises: 0006_brief_invite_contact_name
Create Date: 2026-07-17

Реализует spec docs/superpowers/specs/2026-07-17-brief-processing-launch-design.md §7.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_creative_campaign"
down_revision: str | None = "0006_brief_invite_contact_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "creative",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("brief_id", sa.Integer(), sa.ForeignKey("brief.id"), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("body", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_creative_account_id", "creative", ["account_id"])
    op.create_index("ix_creative_brief_id", "creative", ["brief_id"])

    op.create_table(
        "campaign",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("brief_id", sa.Integer(), sa.ForeignKey("brief.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="prepared"),
        sa.Column("objective", sa.String(length=32), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=True),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_campaign_account_id", "campaign", ["account_id"])
    op.create_index("ix_campaign_brief_id", "campaign", ["brief_id"])
    op.create_index("ix_campaign_client_id", "campaign", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_campaign_client_id", table_name="campaign")
    op.drop_index("ix_campaign_brief_id", table_name="campaign")
    op.drop_index("ix_campaign_account_id", table_name="campaign")
    op.drop_table("campaign")
    op.drop_index("ix_creative_brief_id", table_name="creative")
    op.drop_index("ix_creative_account_id", table_name="creative")
    op.drop_table("creative")
