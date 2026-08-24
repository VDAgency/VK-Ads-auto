"""ad_account.client_id

Revision ID: 0013_ad_account_client
Revises: 0012_community_token_screen_name
Create Date: 2026-08-25

Привязка рекламного кабинета к клиенту (spec 2026-08-25-cabinet-client-binding
§1.1). Сегодня в базе один кабинет и девять клиентов — привязка должна быть
необязательной, иначе ни один бриф не нашёл бы кабинета после миграции.

Колонка nullable, БЕЗ `server_default`: у всех существующих строк `client_id`
становится `NULL`, то есть «общий кабинет оператора», подходящий любому клиенту
— ровно нынешнее поведение. Заполненное значение закрепляет кабинет за
конкретным клиентом (`db/repositories.py::list_ad_accounts_for_client`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_ad_account_client"
down_revision: str | None = "0012_community_token_screen_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ad_account",
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=True),
    )
    op.create_index("ix_ad_account_client_id", "ad_account", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_ad_account_client_id", table_name="ad_account")
    op.drop_column("ad_account", "client_id")
