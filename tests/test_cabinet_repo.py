"""Тесты репозиториев Cabinet/Campaign (K-PR2, spec 2026-07-17-kotbot-channel-design §6).

Проверяем reuse-поиск кабинета (точная четвёрка, самый свежий по id), создание
записи кабинета, выборку активных кампаний для синка статистики и смену статуса
кампании — всё со скоупингом по тенанту.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from db.base import Base
from db.models import Account, Brief, Cabinet, Campaign, Client
from db.repositories import (
    create_cabinet_row,
    find_cabinet,
    list_active_campaigns,
    set_campaign_status,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

CLUB_URL = "https://vk.com/club1"


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
        session.add(Account(id=1, name="tenant-one"))
        session.add(Account(id=2, name="tenant-two"))
        session.add(Client(id=100, account_id=1, full_name="Клиент 1"))
        session.add(Client(id=200, account_id=2, full_name="Клиент 2"))
        session.add(Brief(id=500, account_id=1, client_id=100, variant="community"))
        session.add(Brief(id=600, account_id=2, client_id=200, variant="community"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def _campaign(
    campaign_id: int,
    account_id: int = 1,
    status: str = "launched",
    external_id: str | None = "ext-1",
) -> Campaign:
    """Кампания для сценариев: тенант 1 → бриф 500, тенант 2 → бриф 600."""
    return Campaign(
        id=campaign_id,
        account_id=account_id,
        brief_id=500 if account_id == 1 else 600,
        objective="socialengagement",
        status=status,
        external_id=external_id,
    )


# ---------------------------------------------------------------------------
# find_cabinet
# ---------------------------------------------------------------------------


def test_find_cabinet_returns_exact_match() -> None:
    async def scenario(session: AsyncSession) -> tuple[int, int | None]:
        created = await create_cabinet_row(session, 1, 100, "kotbot", CLUB_URL)
        found = await find_cabinet(session, 1, 100, "kotbot", CLUB_URL)
        return created.id, (found.id if found else None)

    created_id, found_id = asyncio.run(_with_db(scenario))
    assert found_id == created_id


def test_find_cabinet_none_for_other_ad_object_url() -> None:
    """Другой объект рекламы — другой кабинет: совпадения нет."""

    async def scenario(session: AsyncSession) -> Cabinet | None:
        await create_cabinet_row(session, 1, 100, "kotbot", CLUB_URL)
        return await find_cabinet(session, 1, 100, "kotbot", "https://vk.com/club999")

    assert asyncio.run(_with_db(scenario)) is None


def test_find_cabinet_returns_freshest_by_id() -> None:
    """При двух кабинетах с одной четвёркой возвращается самый свежий (по id)."""

    async def scenario(session: AsyncSession) -> tuple[int, int | None]:
        await create_cabinet_row(session, 1, 100, "kotbot", CLUB_URL, status="failed")
        second = await create_cabinet_row(session, 1, 100, "kotbot", CLUB_URL)
        found = await find_cabinet(session, 1, 100, "kotbot", CLUB_URL)
        return second.id, (found.id if found else None)

    second_id, found_id = asyncio.run(_with_db(scenario))
    assert found_id == second_id


def test_find_cabinet_scoped_by_tenant() -> None:
    """Кабинет тенанта 1 не находится запросом тенанта 2 (изоляция строк)."""

    async def scenario(session: AsyncSession) -> Cabinet | None:
        await create_cabinet_row(session, 1, 100, "kotbot", CLUB_URL)
        return await find_cabinet(session, 2, 100, "kotbot", CLUB_URL)

    assert asyncio.run(_with_db(scenario)) is None


# ---------------------------------------------------------------------------
# create_cabinet_row
# ---------------------------------------------------------------------------


def test_create_cabinet_row_persists_fields_and_defaults() -> None:
    async def scenario(session: AsyncSession) -> Cabinet:
        return await create_cabinet_row(
            session,
            1,
            100,
            "kotbot",
            CLUB_URL,
            ad_object_name="Клуб №1",
            external_ref="cab-777",
        )

    cabinet = asyncio.run(_with_db(scenario))
    assert cabinet.id is not None
    assert cabinet.account_id == 1
    assert cabinet.client_id == 100
    assert cabinet.channel == "kotbot"
    assert cabinet.ad_object_url == CLUB_URL
    assert cabinet.ad_object_name == "Клуб №1"
    assert cabinet.external_ref == "cab-777"
    assert cabinet.status == "created"


def test_create_cabinet_row_accepts_status_override() -> None:
    """Кабинет можно завести в статусе creating (persist ДО похода на площадку)."""

    async def scenario(session: AsyncSession) -> Cabinet:
        return await create_cabinet_row(session, 1, 100, "kotbot", CLUB_URL, status="creating")

    cabinet = asyncio.run(_with_db(scenario))
    assert cabinet.status == "creating"
    assert cabinet.external_ref is None


# ---------------------------------------------------------------------------
# list_active_campaigns
# ---------------------------------------------------------------------------


def test_list_active_campaigns_filters_status_and_external_id() -> None:
    """Активные = launched|moderation с external_id; остальное отфильтровано."""

    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, status="launched"))
        session.add(_campaign(2, status="moderation"))
        session.add(_campaign(3, status="prepared"))
        session.add(_campaign(4, status="stopped"))
        session.add(_campaign(5, status="failed"))
        session.add(_campaign(6, status="launched", external_id=None))
        await session.flush()
        return [campaign.id for campaign in await list_active_campaigns(session, 1)]

    assert asyncio.run(_with_db(scenario)) == [1, 2]


def test_list_active_campaigns_sorted_by_id() -> None:
    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(9, status="moderation"))
        session.add(_campaign(3, status="launched"))
        await session.flush()
        return [campaign.id for campaign in await list_active_campaigns(session, 1)]

    assert asyncio.run(_with_db(scenario)) == [3, 9]


def test_list_active_campaigns_excludes_other_tenant() -> None:
    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, account_id=1, status="launched"))
        session.add(_campaign(2, account_id=2, status="launched"))
        await session.flush()
        return [campaign.id for campaign in await list_active_campaigns(session, 1)]

    assert asyncio.run(_with_db(scenario)) == [1]


# ---------------------------------------------------------------------------
# set_campaign_status
# ---------------------------------------------------------------------------


def test_set_campaign_status_updates_status() -> None:
    async def scenario(session: AsyncSession) -> tuple[str | None, datetime | None]:
        session.add(_campaign(1, status="moderation"))
        await session.flush()
        updated = await set_campaign_status(session, 1, 1, "launched")
        return (updated.status if updated else None), (updated.launched_at if updated else None)

    status, launched_at = asyncio.run(_with_db(scenario))
    assert status == "launched"
    assert launched_at is None  # launched_at не передан — не трогаем


def test_set_campaign_status_sets_launched_at_when_given() -> None:
    async def scenario(session: AsyncSession) -> datetime | None:
        session.add(_campaign(1, status="prepared"))
        await session.flush()
        moment = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
        updated = await set_campaign_status(session, 1, 1, "launched", launched_at=moment)
        return updated.launched_at if updated else None

    launched_at = asyncio.run(_with_db(scenario))
    assert launched_at is not None
    assert launched_at.replace(tzinfo=UTC) == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def test_set_campaign_status_returns_none_for_other_tenant() -> None:
    """Чужая кампания не обновляется и не возвращается (изоляция тенанта)."""

    async def scenario(session: AsyncSession) -> tuple[Campaign | None, str]:
        session.add(_campaign(1, account_id=2, status="launched"))
        await session.flush()
        result = await set_campaign_status(session, 1, 1, "stopped")
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return result, campaign.status

    result, status = asyncio.run(_with_db(scenario))
    assert result is None
    assert status == "launched"  # статус чужой кампании не изменился


def test_set_campaign_status_returns_none_for_missing_campaign() -> None:
    async def scenario(session: AsyncSession) -> Campaign | None:
        return await set_campaign_status(session, 1, 12345, "stopped")

    assert asyncio.run(_with_db(scenario)) is None
