"""Внутренний API создания инвайта на бриф (`POST /api/v1/invites`, spec §7.1).

Тонкий роутер: распознаёт контакт, находит/создаёт оператора, зовёт сервис
`create_invite`. Слой доставки собирается из настроек (`get_delivery_router`) —
переопределяется в тестах на стаб. SQL и бизнес-логики здесь нет.
"""

from __future__ import annotations

from typing import Annotated, Literal

from config.settings import get_settings
from db.repositories import get_or_create_operator
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.contact import ContactParseError, detect_contact
from services.delivery import DeliveryRouter
from services.invites import build_delivery_router, create_invite
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/invites", tags=["invites"])

# Единственный тенант на текущем этапе (швы мульти-тенанта заложены, PROJECT.md §6).
DEFAULT_ACCOUNT_ID = 1


def get_delivery_router() -> DeliveryRouter:
    """Собрать роутер доставки из настроек (переопределяется в тестах)."""
    return build_delivery_router(get_settings())


class InviteIn(BaseModel):
    """Запрос на отправку брифа: вариант + контакт клиента (одной строкой)."""

    variant: Literal["individual", "community"]
    contact: str
    operator_telegram_id: int


class InviteOut(BaseModel):
    """Результат отправки: id инвайта, статус, канал и (при необходимости) fallback."""

    invite_id: int
    status: str
    channel: str
    fallback_text: str | None = None
    error: str | None = None


@router.post("", status_code=201)
async def submit_invite(
    data: InviteIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    delivery_router: Annotated[DeliveryRouter, Depends(get_delivery_router)],
) -> InviteOut:
    """Создать инвайт и отправить клиенту ссылку на бриф по подходящему каналу."""
    try:
        contact = detect_contact(data.contact)
    except ContactParseError as exc:
        raise HTTPException(status_code=422, detail="unrecognized_contact") from exc

    operator = await get_or_create_operator(session, DEFAULT_ACCOUNT_ID, data.operator_telegram_id)
    result = await create_invite(
        session,
        account_id=DEFAULT_ACCOUNT_ID,
        operator_id=operator.id,
        variant=data.variant,
        contact=contact,
        router=delivery_router,
        base_url=get_settings().public_base_url,
    )
    return InviteOut(
        invite_id=result.invite_id,
        status=result.status,
        channel=result.channel,
        fallback_text=result.fallback_text,
        error=result.error,
    )
