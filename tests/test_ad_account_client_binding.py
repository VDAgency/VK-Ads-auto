"""Привязка рекламного кабинета к клиенту (T1, spec 2026-08-25 §1.1).

Фундамент для Т2 (сверка при запуске): доказывает две вещи —
пустая привязка остаётся общей (обратная совместимость с продом, где сегодня
один кабинет без привязки и девять клиентов), а заполненная скрывает кабинет
от чужих клиентов. Проверяем на уровне сервиса (`services.ad_accounts`), тем
API, которым будут пользоваться Т2/Т3.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Client
from pydantic import SecretStr
from services.ad_accounts import (
    ClientNotFoundError,
    add_account,
    list_accounts_for_client,
    set_account_client,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"


def _settings() -> Settings:
    return Settings(
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode()),
        vk_ads_access_token=SecretStr(""),
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
        session.add(Client(id=7, account_id=1, full_name="Клиент Дибровой"))
        session.add(Client(id=42, account_id=1, full_name="Клиент Долматова"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


@pytest.fixture(autouse=True)
def _mock_vk(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.ad_accounts as ad_accounts
    from services.vk_identity import VkIdentity

    counter = {"n": 0}

    async def identity(token: str, **_: object) -> VkIdentity:
        counter["n"] += 1
        return VkIdentity(
            external_id=f"{111 + counter['n']}",
            username="a1b2c3d4e5@agency_client",
            title="Студия «Пример»",
            status="active",
        )

    async def balance(token: str, **_: object) -> str | None:
        return "12345.67"

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


def test_account_without_client_suits_any_brief() -> None:
    """Кабинет без привязки — общий: подходит любому клиенту, как было до сих пор."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        account = await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()
        assert account.client_id is None
        assert account.client_name is None

        usable = await list_accounts_for_client(session, 1, 42, settings=cfg)
        assert [a.external_id for a in usable] == [account.external_id]

    asyncio.run(_with_db(scenario))


def test_account_bound_to_client_is_hidden_from_other_clients() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        account = await add_account(session, 1, TOKEN, client_id=7, settings=cfg)
        await session.commit()
        assert account.client_id == 7
        assert account.client_name == "Клиент Дибровой"

        other = await list_accounts_for_client(session, 1, 42, settings=cfg)
        assert [a.external_id for a in other] == []

        own = await list_accounts_for_client(session, 1, 7, settings=cfg)
        assert [a.external_id for a in own] == [account.external_id]

    asyncio.run(_with_db(scenario))


def test_legacy_accounts_without_binding_stay_usable_for_every_client() -> None:
    """Гарантия обратной совместимости (шаг 7): прод не встанет после миграции.

    Сегодня в базе один такой кабинет и девять клиентов — колонка nullable без
    `server_default`, существующая строка приходит с `client_id=NULL` и обязана
    оставаться видимой всем.
    """

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        account = await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()

        for client_id in (7, 42):
            usable = await list_accounts_for_client(session, 1, client_id, settings=cfg)
            assert [a.external_id for a in usable] == [account.external_id]

    asyncio.run(_with_db(scenario))


def test_list_for_client_is_scoped_to_tenant() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        session.add(Account(id=2, name="tenant-two"))
        session.add(Client(id=8, account_id=2, full_name="Другой тенант"))
        await session.commit()
        await add_account(session, 1, TOKEN, client_id=7, settings=cfg)
        await session.commit()
        # Кабинет тенанта 1 не должен быть виден по id тенанта 2, даже без привязки.
        assert await list_accounts_for_client(session, 2, 8, settings=cfg) == []

    asyncio.run(_with_db(scenario))


def test_add_account_rejects_unknown_client() -> None:
    """Привязать кабинет к несуществующему клиенту нельзя — это опечатка оператора."""

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(ClientNotFoundError):
            await add_account(session, 1, TOKEN, client_id=999, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_set_account_client_changes_binding() -> None:
    """Привязку существующего кабинета можно менять после добавления."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        account = await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()

        bound = await set_account_client(session, 1, account.id, 42, settings=cfg)
        assert bound.client_id == 42
        assert bound.client_name == "Клиент Долматова"

        freed = await set_account_client(session, 1, account.id, None, settings=cfg)
        assert freed.client_id is None
        assert freed.client_name is None

    asyncio.run(_with_db(scenario))


def test_client_name_is_blank_when_client_full_name_is_blank() -> None:
    """Пустое имя клиента остаётся пустым — заглушку придумывает интерфейс, не сервис."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        session.add(Client(id=99, account_id=1, full_name=None))
        await session.commit()
        account = await add_account(session, 1, TOKEN, client_id=99, settings=cfg)
        assert account.client_name is None

    asyncio.run(_with_db(scenario))
