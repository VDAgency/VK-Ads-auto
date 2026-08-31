"""Тесты пароля веб-админки оператора: поля модели + сервис `services/operator_auth`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from db.base import Base
from db.models import Account, Operator
from services.operator_auth import (
    WeakPasswordError,
    authenticate_operator,
    set_operator_password,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


async def _run(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
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


def test_operator_model_has_password_fields() -> None:
    """Модель несёт пустые по умолчанию `password_hash`/`password_set_at`."""

    async def scenario(session: AsyncSession) -> Operator:
        op = Operator(account_id=1, telegram_id=42)
        session.add(op)
        await session.commit()
        return op

    operator = asyncio.run(_run(scenario))
    assert operator.password_hash is None
    assert operator.password_set_at is None


def test_set_password_then_authenticate_succeeds() -> None:
    async def scenario(session: AsyncSession) -> bool:
        await set_operator_password(session, 1, 555, "Достаточно_длинный_пароль")
        await session.commit()
        return await authenticate_operator(session, 1, 555, "Достаточно_длинный_пароль")

    assert asyncio.run(_run(scenario)) is True


def test_set_password_materializes_operator_row() -> None:
    """`set_operator_password` создаёт строку оператора лениво (как бот)."""

    async def scenario(session: AsyncSession) -> Operator | None:
        await set_operator_password(session, 1, 777, "ещё_один_длинный_пароль")
        await session.commit()
        from sqlalchemy import select

        stmt = select(Operator).where(Operator.telegram_id == 777)
        return (await session.execute(stmt)).scalar_one_or_none()

    operator = asyncio.run(_run(scenario))
    assert operator is not None
    assert operator.password_hash is not None
    assert operator.password_set_at is not None


def test_set_password_too_short_raises() -> None:
    async def scenario(session: AsyncSession) -> None:
        await set_operator_password(session, 1, 555, "short")

    with pytest.raises(WeakPasswordError):
        asyncio.run(_run(scenario))


def test_authenticate_unknown_operator_returns_false() -> None:
    async def scenario(session: AsyncSession) -> bool:
        return await authenticate_operator(session, 1, 999999, "любой_пароль_длинный")

    assert asyncio.run(_run(scenario)) is False


def test_authenticate_operator_without_password_returns_false() -> None:
    async def scenario(session: AsyncSession) -> bool:
        session.add(Operator(account_id=1, telegram_id=321))
        await session.commit()
        return await authenticate_operator(session, 1, 321, "любой_пароль_длинный")

    assert asyncio.run(_run(scenario)) is False


def test_authenticate_wrong_password_returns_false() -> None:
    async def scenario(session: AsyncSession) -> bool:
        await set_operator_password(session, 1, 555, "правильный_длинный_пароль")
        await session.commit()
        return await authenticate_operator(session, 1, 555, "неправильный_пароль")

    assert asyncio.run(_run(scenario)) is False
