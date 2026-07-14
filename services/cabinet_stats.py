"""Статистика рекламных кабинетов (команда №4) с мок-гейтом (§6–§7 spec 2026-07-15).

Живых данных VK пока нет (адаптер — скелет, боевые вызовы заблокированы статусом ИП).
Модель `Stat` есть, но пустая. Поэтому: реальные агрегаты, если они есть; иначе, пока
мок-гейт открыт, — детерминированные демо-кабинеты и метрики (флаг `is_mock=True`).
Моки не пишутся в БД (§7): синтез на лету, seed по account_id для стабильности.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from config.settings import get_settings
from db.repositories import (
    aggregate_cabinet_stats,
    count_clients,
    list_stat_campaign_ids,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.mock_gate import mock_enabled

Period = Literal["all", "month", "week"]

_PERIOD_DAYS: dict[Period, int | None] = {"all": None, "month": 30, "week": 7}


@dataclass(frozen=True, slots=True)
class CabinetView:
    """Карточка кабинета для списка."""

    id: str
    name: str
    status: str
    launched_at: datetime
    is_mock: bool


@dataclass(frozen=True, slots=True)
class StatsView:
    """Метрики кабинета за период + производные."""

    cabinet_id: str
    period: Period
    shows: float
    clicks: float
    spent: float
    results: float
    is_mock: bool

    @property
    def ctr(self) -> float:
        return round(self.clicks / self.shows * 100, 2) if self.shows else 0.0

    @property
    def cpc(self) -> float:
        return round(self.spent / self.clicks, 2) if self.clicks else 0.0


# --- Детерминированные моки (стабильны в пределах account_id) ---------------

_MOCK_CABINETS = [
    ("demo-1", "Кофейня «Тёплый угол»", "active"),
    ("demo-2", "Автосервис «Гараж 24»", "active"),
    ("demo-3", "Студия йоги «Вдох»", "paused"),
]


def _mock_launched_at(index: int, now: datetime) -> datetime:
    """Даты запуска демо-кабинетов — раскиданы в прошлом для правдоподобия периодов."""
    return now - timedelta(days=45 - index * 15)


def _mock_metrics(cabinet_id: str, period: Period) -> dict[str, float]:
    """Правдоподобные метрики, зависящие от кабинета и периода (без случайности)."""
    seed = sum(ord(c) for c in cabinet_id)
    days = _PERIOD_DAYS[period] or 45
    shows = float(seed * days * 12)
    clicks = round(shows * 0.021, 0)
    spent = round(clicks * 14.5, 0)
    results = round(clicks * 0.11, 0)
    return {"shows": shows, "clicks": clicks, "spent": spent, "results": results}


def _period_since(period: Period, now: datetime) -> datetime | None:
    days = _PERIOD_DAYS[period]
    return None if days is None else now - timedelta(days=days)


async def _gate_open(
    session: AsyncSession, account_id: int, *, real_count: int, now: datetime
) -> bool:
    settings = get_settings()
    clients = await count_clients(session, account_id)
    return mock_enabled(
        real_count=real_count,
        clients_count=clients,
        mock_until=settings.mock_until,
        mock_max_clients=settings.mock_max_clients,
        now=now,
    )


async def list_cabinets(
    session: AsyncSession,
    account_id: int,
    *,
    now: datetime | None = None,
) -> list[CabinetView]:
    """Список кабинетов: реальные (из `Stat`) или демо, пока открыт мок-гейт."""
    now = now or datetime.now(UTC)
    campaign_ids = await list_stat_campaign_ids(session, account_id)
    if campaign_ids:
        return [
            CabinetView(id=cid, name=cid, status="active", launched_at=now, is_mock=False)
            for cid in campaign_ids
        ]
    if not await _gate_open(session, account_id, real_count=len(campaign_ids), now=now):
        return []
    return [
        CabinetView(
            id=cid,
            name=name,
            status=status,
            launched_at=_mock_launched_at(i, now),
            is_mock=True,
        )
        for i, (cid, name, status) in enumerate(_MOCK_CABINETS)
    ]


async def cabinet_stats(
    session: AsyncSession,
    account_id: int,
    cabinet_id: str,
    period: Period,
    *,
    now: datetime | None = None,
) -> StatsView:
    """Метрики кабинета за период: реальные агрегаты или демо (пока гейт открыт)."""
    now = now or datetime.now(UTC)
    since = _period_since(period, now)
    agg = await aggregate_cabinet_stats(session, account_id, cabinet_id, since=since)
    has_real = agg["shows"] > 0 or agg["clicks"] > 0 or agg["spent"] > 0
    if has_real:
        return StatsView(cabinet_id=cabinet_id, period=period, is_mock=False, **agg)

    campaign_ids = await list_stat_campaign_ids(session, account_id)
    if await _gate_open(session, account_id, real_count=len(campaign_ids), now=now):
        return StatsView(
            cabinet_id=cabinet_id, period=period, is_mock=True, **_mock_metrics(cabinet_id, period)
        )
    return StatsView(
        cabinet_id=cabinet_id,
        period=period,
        shows=0.0,
        clicks=0.0,
        spent=0.0,
        results=0.0,
        is_mock=False,
    )
