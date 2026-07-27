"""Репозитории рекламных кабинетов (spec 2026-07-27 §4).

Проверяем скоуп по тенанту, частичную уникальность активного кабинета,
мягкое удаление с затиранием секретов и связку кампании с кабинетом.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from db.base import Base
from db.models import Account, AdAccount, Brief, Campaign, Client
from db.repositories import (
    archive_ad_account,
    count_active_ad_accounts,
    create_ad_account,
    find_active_ad_account_by_external_id,
    get_ad_account,
    list_ad_accounts,
    rename_ad_account,
    set_ad_account_health,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


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
        session.add(Brief(id=500, account_id=1, client_id=100, variant="community"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


async def _add(session: AsyncSession, account_id: int = 1, external_id: str = "10000001") -> int:
    row = await create_ad_account(
        session,
        account_id,
        title="Студия «Пример»",
        external_id=external_id,
        username="a1b2c3d4e5@agency_client",
        token_encrypted="cipher",
        refresh_encrypted="refresh-cipher",
        token_tail="4iA6",
        advertiser_kind="owner",
    )
    await session.commit()
    return row.id


def test_create_and_list() -> None:
    async def scenario(session: AsyncSession) -> None:
        await _add(session)
        rows = await list_ad_accounts(session, 1)
        assert len(rows) == 1
        assert rows[0].title == "Студия «Пример»"
        assert rows[0].external_id == "10000001"
        assert rows[0].status == "active"
        assert rows[0].health == "healthy"
        assert rows[0].health_checked_at is not None

    asyncio.run(_with_db(scenario))


def test_list_is_scoped_to_tenant() -> None:
    """Изоляция строк по тенанту — инвариант CLAUDE.md §1.3."""

    async def scenario(session: AsyncSession) -> None:
        await _add(session, account_id=1, external_id="111")
        await _add(session, account_id=2, external_id="222")
        assert [r.external_id for r in await list_ad_accounts(session, 1)] == ["111"]
        assert [r.external_id for r in await list_ad_accounts(session, 2)] == ["222"]

    asyncio.run(_with_db(scenario))


def test_get_from_other_tenant_returns_none() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session, account_id=1)
        assert await get_ad_account(session, 2, row_id) is None

    asyncio.run(_with_db(scenario))


def test_same_external_id_twice_is_rejected() -> None:
    """Один активный кабинет на один VK-id — частичный уникальный индекс."""

    async def scenario(session: AsyncSession) -> None:
        await _add(session, external_id="10000001")
        with pytest.raises(IntegrityError):
            await _add(session, external_id="10000001")

    asyncio.run(_with_db(scenario))


def test_same_external_id_allowed_in_another_tenant() -> None:
    """Уникальность действует внутри тенанта, а не глобально."""

    async def scenario(session: AsyncSession) -> None:
        await _add(session, account_id=1, external_id="10000001")
        await _add(session, account_id=2, external_id="10000001")
        assert await count_active_ad_accounts(session, 1) == 1
        assert await count_active_ad_accounts(session, 2) == 1

    asyncio.run(_with_db(scenario))


def test_archived_account_frees_external_id() -> None:
    """После удаления тот же кабинет можно завести заново — индекс частичный."""

    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session, external_id="10000001")
        await archive_ad_account(session, 1, row_id)
        await session.commit()
        await _add(session, external_id="10000001")
        assert await count_active_ad_accounts(session, 1) == 1

    asyncio.run(_with_db(scenario))


def test_archive_wipes_secrets() -> None:
    """Удаление обязано стереть оба секрета безвозвратно (spec §6)."""

    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        archived = await archive_ad_account(session, 1, row_id)
        assert archived is not None
        assert archived.status == "archived"
        assert archived.token_encrypted is None
        assert archived.refresh_encrypted is None
        assert archived.archived_at is not None

    asyncio.run(_with_db(scenario))


def test_archive_is_idempotent() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        first = await archive_ad_account(session, 1, row_id)
        assert first is not None
        stamp = first.archived_at
        second = await archive_ad_account(session, 1, row_id)
        assert second is not None
        assert second.archived_at == stamp

    asyncio.run(_with_db(scenario))


def test_archive_other_tenant_returns_none() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session, account_id=1)
        assert await archive_ad_account(session, 2, row_id) is None
        row = await get_ad_account(session, 1, row_id)
        assert row is not None
        assert row.status == "active"

    asyncio.run(_with_db(scenario))


def test_archived_hidden_from_list_but_readable_by_id() -> None:
    """Старые кампании должны уметь достать свой кабинет даже после удаления."""

    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        await archive_ad_account(session, 1, row_id)
        assert await list_ad_accounts(session, 1) == []
        assert len(await list_ad_accounts(session, 1, include_archived=True)) == 1
        assert await get_ad_account(session, 1, row_id) is not None

    asyncio.run(_with_db(scenario))


def test_find_active_by_external_id_ignores_archived() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session, external_id="10000001")
        assert await find_active_ad_account_by_external_id(session, 1, "10000001") is not None
        await archive_ad_account(session, 1, row_id)
        assert await find_active_ad_account_by_external_id(session, 1, "10000001") is None

    asyncio.run(_with_db(scenario))


def test_set_health_records_state_and_error() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        updated = await set_ad_account_health(
            session, 1, row_id, "unauthorized", error="token revoked"
        )
        assert updated is not None
        assert updated.health == "unauthorized"
        assert updated.health_error == "token revoked"

    asyncio.run(_with_db(scenario))


def test_set_health_clears_previous_error_on_recovery() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        await set_ad_account_health(session, 1, row_id, "error", error="timeout")
        recovered = await set_ad_account_health(
            session, 1, row_id, "healthy", balance_rub="12345.67"
        )
        assert recovered is not None
        assert recovered.health == "healthy"
        assert recovered.health_error is None
        assert recovered.balance_rub == "12345.67"

    asyncio.run(_with_db(scenario))


def test_set_health_other_tenant_returns_none() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session, account_id=1)
        assert await set_ad_account_health(session, 2, row_id, "healthy") is None

    asyncio.run(_with_db(scenario))


def test_rename_keeps_other_fields() -> None:
    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        renamed = await rename_ad_account(session, 1, row_id, "Косметология (основной)")
        assert renamed is not None
        assert renamed.title == "Косметология (основной)"
        assert renamed.external_id == "10000001"

    asyncio.run(_with_db(scenario))


def test_campaign_links_to_ad_account() -> None:
    """Кампания помнит кабинет — иначе её нечем остановить (spec §4.2)."""

    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        campaign = Campaign(
            account_id=1,
            brief_id=500,
            client_id=100,
            ad_account_id=row_id,
            objective="socialengagement",
            external_id="70000007",
        )
        session.add(campaign)
        await session.commit()
        stored = await session.get(Campaign, campaign.id)
        assert stored is not None
        assert stored.ad_account_id == row_id

    asyncio.run(_with_db(scenario))


def test_count_active_ignores_archived() -> None:
    async def scenario(session: AsyncSession) -> None:
        first = await _add(session, external_id="111")
        await _add(session, external_id="222")
        assert await count_active_ad_accounts(session, 1) == 2
        await archive_ad_account(session, 1, first)
        assert await count_active_ad_accounts(session, 1) == 1

    asyncio.run(_with_db(scenario))


def test_secrets_are_never_plaintext_in_model() -> None:
    """Модель хранит шифротекст: сырому токену в колонке взяться неоткуда."""

    async def scenario(session: AsyncSession) -> None:
        row_id = await _add(session)
        row = await get_ad_account(session, 1, row_id)
        assert row is not None
        assert isinstance(row, AdAccount)
        assert row.token_encrypted == "cipher"
        assert row.token_tail == "4iA6"

    asyncio.run(_with_db(scenario))
