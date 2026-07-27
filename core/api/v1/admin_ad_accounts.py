"""Рекламные кабинеты в админ-панели (`/api/v1/admin/ad-accounts`, `require_admin`).

Веб-зеркало операторского роутера `ad_accounts.py`: те же операции, тот же
сервис, та же модель ответа — отличается только защита. Оператор должен видеть
одинаковую картину и в боте, и в браузере, поэтому логика здесь не дублируется,
а переиспользуется (CLAUDE.md §1.3).
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.ad_accounts import (
    AdAccountIn,
    AdAccountOut,
    AdAccountsOut,
    check_ad_account_response,
    create_ad_account_response,
    delete_ad_account_response,
    list_ad_accounts_response,
)
from core.api.v1.admin import require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/ad-accounts")
async def admin_get_ad_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountsOut:
    """Список рекламных кабинетов для веб-админки."""
    return await list_ad_accounts_response(session)


@router.post("/ad-accounts", status_code=201)
async def admin_post_ad_account(
    payload: AdAccountIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Добавить рекламный кабинет из веб-админки."""
    return await create_ad_account_response(session, payload)


@router.post("/ad-accounts/{ad_account_id}/check")
async def admin_check_ad_account(
    ad_account_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Проверить токен кабинета из веб-админки."""
    return await check_ad_account_response(session, ad_account_id)


@router.delete("/ad-accounts/{ad_account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_ad_account(
    ad_account_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Удалить рекламный кабинет из веб-админки."""
    await delete_ad_account_response(session, ad_account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
