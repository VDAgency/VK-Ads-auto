"""Тесты C2: хеширование пароля (pbkdf2) и уникальность email в рамках тенанта."""

from __future__ import annotations

import asyncio

import pytest
from db.base import Base
from db.models import Account, Client
from services.password import hash_password, verify_password
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_hash_and_verify_roundtrip() -> None:
    hashed = hash_password("Секрет123!")
    assert verify_password("Секрет123!", hashed)
    assert not verify_password("wrong", hashed)


def test_hash_is_salted_and_unique() -> None:
    # Одинаковый пароль → разные хеши (соль случайна), но оба проверяются.
    a = hash_password("одинаковый")
    b = hash_password("одинаковый")
    assert a != b
    assert verify_password("одинаковый", a)
    assert verify_password("одинаковый", b)


def test_hash_format() -> None:
    hashed = hash_password("p")
    assert hashed.startswith("pbkdf2_sha256$")
    assert len(hashed.split("$")) == 4


def test_verify_malformed_returns_false() -> None:
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "")


def test_client_email_unique_per_account() -> None:
    async def scenario() -> bool:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        raised = False
        async with maker() as session:
            session.add(Account(id=1, name="default"))
            session.add(Client(account_id=1, email="dup@example.com"))
            session.add(Client(account_id=1, email="dup@example.com"))
            try:
                await session.commit()
            except IntegrityError:
                raised = True
        await engine.dispose()
        return raised

    assert asyncio.run(scenario()) is True


def test_client_null_email_not_conflicting() -> None:
    async def scenario() -> int:
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
            # Несколько клиентов без email допустимы (NULL ≠ NULL).
            session.add(Client(account_id=1, telegram="@a"))
            session.add(Client(account_id=1, telegram="@b"))
            await session.commit()
            from sqlalchemy import func, select

            count = (await session.execute(select(func.count()).select_from(Client))).scalar_one()
        await engine.dispose()
        return int(count)

    assert asyncio.run(scenario()) == 2


@pytest.mark.parametrize("password", ["", "a", "很长的密码"])
def test_roundtrip_various(password: str) -> None:
    assert verify_password(password, hash_password(password))
