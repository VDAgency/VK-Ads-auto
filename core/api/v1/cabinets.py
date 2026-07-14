"""Внутренний API статистики кабинетов (`/api/v1/cabinets`).

Тонкий роутер: список кабинетов и метрики по периоду. Мок-гейт и агрегаты — в
сервисе `cabinet_stats`. Скоуп — единственный тенант (см. briefs.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from db.session import get_session
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from services.cabinet_stats import cabinet_stats, list_cabinets
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cabinets", tags=["cabinets"])

DEFAULT_ACCOUNT_ID = 1


class CabinetItem(BaseModel):
    id: str
    name: str
    status: str
    launched_at: datetime
    is_mock: bool


class CabinetsOut(BaseModel):
    items: list[CabinetItem]


class StatsOut(BaseModel):
    cabinet_id: str
    period: str
    shows: float
    clicks: float
    spent: float
    results: float
    ctr: float
    cpc: float
    is_mock: bool


@router.get("")
async def get_cabinets(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CabinetsOut:
    """Список рекламных кабинетов (реальные или демо, пока открыт мок-гейт)."""
    views = await list_cabinets(session, DEFAULT_ACCOUNT_ID)
    return CabinetsOut(
        items=[
            CabinetItem(
                id=v.id,
                name=v.name,
                status=v.status,
                launched_at=v.launched_at,
                is_mock=v.is_mock,
            )
            for v in views
        ]
    )


@router.get("/{cabinet_id}/stats")
async def get_cabinet_stats(
    cabinet_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    period: Annotated[Literal["all", "month", "week"], Query()] = "all",
) -> StatsOut:
    """Метрики кабинета за период (`all`/`month`/`week`) + производные."""
    view = await cabinet_stats(session, DEFAULT_ACCOUNT_ID, cabinet_id, period)
    return StatsOut(
        cabinet_id=view.cabinet_id,
        period=view.period,
        shows=view.shows,
        clicks=view.clicks,
        spent=view.spent,
        results=view.results,
        ctr=view.ctr,
        cpc=view.cpc,
        is_mock=view.is_mock,
    )
