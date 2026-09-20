"""client_bank_details

Revision ID: 0016_client_bank_details
Revises: 0015_sessions_valid_from
Create Date: 2026-09-19

Банковские реквизиты клиента для документов (кабинет, spec §E). Хранение
открытым текстом (не секрет доступа, сервер в РФ) — формат и контрольные суммы
проверяет `services/bank_details.py` до сохранения строки. Одна запись на
клиента (`UniqueConstraint(account_id, client_id)`) — сохранение всегда
заменяет прежнюю.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_client_bank_details"
down_revision: str | None = "0015_sessions_valid_from"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_bank_details",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=False),
        sa.Column("payer_name", sa.String(length=255), nullable=False),
        sa.Column("bank_name", sa.String(length=255), nullable=False),
        sa.Column("bik", sa.String(length=9), nullable=False),
        sa.Column("settlement_account", sa.String(length=20), nullable=False),
        sa.Column("correspondent_account", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "account_id", "client_id", name="uq_client_bank_details_account_client"
        ),
    )
    op.create_index("ix_client_bank_details_account_id", "client_bank_details", ["account_id"])
    op.create_index("ix_client_bank_details_client_id", "client_bank_details", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_client_bank_details_client_id", table_name="client_bank_details")
    op.drop_index("ix_client_bank_details_account_id", table_name="client_bank_details")
    op.drop_table("client_bank_details")
