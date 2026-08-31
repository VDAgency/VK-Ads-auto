"""Полный список брифов тенанта — не только пришедшие по приглашению.

`invite_tracking` строит списки «Ждём»/«Пришли» из таблицы `BriefInvite` (см.
докстринг там) и поэтому не видит брифы, пришедшие без приглашения: по
реферальной ссылке от другого клиента или из холодного трафика с лендинга
(PRODUCT.md §«Три источника трафика» — второй и третий источники). Этот модуль
даёт оператору полный список: все брифы тенанта, без привязки к инвайтам.

Headless-ядро: выборка — в `db.repositories`, здесь только сборка строк для
канала (бот/веб). Никакой презентации (форматирования дат) — только данные.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from db.repositories import list_briefs
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class BriefListItem:
    """Строка полного списка брифов тенанта для рендера в канале (веб/бот)."""

    brief_id: int
    variant: str
    status: str
    source: str  # web | bot
    created_at: datetime
    client_id: int | None
    client_name: str | None


async def list_all(
    session: AsyncSession,
    account_id: int,
    *,
    limit: int = 50,
) -> list[BriefListItem]:
    """Все брифы тенанта, новые сверху (включая пришедшие без приглашения)."""
    rows = await list_briefs(session, account_id, limit=limit)
    return [
        BriefListItem(
            brief_id=brief.id,
            variant=brief.variant,
            status=brief.status,
            source=brief.source,
            created_at=brief.created_at,
            client_id=brief.client_id,
            client_name=client_name,
        )
        for brief, client_name in rows
    ]
