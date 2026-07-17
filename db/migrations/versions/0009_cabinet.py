"""cabinet table + campaign.cabinet_id

Revision ID: 0009_cabinet
Revises: 0008_client_password
Create Date: 2026-07-17

Реализует K-PR2 из docs/superpowers/specs/2026-07-17-kotbot-channel-design.md §6:
иерархия «Клиент 1:N Кабинет 1:N Кампания», reuse-индекс кабинета, привязка
кампании к кабинету (старые строки остаются с NULL).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_cabinet"
down_revision: str | None = "0008_client_password"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cabinet",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column("ad_object_url", sa.String(length=500), nullable=False),
        sa.Column("ad_object_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="created"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cabinet_account_id", "cabinet", ["account_id"])
    op.create_index("ix_cabinet_client_id", "cabinet", ["client_id"])
    # Reuse-or-create кабинета по четвёрке; НЕ уникальный — история пересозданий допустима.
    op.create_index(
        "ix_cabinet_reuse",
        "cabinet",
        ["account_id", "client_id", "channel", "ad_object_url"],
    )

    op.add_column(
        "campaign",
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinet.id"), nullable=True),
    )
    op.create_index("ix_campaign_cabinet_id", "campaign", ["cabinet_id"])


def downgrade() -> None:
    # Порядок важен: сначала колонка campaign.cabinet_id (FK на cabinet),
    # потом сама таблица cabinet.
    op.drop_index("ix_campaign_cabinet_id", table_name="campaign")
    op.drop_column("campaign", "cabinet_id")
    op.drop_index("ix_cabinet_reuse", table_name="cabinet")
    op.drop_index("ix_cabinet_client_id", table_name="cabinet")
    op.drop_index("ix_cabinet_account_id", table_name="cabinet")
    op.drop_table("cabinet")
