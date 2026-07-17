"""Тесты модели Cabinet (K-PR2, spec 2026-07-17-kotbot-channel-design §6).

Проверяем схему таблицы (поля, дефолты, reuse-индекс), связь Campaign.cabinet_id
и мульти-тенант-инвариант: кабинет одного тенанта не виден запросам другого.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from db.base import Base
from db.models import Account, Cabinet, Campaign, Client
from sqlalchemy import select
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
        session.add(Client(id=200, account_id=2, full_name="Клиент 2"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_cabinet_has_account_id_not_nullable() -> None:
    """Мульти-тенант-инвариант (CLAUDE.md §1.3): account_id обязателен."""
    assert "account_id" in Cabinet.__table__.columns
    assert Cabinet.__table__.columns["account_id"].nullable is False


def test_cabinet_schema_columns() -> None:
    """Все поля из спеки §6 на месте, nullable-семантика по спеке."""
    cols = Cabinet.__table__.columns
    assert cols["client_id"].nullable is False
    assert cols["channel"].nullable is False
    assert cols["external_ref"].nullable is True
    assert cols["ad_object_url"].nullable is False
    assert cols["ad_object_name"].nullable is True
    assert cols["status"].nullable is False
    assert "created_at" in cols


def test_cabinet_status_defaults_to_created() -> None:
    """Строка без явного статуса получает status='created'."""

    async def scenario(session: AsyncSession) -> str:
        cabinet = Cabinet(
            account_id=1,
            client_id=100,
            channel="stub",
            ad_object_url="https://vk.com/club1",
        )
        session.add(cabinet)
        await session.flush()
        return cabinet.status

    assert asyncio.run(_with_db(scenario)) == "created"


def test_cabinet_reuse_index_is_not_unique() -> None:
    """Индекс ix_cabinet_reuse по четвёрке существует и НЕ уникален."""
    # Через metadata: у Table есть .indexes (у FromClause в типах mypy — нет).
    reuse = next(
        index
        for index in Base.metadata.tables["cabinet"].indexes
        if index.name == "ix_cabinet_reuse"
    )
    assert reuse.unique is False
    assert [col.name for col in reuse.columns] == [
        "account_id",
        "client_id",
        "channel",
        "ad_object_url",
    ]


def test_campaign_has_nullable_cabinet_id() -> None:
    """Campaign.cabinet_id — FK на cabinet.id, NULL у строк до миграции 0009."""
    col = Campaign.__table__.columns["cabinet_id"]
    assert col.nullable is True
    assert [fk.target_fullname for fk in col.foreign_keys] == ["cabinet.id"]


def test_cabinet_not_visible_to_other_tenant() -> None:
    """Кабинет account_id=1 не виден запросам, скоупленным по account_id=2."""

    async def scenario(session: AsyncSession) -> tuple[int, int]:
        session.add(
            Cabinet(
                account_id=1,
                client_id=100,
                channel="kotbot",
                ad_object_url="https://vk.com/club1",
            )
        )
        await session.flush()
        own = (
            (await session.execute(select(Cabinet).where(Cabinet.account_id == 1))).scalars().all()
        )
        foreign = (
            (await session.execute(select(Cabinet).where(Cabinet.account_id == 2))).scalars().all()
        )
        return len(list(own)), len(list(foreign))

    own_count, foreign_count = asyncio.run(_with_db(scenario))
    assert own_count == 1
    assert foreign_count == 0
