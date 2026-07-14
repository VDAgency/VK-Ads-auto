"""Внутренний API трекинга брифов (`GET /api/v1/invites`).

Тонкий роутер: выбирает выборку по `status`, зовёт сервис, рендерит. Скоуп —
единственный тенант на текущем этапе (см. briefs.py). SQL/логики здесь нет.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from db.session import get_session
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from services.invite_tracking import InviteView, list_pending, list_recent
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/invites", tags=["invites"])

DEFAULT_ACCOUNT_ID = 1


class InviteItem(BaseModel):
    """Строка трекинга инвайта."""

    contact: str
    variant: str
    channel: str
    sent_at: datetime | None
    received_at: datetime | None
    waiting_days: int


class InvitesOut(BaseModel):
    """Список инвайтов под запрошенный статус."""

    items: list[InviteItem]


def _to_item(view: InviteView) -> InviteItem:
    return InviteItem(
        contact=view.contact,
        variant=view.variant,
        channel=view.channel,
        sent_at=view.sent_at,
        received_at=view.received_at,
        waiting_days=view.waiting_days,
    )


@router.get("")
async def list_invites(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[Literal["pending", "recent"], Query()] = "pending",
) -> InvitesOut:
    """Кого ждём (`pending`) или кто прислал за последнюю неделю (`recent`)."""
    if status == "recent":
        views = await list_recent(session, DEFAULT_ACCOUNT_ID)
    else:
        views = await list_pending(session, DEFAULT_ACCOUNT_ID)
    return InvitesOut(items=[_to_item(v) for v in views])
