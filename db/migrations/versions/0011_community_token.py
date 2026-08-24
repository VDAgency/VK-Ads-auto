"""community_token table

Revision ID: 0011_community_token
Revises: 0010_ad_account
Create Date: 2026-08-24

Проверка подключения Senler (B2, spec 2026-08-24 §7): токен сообщества VK,
которым спрашиваем classic API `groups.getCallbackServers`, чтобы узнать,
подключён ли к сообществу чат-бот Senler, перед тем как запускать кампанию с
этой целью.

НЕ путать с `ad_account`: тот — доступ к рекламному кабинету (Ads API), этот —
доступ к самому сообществу (classic API), заводится отдельно, своим токеном.
Шифрование то же (Fernet, `VK_ADS_SECRET_KEY`), то же мягкое архивирование
вместо физического удаления — второй токен того же сообщества архивирует
прежнюю активную строку и заводит новую.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_community_token"
down_revision: str | None = "0010_ad_account"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "community_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("community_id", sa.String(length=32), nullable=False),
        # Секрет: Fernet-шифротекст, ключ VK_ADS_SECRET_KEY живёт только в окружении.
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_community_token_account_id", "community_token", ["account_id"])
    # Один активный токен на сообщество внутри тенанта. Частичный индекс: архивные
    # строки не мешают привязать новый токен взамен архивированного.
    op.create_index(
        "uq_community_token_active",
        "community_token",
        ["account_id", "community_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_community_token_active", table_name="community_token")
    op.drop_index("ix_community_token_account_id", table_name="community_token")
    op.drop_table("community_token")
