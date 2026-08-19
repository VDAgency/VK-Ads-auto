"""Кабинет по умолчанию для запуска кампании.

Проверяем три исхода `resolve_default_account`: активных кабинетов нет,
ровно один (используется он) и несколько (оператор не выбрал — явная
ошибка вместо угадывания). VK замокан целиком, тесты не ходят в сеть.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
import services.ad_accounts as ad_accounts
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account
from pydantic import SecretStr
from services.ad_accounts import (
    AmbiguousAdAccountError,
    NoAdAccountError,
    add_account,
    resolve_default_account,
)
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN_1 = "fake-access-token-for-tests-0000000000000001"
TOKEN_2 = "fake-access-token-for-tests-0000000000000002"

IDENTITY_1 = VkIdentity(
    external_id="10000001",
    username="a1b2c3d4e5@agency_client",
    title="Студия «Пример»",
    status="active",
)
IDENTITY_2 = VkIdentity(
    external_id="10000002",
    username="f6g7h8i9j0@agency_client",
    title="Кабинет «Второй»",
    status="active",
)


def _settings(*, key: str | None = None, ttl: int = 15) -> Settings:
    return Settings(
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode() if key is None else key),
        vk_ads_access_token=SecretStr(""),
        ad_account_health_ttl_minutes=ttl,
    )


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
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


@pytest.fixture(autouse=True)
def _mock_vk(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию VK отвечает первой личностью. Тест с двумя кабинетами переключает."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY_1

    async def balance(token: str, **_: object) -> str | None:
        return "100.00"

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


def test_no_active_accounts_raises_no_ad_account_error() -> None:
    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(NoAdAccountError):
            await resolve_default_account(session, 1, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_single_active_account_is_resolved() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        created = await add_account(session, 1, TOKEN_1, settings=cfg)
        await session.commit()

        view, token = await resolve_default_account(session, 1, settings=cfg)

        assert view.id == created.id
        assert view.external_id == created.external_id
        assert token.get_secret_value() == TOKEN_1

    asyncio.run(_with_db(scenario))


def test_two_active_accounts_raise_ambiguous_error() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()

        async def identity_1(token: str, **_: object) -> VkIdentity:
            return IDENTITY_1

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ad_accounts, "fetch_identity", identity_1)
        try:
            await add_account(session, 1, TOKEN_1, settings=cfg)
        finally:
            monkeypatch.undo()

        async def identity_2(token: str, **_: object) -> VkIdentity:
            return IDENTITY_2

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ad_accounts, "fetch_identity", identity_2)
        try:
            await add_account(session, 1, TOKEN_2, settings=cfg)
        finally:
            monkeypatch.undo()

        await session.commit()

        with pytest.raises(AmbiguousAdAccountError):
            await resolve_default_account(session, 1, settings=cfg)

    asyncio.run(_with_db(scenario))
