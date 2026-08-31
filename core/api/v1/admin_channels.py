"""Каналы доставки в веб-админке (`/api/v1/admin/senler/*`, `/admin/channels`,
`require_admin`).

Токен Senler — веб-зеркало операторского роутера `senler.py`: та же логика
опознания сообщества и привязки/отвязки токена, что у бота — переиспользуются
готовые хелперы-роутеры (`create_community_token_response`,
`delete_community_token_response`), своей бизнес-логики здесь нет
(CLAUDE.md §1.3). Токен сообщества, как и в операторском роутере, ни в одном
ответе не появляется.

`GET /admin/channels` — просмотр состояния юзербота и kotbot (то же, что бот
показывает командами `/userbot_status` и `/kotbot`): бизнес-логика — в
`services/channels_status.py`, роутер только собирает тело ответа. Подключение
аккаунта (код из SMS, пароль 2FA) сознательно остаётся в боте.
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from services.channels_status import channels_status
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.admin import require_admin
from core.api.v1.senler import (
    CommunityTokenIn,
    CommunityTokenOut,
    create_community_token_response,
    delete_community_token_response,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/senler/community-token", status_code=201)
async def admin_post_community_token(
    payload: CommunityTokenIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommunityTokenOut:
    """Опознать сообщество по токену, сохранить его и сразу проверить Senler."""
    return await create_community_token_response(session, payload)


@router.delete("/senler/community-token", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_community_token(
    reference: Annotated[str, Query(min_length=1, max_length=64)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Отвязать токен сообщества (снять устаревшую или ошибочную привязку)."""
    await delete_community_token_response(session, reference)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class UserbotSessionOut(BaseModel):
    """Одна сессия юзербота — телефон только замаскированным."""

    sender_id: int
    authorized: bool
    unreachable: bool
    phone_masked: str | None


class UserbotChannelOut(BaseModel):
    """Состояние юзербот-сервиса целиком (зеркало `/userbot_status` бота)."""

    configured: bool
    available: bool
    sessions: list[UserbotSessionOut]


class KotbotChannelOut(BaseModel):
    """Состояние kotbot-сервиса (зеркало `/kotbot` бота): настроен и здоров ли."""

    configured: bool
    healthy: bool


class ChannelsOut(BaseModel):
    userbot: UserbotChannelOut
    kotbot: KotbotChannelOut


@router.get("/channels")
async def admin_get_channels() -> ChannelsOut:
    """Состояние каналов доставки — веб-зеркало `/userbot_status` и `/kotbot` бота.

    Только просмотр: подключение аккаунта (код из SMS, пароль 2FA) остаётся в
    боте. Внешний сервис недоступен или не настроен — эндпоинт всё равно
    отвечает 200 с честным описанием состояния (`services.channels_status`
    не бросает исключений), а не падает 500.
    """
    status_data = await channels_status()
    return ChannelsOut(
        userbot=UserbotChannelOut(
            configured=status_data.userbot.configured,
            available=status_data.userbot.available,
            sessions=[
                UserbotSessionOut(
                    sender_id=session.sender_id,
                    authorized=session.authorized,
                    unreachable=session.unreachable,
                    phone_masked=session.phone_masked,
                )
                for session in status_data.userbot.sessions
            ],
        ),
        kotbot=KotbotChannelOut(
            configured=status_data.kotbot.configured,
            healthy=status_data.kotbot.healthy,
        ),
    )
