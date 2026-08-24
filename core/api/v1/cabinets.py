"""Внутренний API статистики кабинетов (`/api/v1/cabinets`).

Тонкий роутер: список кабинетов, метрики по периоду и явный синк по кабинету.
Мок-гейт и агрегаты — в сервисе `cabinet_stats`. Скоуп — единственный тенант
(см. briefs.py).

`POST /{id}/stats/sync` (задача 2, дефект 1) — GET не должен мутировать данные,
поэтому обновление метрик перед показом вынесено в отдельный явный эндпоинт; бот
дёргает его при входе в кабинет, до чтения `GET /{id}/stats`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from db.session import get_session
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from services.cabinet_stats import cabinet_stats, list_cabinets
from services.stats_sync import CabinetSyncOutcome, cabinet_sync_outcome, sync_cabinet_stats
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
    cpl: float
    is_mock: bool


class CabinetSyncOut(BaseModel):
    """Итог синка метрик ОДНОГО кабинета: исход и построчная сводка по кампаниям.

    `outcome` (A3) — честные три состояния, не два: `"updated"` (что-то реально
    синкнулось), `"nothing_to_update"` (под кабинетом нет активных кампаний — это
    норма, не сбой), `"failed"` (настоящая ошибка синка). `ok` оставлен для
    обратной совместимости с уже читающими его клиентами (бот) — это
    `outcome != "failed"`, т.е. «не было настоящего сбоя»; за различием «обновлено»
    vs «нечего обновлять» — в `outcome`.
    """

    ok: bool
    outcome: CabinetSyncOutcome
    synced: int
    failed: int
    results: dict[int, str]


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
    """Метрики кабинета за период (`all`/`month`/`week`) + производные.

    Только чтение сохранённых данных — не мутирует БД (обновление — отдельный
    `POST /{cabinet_id}/stats/sync`, см. ниже).
    """
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
        cpl=view.cpl,
        is_mock=view.is_mock,
    )


@router.post("/{cabinet_id}/stats/sync")
async def sync_cabinet(
    cabinet_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CabinetSyncOut:
    """Обновить метрики и статус кампаний ЭТОГО кабинета перед показом (дефект 1).

    Синк по кампании никогда не бросает исключение наружу (см.
    `services.stats_sync`): сбой площадки попадает в `results` как `error`, а
    `outcome="failed"` — честная сводка вместо 5xx, чтобы бот показал сохранённые
    данные с пометкой о сбое, а не уронил экран статистики.

    `outcome` считается через `cabinet_sync_outcome` (A3, доработка A2): пустая
    сводка (под этим кабинетом нет ни одной АКТИВНОЙ кампании — она либо не
    запущена (`prepared`/`stopped`/`failed`), либо кабинета с таким id вовсе нет)
    — это `"nothing_to_update"`, а не тривиальный успех и не сбой площадки. Раньше
    (A2, первая версия) такая сводка ошибочно засчитывалась `ok=False` наравне с
    настоящей ошибкой площадки — из-за этого бот рисовал тревожную пометку под
    каждым неподнятым кабинетом, хотя обновлять там было нечего.
    """
    results = await sync_cabinet_stats(session, DEFAULT_ACCOUNT_ID, cabinet_id)
    await session.commit()
    failed = sum(1 for outcome in results.values() if outcome != "ok")
    outcome = cabinet_sync_outcome(results)
    return CabinetSyncOut(
        ok=outcome != "failed",
        outcome=outcome,
        synced=len(results) - failed,
        failed=failed,
        results=results,
    )
