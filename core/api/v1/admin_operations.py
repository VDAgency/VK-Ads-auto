"""Разные операции веб-админки без отдельного домена (`/api/v1/admin/*`, `require_admin`).

Веб-зеркала операторских путей, которых у веба ещё не было: справочник площадок
размещения, запуск кампании без креатива и привязка кабинета к клиенту. Та же
логика, что у бота — переиспользуются существующие сервисы и хелперы-роутеры
операторских модулей (`briefs.py`, `ad_accounts.py`), своей бизнес-логики здесь
нет (CLAUDE.md §1.3).
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from services.goals import goal_titles, subscription_targets
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.ad_accounts import (
    AdAccountClientIn,
    AdAccountOut,
    set_ad_account_client_response,
)
from core.api.v1.admin import require_admin
from core.api.v1.briefs import CreativeLaunchOut, LaunchIn, launch_brief_response

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class SurfaceOut(BaseModel):
    """Одна площадка подписки (зеркало карточки из бот-команды `/surfaces`)."""

    kind: str
    title: str
    hint: str
    available: bool
    goal: str
    goal_title: str
    needs_creative: bool


class SurfacesOut(BaseModel):
    items: list[SurfaceOut]


@router.get("/surfaces")
async def admin_get_surfaces() -> SurfacesOut:
    """Справочник площадок размещения — веб-зеркало команды `/surfaces` в боте.

    Данные — из `services.goals` (единственный источник правды для бота и веба),
    своей логики здесь нет.
    """
    titles = goal_titles()
    return SurfacesOut(
        items=[
            SurfaceOut(
                kind=target.kind,
                title=target.title,
                hint=target.hint,
                available=target.available,
                goal=target.goal,
                goal_title=titles.get(target.goal, target.goal),
                needs_creative=target.needs_creative,
            )
            for target in subscription_targets()
        ]
    )


@router.post("/briefs/{brief_id}/launch", status_code=201)
async def admin_launch_brief(
    brief_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    data: LaunchIn | None = None,
) -> CreativeLaunchOut:
    """Запустить кампанию без креатива из веб-админки (зеркало бот-эндпоинта)."""
    ad_account_id = data.ad_account_id if data is not None else None
    return await launch_brief_response(session, brief_id, ad_account_id)


@router.patch("/ad-accounts/{ad_account_id}/client")
async def admin_patch_ad_account_client(
    ad_account_id: int,
    payload: AdAccountClientIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Привязать/отвязать кабинет от клиента из веб-админки (`client_id=None` — снять)."""
    return await set_ad_account_client_response(session, ad_account_id, payload)
