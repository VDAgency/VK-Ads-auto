"""Рекламные кабинеты в админ-панели (`/api/v1/admin/ad-accounts`, `require_admin`).

Веб-зеркало операторского роутера `ad_accounts.py`: те же операции, тот же
сервис, та же модель ответа — отличается только защита. Оператор должен видеть
одинаковую картину и в боте, и в браузере, поэтому логика здесь не дублируется,
а переиспользуется (CLAUDE.md §1.3).
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.ad_accounts import (
    AdAccountIn,
    AdAccountOut,
    AdAccountsOut,
    AgencyCabinetIn,
    check_ad_account_response,
    create_ad_account_response,
    create_agency_cabinet_response,
    delete_ad_account_response,
    list_ad_accounts_response,
)
from core.api.v1.admin import require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/ad-accounts")
async def admin_get_ad_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
    client_id: Annotated[int | None, Query()] = None,
) -> AdAccountsOut:
    """Список рекламных кабинетов для веб-админки; `client_id` сужает до пригодных
    этому клиенту (общие плюс закреплённые за ним), ровно как у операторского
    оригинала (`GET /api/v1/ad-accounts`) — тот же хелпер `list_ad_accounts_response`,
    своей логики фильтрации здесь нет (CLAUDE.md §1.3).
    """
    return await list_ad_accounts_response(session, client_id)


@router.post("/ad-accounts", status_code=201)
async def admin_post_ad_account(
    payload: AdAccountIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Добавить рекламный кабинет из веб-админки."""
    return await create_ad_account_response(session, payload)


@router.post("/ad-accounts/agency-cabinets", status_code=201)
async def admin_post_agency_cabinet(
    payload: AgencyCabinetIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Завести клиенту рекламный кабинет VK автоматически из веб-админки (зеркало).

    Тот же `create_agency_cabinet_response`, что и у операторского пути: обработка
    отказов сервиса (предохранитель `vk_agency_confirmed`, отсутствующий ИНН,
    половинчатые состояния VK) не дублируется, а переиспользуется целиком.
    """
    return await create_agency_cabinet_response(session, payload)


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
