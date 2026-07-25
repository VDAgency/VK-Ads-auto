"""Тесты синхронизации статистики кампаний (`services/stats_sync`, spec §9).

Покрывают: сохранение среза `Stat` по активным кампаниям, обновление статуса по
ответу площадки (moderation→launched, →stopped), выбор адаптера по каналу
кабинета, изоляцию ошибок (одна кампания не роняет остальные) и скоуп тенанта.
Живой VK API не дёргается — адаптеры подменяются в тестах.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from config.settings import Settings
from db.base import Base
from db.models import Account, Brief, Cabinet, Campaign, Client, Stat
from integrations.adapter import PlatformAdapter
from services.stats_sync import sync_campaign_stats
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


class _FakeAdapter(PlatformAdapter):
    """Площадка в тестах: отдаёт заданные метрики/статус, копит вызовы."""

    def __init__(
        self,
        *,
        status: str = "active",
        stats: dict[str, float] | None = None,
        broken: bool = False,
    ) -> None:
        self._status = status
        self._stats = stats if stats is not None else {"shows": 100.0, "clicks": 5.0}
        self._broken = broken
        self.stats_calls: list[str] = []
        self.status_calls: list[str] = []

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return "camp"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return "creative"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        self.stats_calls.append(campaign_id)
        if self._broken:
            raise RuntimeError("platform is down")
        return dict(self._stats)

    async def get_status(self, campaign_id: str) -> str:
        self.status_calls.append(campaign_id)
        return self._status


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _with_db(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Пустая БД с тенантом, клиентом, брифом и двумя кабинетами разных каналов."""
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
        session.add(Account(id=2, name="other"))
        session.add(Client(id=1, account_id=1, full_name="Вячеслав"))
        session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
        session.add(
            Cabinet(
                id=1,
                account_id=1,
                client_id=1,
                channel="vk_api",
                ad_object_url="https://vk.com/id1",
            )
        )
        session.add(
            Cabinet(
                id=2,
                account_id=1,
                client_id=1,
                channel="kotbot",
                ad_object_url="https://vk.com/club1",
            )
        )
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def _campaign(
    campaign_id: int,
    *,
    account_id: int = 1,
    status: str = "launched",
    external_id: str | None = "ext-1",
    cabinet_id: int | None = 1,
) -> Campaign:
    return Campaign(
        id=campaign_id,
        account_id=account_id,
        brief_id=1,
        cabinet_id=cabinet_id,
        status=status,
        objective="socialengagement",
        external_id=external_id,
    )


# --- сохранение среза метрик ------------------------------------------------------


def test_sync_saves_stat_row_for_active_campaign() -> None:
    adapter = _FakeAdapter(stats={"shows": 100.0, "clicks": 5.0, "spent": 250.0, "goals": 10.0})

    async def scenario(session: AsyncSession) -> tuple[dict[int, str], list[Stat]]:
        session.add(_campaign(1))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )
        await session.commit()
        stats = list((await session.execute(select(Stat))).scalars().all())
        return summary, stats

    summary, stats = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok"}
    assert len(stats) == 1
    assert stats[0].campaign_id == "ext-1"
    assert stats[0].shows == 100.0
    assert stats[0].results == 10.0
    assert adapter.stats_calls == ["ext-1"]


def test_empty_platform_stats_are_stored_as_zeroes() -> None:
    # Площадка ещё не отдала метрики — срез нулевой, но синк не считается ошибкой.
    adapter = _FakeAdapter(stats={})

    async def scenario(session: AsyncSession) -> tuple[dict[int, str], float]:
        session.add(_campaign(1))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )
        await session.commit()
        stat = (await session.execute(select(Stat))).scalars().one()
        return summary, stat.shows

    summary, shows = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok"}
    assert shows == 0.0


# --- обновление статуса по площадке -----------------------------------------------


def test_moderation_becomes_launched_when_platform_is_active() -> None:
    adapter = _FakeAdapter(status="active")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1, status="moderation"))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "launched"


def test_blocked_on_platform_becomes_stopped() -> None:
    adapter = _FakeAdapter(status="blocked")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "stopped"


def test_launched_becomes_moderation_when_platform_moderates() -> None:
    adapter = _FakeAdapter(status="moderation")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "moderation"


def test_unknown_platform_status_keeps_current_status() -> None:
    # Канал статус не сообщает (`unknown` по умолчанию контракта адаптера) — не трогаем.
    adapter = _FakeAdapter(status="unknown")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1, status="moderation"))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "moderation"


# --- выбор канала и скоуп ----------------------------------------------------------


def test_adapter_is_chosen_by_cabinet_channel() -> None:
    vk = _FakeAdapter()
    kotbot = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        session.add(_campaign(2, external_id="kot-1", cabinet_id=2))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": vk, "kotbot": kotbot}
        )
        await session.commit()
        return summary

    summary = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok", 2: "ok"}
    assert vk.stats_calls == ["vk-1"]
    assert kotbot.stats_calls == ["kot-1"]


def test_prepared_campaign_is_not_synced() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, status="prepared"))
        await session.commit()
        return await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )

    assert asyncio.run(_with_db(scenario)) == {}
    assert adapter.stats_calls == []


def test_other_tenant_campaigns_are_not_synced() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        # Кампания чужого тенанта: кабинет не указываем — FK кабинета за тенантом.
        session.add(_campaign(1, account_id=2, cabinet_id=None))
        await session.commit()
        return await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )

    assert asyncio.run(_with_db(scenario)) == {}
    assert adapter.stats_calls == []


# --- изоляция ошибок ---------------------------------------------------------------


def test_error_on_one_campaign_does_not_break_the_rest() -> None:
    broken = _FakeAdapter(broken=True)
    healthy = _FakeAdapter()

    async def scenario(session: AsyncSession) -> tuple[dict[int, str], int]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        session.add(_campaign(2, external_id="kot-1", cabinet_id=2))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": broken, "kotbot": healthy}
        )
        await session.commit()
        stats = list((await session.execute(select(Stat))).scalars().all())
        return summary, len(stats)

    summary, saved = asyncio.run(_with_db(scenario))
    assert summary == {1: "error", 2: "ok"}
    assert saved == 1
