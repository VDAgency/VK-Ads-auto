"""Тесты сервиса статистики кабинетов (PR-C): моки/реальные, периоды, детерминизм.

Задача 2 (дефекты 2 и 4): мок-гейт закрыт по умолчанию (нужен явный
`MOCK_STATS_ENABLED=true`), агрегат по кабинету не задваивает историю срезов.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

import pytest
import services.cabinet_stats as cabinet_stats_module
from config.settings import Settings
from db.base import Base
from db.models import Account, Client, Stat
from services.cabinet_stats import CabinetView, cabinet_stats, list_cabinets
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

# До mock_until (2026-12-31) — гейт по сроку открыт.
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _settings(*, mock_stats_enabled: bool = False, **over: Any) -> Settings:
    """Настройки для теста; по умолчанию демо-гейт выключен (дефект 2 — закрыт по умолчанию)."""
    base: dict[str, Any] = {"_env_file": None, "mock_stats_enabled": mock_stats_enabled}
    base.update(over)
    return Settings(**base)


def _enable_mock_gate(monkeypatch: pytest.MonkeyPatch, **over: Any) -> None:
    """Подменить настройки сервиса так, чтобы демо-гейт мог открыться (явный флаг включён)."""
    settings = _settings(mock_stats_enabled=True, **over)
    monkeypatch.setattr(cabinet_stats_module, "get_settings", lambda: settings)


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


# --- дефект 2: демо-гейт закрыт по умолчанию, показывается только по явному флагу --


def test_no_mock_cabinets_by_default_when_empty_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без `MOCK_STATS_ENABLED` демо не показываются, даже если БД совсем пустая."""
    monkeypatch.setattr(cabinet_stats_module, "get_settings", lambda: _settings())

    async def scenario(session: AsyncSession) -> list[CabinetView]:
        return await list_cabinets(session, 1, now=NOW)

    assert asyncio.run(_with_db(scenario)) == []


def test_mock_cabinets_when_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """С явным `MOCK_STATS_ENABLED=true` и пустой БД демо-кабинеты показываются."""
    _enable_mock_gate(monkeypatch)

    async def scenario(session: AsyncSession) -> list[bool]:
        cabinets = await list_cabinets(session, 1, now=NOW)
        assert cabinets, "ожидаем демо-кабинеты при включённом флаге и пустой БД"
        return [c.is_mock for c in cabinets]

    result = asyncio.run(_with_db(scenario))
    assert all(result)


def test_no_mock_stats_by_default_when_no_real_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без флага и без реальных данных — честное «нулевое» и НЕ демо (не выдумка)."""
    monkeypatch.setattr(cabinet_stats_module, "get_settings", lambda: _settings())

    async def scenario(session: AsyncSession) -> tuple[float, bool]:
        view = await cabinet_stats(session, 1, "demo-1", "all", now=NOW)
        return view.shows, view.is_mock

    shows, is_mock = asyncio.run(_with_db(scenario))
    assert shows == 0.0
    assert is_mock is False


def test_real_cabinets_when_stats_exist() -> None:
    async def scenario(session: AsyncSession) -> list[tuple[str, bool]]:
        session.add(Stat(account_id=1, campaign_id="camp-1", shows=100, clicks=5, spent=70))
        await session.commit()
        cabinets = await list_cabinets(session, 1, now=NOW)
        return [(c.id, c.is_mock) for c in cabinets]

    result = asyncio.run(_with_db(scenario))
    assert result == [("camp-1", False)]


def test_gate_closed_by_client_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """При включённом флаге остальные условия §7 по-прежнему действуют (порог клиентов)."""
    _enable_mock_gate(monkeypatch)

    async def scenario(session: AsyncSession) -> list[CabinetView]:
        for i in range(5):  # порог MOCK_MAX_CLIENTS=5 достигнут
            session.add(Client(account_id=1, full_name=f"c{i}"))
        await session.commit()
        return await list_cabinets(session, 1, now=NOW)

    assert asyncio.run(_with_db(scenario)) == []


# --- дефект 4: агрегат берёт последний срез, а не сумму всей истории ---------------


def test_real_aggregates_use_latest_snapshot_only() -> None:
    """VK отдаёт накопительный итог — второй срез уже включает первый, суммировать нельзя."""

    async def scenario(session: AsyncSession) -> tuple[float, float]:
        session.add(
            Stat(
                account_id=1,
                campaign_id="camp-1",
                shows=100,
                clicks=10,
                spent=50,
                captured_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            )
        )
        session.add(
            Stat(
                account_id=1,
                campaign_id="camp-1",
                shows=200,
                clicks=20,
                spent=90,
                captured_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            )
        )
        await session.commit()
        view = await cabinet_stats(session, 1, "camp-1", "all", now=NOW)
        return view.shows, view.spent

    shows, spent = asyncio.run(_with_db(scenario))
    # Учтён только последний (10:00) срез, а не сумма двух (100+200, 50+90).
    assert shows == 200.0
    assert spent == 90.0


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


# --- дефект 3: CPL в StatsView -----------------------------------------------------


def test_cpl_derived() -> None:
    async def scenario(session: AsyncSession) -> float:
        session.add(
            Stat(account_id=1, campaign_id="camp-1", shows=1000, clicks=50, spent=250, results=10)
        )
        await session.commit()
        view = await cabinet_stats(session, 1, "camp-1", "all", now=NOW)
        return view.cpl

    assert asyncio.run(_with_db(scenario)) == 25.0  # 250/10


def test_cpl_zero_safe() -> None:
    async def scenario(session: AsyncSession) -> float:
        session.add(
            Stat(account_id=1, campaign_id="camp-1", shows=1000, clicks=50, spent=250, results=0)
        )
        await session.commit()
        view = await cabinet_stats(session, 1, "camp-1", "all", now=NOW)
        return view.cpl

    assert asyncio.run(_with_db(scenario)) == 0.0


def test_mock_metrics_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_mock_gate(monkeypatch)

    async def scenario(session: AsyncSession) -> tuple[float, float]:
        a = await cabinet_stats(session, 1, "demo-1", "all", now=NOW)
        b = await cabinet_stats(session, 1, "demo-1", "all", now=NOW)
        return a.shows, b.shows

    first, second = asyncio.run(_with_db(scenario))
    assert first == second
    assert first > 0


def test_mock_metrics_differ_by_period(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_mock_gate(monkeypatch)

    async def scenario(session: AsyncSession) -> tuple[float, float]:
        month = await cabinet_stats(session, 1, "demo-1", "month", now=NOW)
        week = await cabinet_stats(session, 1, "demo-1", "week", now=NOW)
        return month.shows, week.shows

    month, week = asyncio.run(_with_db(scenario))
    assert month > week > 0  # больше окно — больше показов
