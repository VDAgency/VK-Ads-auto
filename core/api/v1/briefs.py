"""Внутренний API приёма брифа (`POST /api/v1/briefs`).

Тонкий роутер: валидирует вход, зовёт сервис, рендерит результат. SQL и бизнес-логики
здесь нет (headless-инвариант).

Два пути приёма:
- без токена — публичная веб-форма/реф-ссылка (как было);
- с токеном — бриф пришёл по инвайту оператора: связываем бриф с `BriefInvite`,
  метим инвайт `received` и уведомляем оператора (spec §7.2, §8.3).
"""

from __future__ import annotations

from typing import Annotated, Literal

from config.settings import get_settings
from db.models import BriefInvite
from db.repositories import find_brief_invite_by_token, get_operator_by_id
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from services.brief_parser import BriefValidationError, BriefVariant
from services.briefs import InviteReceiveConflictError, intake_brief
from services.notifier import SendMessage, notify_operator_brief_received
from services.telegram_send import build_telegram_sender
from sqlalchemy.ext.asyncio import AsyncSession

from core.ratelimit import SlidingWindowRateLimiter

router = APIRouter(prefix="/briefs", tags=["briefs"])

# Единственный тенант на текущем этапе (швы мульти-тенанта заложены, см. PROJECT.md §6).
DEFAULT_ACCOUNT_ID = 1

# Rate-limit публичного приёма брифа: 30 запросов в минуту с одного IP (spec §7.2).
# Один инстанс на процесс; при масштабировании вынести в Redis.
_BRIEFS_LIMITER = SlidingWindowRateLimiter(limit=30, window_seconds=60)


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Лимитер приёма брифов (переопределяется в тестах)."""
    return _BRIEFS_LIMITER


def get_operator_notifier() -> SendMessage:
    """Колбэк отправки сообщения оператору (переопределяется в тестах).

    По умолчанию — отправка через Telegram Bot API по httpx (ядро не импортирует
    aiogram; бот и ядро — разные процессы).
    """
    return build_telegram_sender(get_settings().bot_token.get_secret_value())


class BriefIn(BaseModel):
    """Входящий бриф: вариант (физлицо/сообщество) + сырые поля формы + опц. токен."""

    variant: Literal["individual", "community"]
    payload: dict[str, str]
    ref_code: str | None = None
    token: str | None = None


class BriefOut(BaseModel):
    """Результат приёма брифа."""

    brief_id: int
    client_id: int
    status: str


@router.post("", status_code=201)
async def submit_brief(
    data: BriefIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    notifier: Annotated[SendMessage, Depends(get_operator_notifier)],
    limiter: Annotated[SlidingWindowRateLimiter, Depends(get_rate_limiter)],
) -> BriefOut:
    """Принять бриф с веб-формы/по токену и сохранить в БД."""
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="rate_limited")

    invite = None
    if data.token is not None:
        invite = await find_brief_invite_by_token(session, data.token)
        if invite is None:
            raise HTTPException(status_code=404, detail="invite_not_found")
        if invite.status != "sent":
            raise HTTPException(status_code=409, detail="invite_not_active")

    try:
        brief = await intake_brief(
            session,
            DEFAULT_ACCOUNT_ID,
            BriefVariant(data.variant),
            data.payload,
            source="web",
            ref_code=data.ref_code,
            invite=invite,
        )
    except BriefValidationError as exc:
        raise HTTPException(status_code=422, detail={"missing": exc.missing}) from exc
    except InviteReceiveConflictError as exc:
        raise HTTPException(status_code=409, detail="invite_not_active") from exc

    if invite is not None:
        await _notify_operator(session, notifier, invite, brief.id, data.variant)

    assert brief.client_id is not None  # сервис всегда привязывает клиента
    return BriefOut(brief_id=brief.id, client_id=brief.client_id, status=brief.status)


async def _notify_operator(
    session: AsyncSession,
    notifier: SendMessage,
    invite: BriefInvite,
    brief_id: int,
    variant: str,
) -> None:
    """Уведомить оператора о приходе брифа."""
    operator = await get_operator_by_id(session, invite.operator_id)
    if operator is None:
        return
    await notify_operator_brief_received(
        notifier,
        operator_telegram_id=operator.telegram_id,
        contact=invite.contact_value,
        sent_at=invite.delivered_at,
        channel=invite.channel,
        variant=variant,
        brief_id=brief_id,
    )
