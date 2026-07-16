"""brief_invite.contact_name column

Revision ID: 0006_brief_invite_contact_name
Revises: 0005_brief_invite
Create Date: 2026-07-16

Имя получателя, добытое каналом доставки в момент отправки (Telegram first/last
name) — для читаемого списка «Ждём бриф» (имя рядом с @username).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_brief_invite_contact_name"
down_revision: str | None = "0005_brief_invite"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "brief_invite",
        sa.Column("contact_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("brief_invite", "contact_name")
