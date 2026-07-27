"""ad_account table + campaign.ad_account_id

Revision ID: 0010_ad_account
Revises: 0009_cabinet
Create Date: 2026-07-27

Мультикабинетность (docs/superpowers/specs/2026-07-27-multi-cabinet-design.md §4):
пул доступов оператора к рекламным кабинетам VK с шифрованным токеном на строку.
Токен на кабинет обязателен — проверено живыми запросами: параметр `sudo`
работает только в веб-интерфейсе, через API игнорируется.

`campaign.ad_account_id` — чтобы остановку и статистику кампании можно было
выполнить токеном ТОГО кабинета, где она заведена. Старые строки остаются NULL:
они создавались, когда токен был один и лежал в `.env`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_ad_account"
down_revision: str | None = "0009_cabinet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ad_account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=True),
        # Секреты: Fernet-шифротекст, ключ VK_ADS_SECRET_KEY живёт только в окружении.
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_encrypted", sa.Text(), nullable=True),
        sa.Column("token_tail", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("advertiser_kind", sa.String(length=16), nullable=False, server_default="owner"),
        sa.Column("advertiser_name", sa.String(length=255), nullable=True),
        sa.Column("advertiser_inn", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("health", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health_error", sa.String(length=255), nullable=True),
        sa.Column("balance_rub", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ad_account_account_id", "ad_account", ["account_id"])
    op.create_index("ix_ad_account_tenant", "ad_account", ["account_id", "status"])
    # Один активный кабинет на один VK-id внутри тенанта. Частичный, чтобы
    # архивные строки не мешали завести тот же кабинет заново после удаления.
    op.create_index(
        "uq_ad_account_active",
        "ad_account",
        ["account_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.add_column(
        "campaign",
        sa.Column("ad_account_id", sa.Integer(), sa.ForeignKey("ad_account.id"), nullable=True),
    )
    op.create_index("ix_campaign_ad_account_id", "campaign", ["ad_account_id"])


def downgrade() -> None:
    # Порядок важен: сначала колонка campaign.ad_account_id (FK на ad_account),
    # потом сама таблица.
    op.drop_index("ix_campaign_ad_account_id", table_name="campaign")
    op.drop_column("campaign", "ad_account_id")
    op.drop_index("uq_ad_account_active", table_name="ad_account")
    op.drop_index("ix_ad_account_tenant", table_name="ad_account")
    op.drop_index("ix_ad_account_account_id", table_name="ad_account")
    op.drop_table("ad_account")
