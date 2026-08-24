"""Внутренний API токена сообщества для проверки подключения Senler (B2).

Тонкий роутер: вся логика — в `db/community_tokens.py`, `integrations/vk_community.py`
и `services/senler.py`. Эндпоинт операторский, поэтому снаружи закрыт через
`infra/Caddyfile` (404), как `/ad-accounts` и `/invites` — оба тоже принимают
секреты и не предназначены для публики.

Ключевой инвариант: токен не появляется ни в одном ответе. Наружу уходит только
id сообщества и факт подключения Senler — этого достаточно, чтобы оператор сразу
увидел результат привязки, не заглядывая в базу.
"""

from __future__ import annotations

from typing import Annotated

from db.community_tokens import save_community_token
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException
from integrations.vk_community import VkCommunityUnreachable, fetch_callback_servers
from pydantic import BaseModel, Field
from services.secret_box import NotConfiguredError
from services.senler import detect_senler
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/senler", tags=["senler"])

# Скоуп единственного тенанта — та же конвенция, что в ad_accounts.py/briefs.py.
DEFAULT_ACCOUNT_ID = 1

_UNREACHABLE_REASON = "Токен сохранён, но проверить подключение не удалось — VK не ответил."


class CommunityTokenIn(BaseModel):
    """Вход привязки. `token` приходит только сюда и дальше не возвращается."""

    community_id: str = Field(min_length=1, max_length=32)
    token: str = Field(min_length=8, max_length=512)


class CommunityTokenOut(BaseModel):
    """Результат привязки: без токена — только факт подключения Senler."""

    community_id: str
    connected: bool
    reason: str


@router.post("/community-token", status_code=201)
async def post_community_token(
    payload: CommunityTokenIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommunityTokenOut:
    """Привязать токен сообщества и сразу проверить, подключён ли Senler.

    Сохранение и проверка — раздельные шаги: даже если VK сейчас недоступен,
    токен уже сохранён (следующий запуск кампании проверит подключение сам,
    `services.launch_service._verify_senler`), а оператор получает честный
    статус вместо утечки 500.
    """
    try:
        await save_community_token(session, DEFAULT_ACCOUNT_ID, payload.community_id, payload.token)
    except NotConfiguredError:
        raise HTTPException(status_code=500, detail="encryption_key_missing") from None
    await session.commit()

    try:
        servers = await fetch_callback_servers(payload.token, payload.community_id)
    except VkCommunityUnreachable:
        return CommunityTokenOut(
            community_id=payload.community_id, connected=False, reason=_UNREACHABLE_REASON
        )
    check = detect_senler(servers)
    return CommunityTokenOut(
        community_id=payload.community_id, connected=check.connected, reason=check.reason
    )


__all__ = ["router"]
