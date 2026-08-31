"""Кампании, кабинеты и статистика в веб-админке (`/api/v1/admin/*`, `require_admin`).

Веб-зеркала операторских путей `cabinets.py`/`stats.py`/`campaigns.py`: те же
сервисы и те же модели ответа, что у бота — переиспользуются готовые
хелперы-роутеры (`list_cabinets_response`, `cabinet_stats_response`,
`sync_cabinet_response`, `sync_stats_response`, `stop_campaign_response`), своей
бизнес-логики здесь нет (CLAUDE.md §1.3).
"""

from __future__ import annotations

from typing import Annotated, Literal

from db.session import get_session
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.admin import require_admin
from core.api.v1.cabinets import (
    CabinetsOut,
    CabinetSyncOut,
    StatsOut,
    cabinet_stats_response,
    list_cabinets_response,
    sync_cabinet_response,
)
from core.api.v1.campaigns import CampaignStopOut, stop_campaign_response
from core.api.v1.stats import StatsSyncOut, sync_stats_response

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/cabinets")
async def admin_get_cabinets(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CabinetsOut:
    """Список рекламных кабинетов со статистикой (веб-зеркало `/cabinets` бота)."""
    return await list_cabinets_response(session)


@router.get("/cabinets/{cabinet_id}/stats")
async def admin_get_cabinet_stats(
    cabinet_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    period: Annotated[Literal["all", "month", "week"], Query()] = "all",
) -> StatsOut:
    """Метрики кабинета за период (`all`/`month`/`week`) + производные."""
    return await cabinet_stats_response(session, cabinet_id, period)


@router.post("/cabinets/{cabinet_id}/stats/sync")
async def admin_sync_cabinet(
    cabinet_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CabinetSyncOut:
    """Синк метрик и статуса кампаний ЭТОГО кабинета перед показом (дефект 1)."""
    return await sync_cabinet_response(session, cabinet_id)


@router.post("/stats/sync")
async def admin_sync_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatsSyncOut:
    """Общий синк метрик и статусов всех активных кампаний тенанта."""
    return await sync_stats_response(session)


@router.post("/campaigns/{campaign_id}/stop")
async def admin_stop_campaign(
    campaign_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CampaignStopOut:
    """Остановить кампанию на площадке и зафиксировать статус `stopped`."""
    return await stop_campaign_response(session, campaign_id)
