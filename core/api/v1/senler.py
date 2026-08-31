"""Внутренний API токена сообщества для проверки подключения Senler (B2).

Тонкий роутер: вся логика — в `db/community_tokens.py`, `integrations/vk_community.py`
и `services/senler.py`. Эндпоинт операторский, поэтому снаружи закрыт через
`infra/Caddyfile` (404), как `/ad-accounts` и `/invites` — оба тоже принимают
секреты и не предназначены для публики.

Оператор вводит только сам токен. `community_id`/`screen_name`/`community_name`
эндпоинт узнаёт живым запросом `groups.getById` без `group_id`
(`integrations/vk_community.py::fetch_own_community`) — сообщество опознаёт себя
по токену, тем же приёмом, что `services/ad_accounts.py::add_account` для
рекламных кабинетов. Опознание идёт ДО записи в БД: не смогли — токен не
сохраняем и честно говорим об этом, а не имитируем успех (CLAUDE.md §7).

Ключевой инвариант: токен не появляется ни в одном ответе. Наружу уходит id,
название и факт подключения Senler — этого достаточно, чтобы оператор сразу
увидел результат привязки, не заглядывая в базу.
"""

from __future__ import annotations

from typing import Annotated

from db.community_tokens import delete_community_token, save_community_token
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from integrations.vk_community import (
    VkCommunityUnreachable,
    fetch_callback_servers,
    fetch_own_community,
)
from pydantic import BaseModel, Field
from services.secret_box import NotConfiguredError
from services.senler import detect_senler
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/senler", tags=["senler"])

# Скоуп единственного тенанта — та же конвенция, что в ad_accounts.py/briefs.py.
DEFAULT_ACCOUNT_ID = 1

_UNREACHABLE_REASON = "Токен сохранён, но проверить подключение не удалось — VK не ответил."


class CommunityTokenIn(BaseModel):
    """Вход привязки. `token` приходит только сюда и дальше не возвращается.

    Id сообщества оператор больше не вводит — его называет сам VK.
    """

    token: str = Field(min_length=8, max_length=512)


class CommunityTokenOut(BaseModel):
    """Результат привязки: без токена — id, название сообщества и факт подключения Senler."""

    community_id: str
    community_name: str
    connected: bool
    reason: str


async def create_community_token_response(
    session: AsyncSession, payload: CommunityTokenIn
) -> CommunityTokenOut:
    """Опознать сообщество по токену, сохранить токен, сразу проверить Senler.

    Три шага строго по порядку:
    1. `groups.getById` без `group_id` — опознаём сообщество. Не ответил/вернул
       ошибку -> 422, токен НЕ сохраняем (не подтверждённый токен хранить
       незачем, а оператору нужен честный отказ, а не мнимый успех).
    2. Сохранение — уже с id/screen_name/name из шага 1.
    3. Проверка `groups.getCallbackServers` — отдельно от сохранения: даже
       если VK сейчас недоступен именно для неё, токен уже сохранён (следующий
       запуск кампании проверит подключение сам,
       `services.launch_service._verify_senler`), а оператор получает честный
       статус вместо утечки 500.

    Общая часть для операторского эндпоинта и веб-админки.
    """
    try:
        identity = await fetch_own_community(payload.token)
    except VkCommunityUnreachable:
        raise HTTPException(status_code=422, detail="community_unreachable") from None

    try:
        await save_community_token(
            session,
            DEFAULT_ACCOUNT_ID,
            identity.id,
            payload.token,
            screen_name=identity.screen_name,
            community_name=identity.name,
        )
    except NotConfiguredError:
        raise HTTPException(status_code=500, detail="encryption_key_missing") from None
    await session.commit()

    try:
        servers = await fetch_callback_servers(payload.token, identity.id)
    except VkCommunityUnreachable:
        return CommunityTokenOut(
            community_id=identity.id,
            community_name=identity.name,
            connected=False,
            reason=_UNREACHABLE_REASON,
        )
    check = detect_senler(servers)
    return CommunityTokenOut(
        community_id=identity.id,
        community_name=identity.name,
        connected=check.connected,
        reason=check.reason,
    )


async def delete_community_token_response(session: AsyncSession, reference: str) -> None:
    """Отвязать токен сообщества (снять устаревшую или ошибочную привязку).

    `reference` — короткий адрес сообщества ИЛИ его числовой id, тем же
    приёмом и приоритетом, что и поиск токена под запуск
    (`db.community_tokens.find_decrypted_token`): цифры целиком — числовой id,
    иначе — короткий адрес (регистр не важен). Оператор обычно знает адрес
    (это то же, что в ссылке из брифа), а не числовой id сообщества.

    Бросает `HTTPException(404, "not_found")` — активной привязки для этого
    сообщества не было. Общая часть для операторского эндпоинта и веб-админки.
    """
    ref = reference.strip()
    community_id = ref if ref.isdigit() else None
    screen_name = None if community_id else ref.lower()
    removed = await delete_community_token(
        session, DEFAULT_ACCOUNT_ID, community_id=community_id, screen_name=screen_name
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="not_found")
    await session.commit()


@router.post("/community-token", status_code=201)
async def post_community_token(
    payload: CommunityTokenIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommunityTokenOut:
    """Опознать сообщество по токену, сохранить токен, сразу проверить Senler."""
    return await create_community_token_response(session, payload)


@router.delete("/community-token", status_code=status.HTTP_204_NO_CONTENT)
async def delete_community_token_endpoint(
    reference: Annotated[str, Query(min_length=1, max_length=64)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Отвязать токен сообщества (снять устаревшую или ошибочную привязку).

    Раньше `db.community_tokens.delete_community_token` существовал, но
    ниоткуда не вызывался — у оператора не было способа снять привязку иначе
    как правкой базы руками (ревью 2026-08-24, дефект 3). Это и была причина,
    по которой дефект 1 (аномалия с двумя активными токенами на один короткий
    адрес) некому было бы исправить в проде.
    """
    await delete_community_token_response(session, reference)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
