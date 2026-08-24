"""community_token screen_name + community_name

Revision ID: 0012_community_token_screen_name
Revises: 0011_community_token
Create Date: 2026-08-24

Клиенты в брифе почти всегда присылают короткий адрес сообщества
(`vk.ru/djbeauty`), а не числовой id — числовой id из него не извлечь, так что
проверка подключения Senler молча не находила сохранённый токен и уходила в
честное «не смогли проверить» на самом частом случае брифа.

Решение: `groups.getById`, вызванный БЕЗ `group_id`, с одним лишь токеном
сообщества, называет само сообщество (id, screen_name, name) — сообщество
опознаёт себя по токену. Оператор при привязке токена больше не вводит id
руками; `community_id`/`screen_name`/`community_name` теперь заполняет живой
ответ VK, а запуск сопоставляет сообщество из брифа с сохранённым токеном по
любому из двух признаков (`db/community_tokens.py::find_decrypted_token`).

`server_default=''` — только чтобы миграция не упала на уже существующих
строках; для новых строк оба поля обязательны на уровне приложения
(`db/community_tokens.py::save_community_token`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_community_token_screen_name"
down_revision: str | None = "0011_community_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "community_token",
        sa.Column("screen_name", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "community_token",
        sa.Column("community_name", sa.String(length=255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("community_token", "community_name")
    op.drop_column("community_token", "screen_name")
