"""brief_object_id

Revision ID: 0017_brief_object_id
Revises: 0016_client_bank_details
Create Date: 2026-09-20

Числовой id объекта рекламы (сообщество/личная страница), разрешённый из
короткого адреса ВК при запуске кампании (spec 2026-09-19-block1-remaining-gaps
§D). Резолвится один раз через `integrations/vk_object.py::resolve_vk_object`
и сохраняется в бриф — повторных попыток по тому же адресу нет.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_brief_object_id"
down_revision: str | None = "0016_client_bank_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("brief", sa.Column("object_numeric_id", sa.BigInteger(), nullable=True))
    op.add_column("brief", sa.Column("object_resolved_kind", sa.String(length=16), nullable=True))
    op.add_column(
        "brief", sa.Column("object_resolved_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("brief", "object_resolved_at")
    op.drop_column("brief", "object_resolved_kind")
    op.drop_column("brief", "object_numeric_id")
