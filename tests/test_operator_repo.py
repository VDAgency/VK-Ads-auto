"""Тесты репозитория операторов: get_or_create_operator (idempotent по telegram_id)."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from db.base import Base
from db.models import Account
from db.repositories import get_or_create_operator
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
        session.add(Account(id=1, name="default"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_creates_operator_when_absent() -> None:
    async def scenario(session: AsyncSession) -> None:
        op = await get_or_create_operator(session, account_id=1, telegram_id=555)
        assert op.id >= 1
        assert op.telegram_id == 555
        assert op.account_id == 1

    asyncio.run(_with_db(scenario))


def test_returns_existing_operator_same_id() -> None:
    async def scenario(session: AsyncSession) -> None:
        first = await get_or_create_operator(session, account_id=1, telegram_id=555)
        second = await get_or_create_operator(session, account_id=1, telegram_id=555)
        assert first.id == second.id

    asyncio.run(_with_db(scenario))
