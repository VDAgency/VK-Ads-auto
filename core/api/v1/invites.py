"""Внутренний API трекинга брифов (`GET /api/v1/invites`).

Тонкий роутер: выбирает выборку по `status`, зовёт сервис, рендерит. Скоуп —
единственный тенант на текущем этапе (см. briefs.py). SQL/логики здесь нет.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from config.settings import get_settings
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from services.brief_parser import BriefVariant
from services.contact import ContactParseError, detect_contact
from services.delivery.factory import build_delivery_router
from services.invite_tracking import InviteView, list_pending, list_recent
from services.invites import create_invite
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/invites", tags=["invites"])

DEFAULT_ACCOUNT_ID = 1


class InviteItem(BaseModel):
    """Строка трекинга инвайта."""

    contact: str
    contact_name: str | None = None
    variant: str
    channel: str
    sent_at: datetime | None
    received_at: datetime | None
    waiting_days: int
    # id присланного брифа (для открытия карточки в боте); None у ожидающих.
    brief_id: int | None = None


class InvitesOut(BaseModel):
    """Список инвайтов под запрошенный статус."""

    items: list[InviteItem]


def _to_item(view: InviteView) -> InviteItem:
    return InviteItem(
        contact=view.contact,
        contact_name=view.contact_name,
        variant=view.variant,
        channel=view.channel,
        sent_at=view.sent_at,
        received_at=view.received_at,
        waiting_days=view.waiting_days,
        brief_id=view.brief_id,
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


class CreateInviteIn(BaseModel):
    """Запрос на создание инвайта: вариант брифа + контакт клиента + оператор."""

    variant: Literal["individual", "community"]
    contact: str
    operator_telegram_id: int


class CreateInviteOut(BaseModel):
    """Итог создания инвайта для рендера оператору (сценарии §8.1 спеки)."""

    invite_id: int
    status: str  # sent | failed
    channel: str  # telegram | email | manual
    fallback_text: str | None = None
    error: str | None = None


@router.post("", status_code=201)
async def create_invite_endpoint(
    data: CreateInviteIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreateInviteOut:
    """Создать инвайт и попытаться доставить ссылку на бриф выбранным каналом."""
    try:
        contact = detect_contact(data.contact)
    except ContactParseError as exc:
        raise HTTPException(
            status_code=422, detail="Не распознан контакт (email/телефон/@username)."
        ) from exc

    result = await create_invite(
        session,
        DEFAULT_ACCOUNT_ID,
        data.operator_telegram_id,
        BriefVariant(data.variant),
        contact,
        get_settings().public_base_url,
        # Отправитель в Telegram — оператор, создающий инвайт (его сессия юзербота).
        build_delivery_router(sender_id=data.operator_telegram_id),
    )
    await session.commit()
    return CreateInviteOut(
        invite_id=result.invite_id,
        status=result.status,
        channel=result.channel,
        fallback_text=result.fallback_text,
        error=result.error,
    )
