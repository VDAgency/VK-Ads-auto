"""Тесты сервиса статистики кабинетов (PR-C): моки/реальные, периоды, детерминизм."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from db.base import Base
from db.models import Account, Client, Stat
from services.cabinet_stats import CabinetView, cabinet_stats, list_cabinets
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

# До mock_until (2026-12-31) — гейт по сроку открыт.
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


async def _with_db(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(Account(id=1, name="default"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_mock_cabinets_when_empty_db() -> None:
    async def scenario(session: AsyncSession) -> list[bool]:
        cabinets = await list_cabinets(session, 1, now=NOW)
        assert cabinets, "ожидаем демо-кабинеты при пустой БД"
        return [c.is_mock for c in cabinets]

    result = asyncio.run(_with_db(scenario))
    assert all(result)


def test_real_cabinets_when_stats_exist() -> None:
    async def scenario(session: AsyncSession) -> list[tuple[str, bool]]:
        session.add(Stat(account_id=1, campaign_id="camp-1", shows=100, clicks=5, spent=70))
        await session.commit()
        cabinets = await list_cabinets(session, 1, now=NOW)
        return [(c.id, c.is_mock) for c in cabinets]

    result = asyncio.run(_with_db(scenario))
    assert result == [("camp-1", False)]


def test_gate_closed_by_client_threshold() -> None:
    async def scenario(session: AsyncSession) -> list[CabinetView]:
        for i in range(5):  # порог MOCK_MAX_CLIENTS=5 достигнут
            session.add(Client(account_id=1, full_name=f"c{i}"))
        await session.commit()
        return await list_cabinets(session, 1, now=NOW)

    assert asyncio.run(_with_db(scenario)) == []


def test_real_aggregates_summed() -> None:
    async def scenario(session: AsyncSession) -> tuple[float, float]:
        session.add(Stat(account_id=1, campaign_id="camp-1", shows=100, clicks=10, spent=50))
        session.add(Stat(account_id=1, campaign_id="camp-1", shows=200, clicks=20, spent=90))
        await session.commit()
        view = await cabinet_stats(session, 1, "camp-1", "all", now=NOW)
        return view.shows, view.spent

    shows, spent = asyncio.run(_with_db(scenario))
    assert shows == 300.0
    assert spent == 140.0


def test_ctr_cpc_derived() -> None:
    async def scenario(session: AsyncSession) -> tuple[float, float, bool]:
        session.add(Stat(account_id=1, campaign_id="camp-1", shows=1000, clicks=50, spent=100))
        await session.commit()
        view = await cabinet_stats(session, 1, "camp-1", "all", now=NOW)
        return view.ctr, view.cpc, view.is_mock

    ctr, cpc, is_mock = asyncio.run(_with_db(scenario))
    assert ctr == 5.0  # 50/1000*100
    assert cpc == 2.0  # 100/50
    assert is_mock is False


def test_mock_metrics_deterministic() -> None:
    async def scenario(session: AsyncSession) -> tuple[float, float]:
        a = await cabinet_stats(session, 1, "demo-1", "all", now=NOW)
        b = await cabinet_stats(session, 1, "demo-1", "all", now=NOW)
        return a.shows, b.shows

    first, second = asyncio.run(_with_db(scenario))
    assert first == second
    assert first > 0


def test_mock_metrics_differ_by_period() -> None:
    async def scenario(session: AsyncSession) -> tuple[float, float]:
        month = await cabinet_stats(session, 1, "demo-1", "month", now=NOW)
        week = await cabinet_stats(session, 1, "demo-1", "week", now=NOW)
        return month.shows, week.shows

    month, week = asyncio.run(_with_db(scenario))
    assert month > week > 0  # больше окно — больше показов
