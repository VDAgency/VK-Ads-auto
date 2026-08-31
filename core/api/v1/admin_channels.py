"""Каналы доставки в веб-админке (`/api/v1/admin/senler/*`, `require_admin`).

Веб-зеркало операторского роутера `senler.py`: та же логика опознания
сообщества и привязки/отвязки токена, что у бота — переиспользуются готовые
хелперы-роутеры (`create_community_token_response`,
`delete_community_token_response`), своей бизнес-логики здесь нет
(CLAUDE.md §1.3). Токен сообщества, как и в операторском роутере, ни в одном
ответе не появляется.
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends, Query, Response, status
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
