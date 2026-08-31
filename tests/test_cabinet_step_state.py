"""Перенос предусловий шага C1 «завести клиенту кабинет автоматически» из бота
в ядро (`services.agency_cabinets.cabinet_step_state`).

Раньше `bot/handlers/creative.py` читал `get_settings().vk_agency_confirmed`
напрямую (канал не должен читать настройки ядра, CLAUDE.md §1.3) и сам решал,
чего не хватает для предложения создания (`_own_cabinet_missing`,
`_cabinet_prereq_issue`). Теперь решение — здесь, одной функцией по брифу.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Client
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.agency_cabinets import CabinetStepState, cabinet_step_state
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

IDENTITY = VkIdentity(
    external_id="10000009",
    username=None,
    title="Кабинет клиента",
    status="active",
)


def _settings(*, confirmed: bool = True) -> Settings:
    return Settings(vk_agency_confirmed=confirmed)


async def _with_db(
    scenario: Callable[[AsyncSession], Awaitable[T]],
    *,
    client_full_name: str | None = "Иван Иванов",
    brief_payload: dict[str, str] | None = None,
    brief_client_id: int | None = 100,
) -> T:
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
        session.add(Client(id=100, account_id=1, full_name=client_full_name))
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=brief_client_id,
                variant="individual",
                payload=brief_payload if brief_payload is not None else {"tax_id": "770123456789"},
            )
        )
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_step_unavailable_when_agency_not_confirmed() -> None:
    """Предохранитель выключен (дефолт) — шага нет вовсе, не «есть, но заблокирован»."""

    async def scenario(session: AsyncSession) -> CabinetStepState:
        return await cabinet_step_state(session, 1, 1, settings=_settings(confirmed=False))

    state = _run(_with_db(scenario))
    assert state.available is False
    assert state.own_cabinet_exists is False
    assert state.blocked_reason is None


def test_step_unavailable_when_brief_has_no_client() -> None:
    """У брифа нет привязанного клиента — заводить кабинет решительно не для кого."""

    async def scenario(session: AsyncSession) -> CabinetStepState:
        return await cabinet_step_state(session, 1, 1, settings=_settings())

    state = _run(_with_db(scenario, brief_client_id=None))
    assert state.available is False


def test_step_unavailable_when_brief_is_missing() -> None:
    async def scenario(session: AsyncSession) -> CabinetStepState:
        return await cabinet_step_state(session, 1, 999, settings=_settings())

    state = _run(_with_db(scenario))
    assert state.available is False


def test_step_available_and_no_own_cabinet_and_no_issue() -> None:
    """Флаг включён, клиент есть, имя и ИНН заполнены, своего кабинета ещё нет —
    шаг доступен, ничего не блокирует."""

    async def scenario(session: AsyncSession) -> CabinetStepState:
        return await cabinet_step_state(session, 1, 1, settings=_settings())

    state = _run(_with_db(scenario))
    assert state.available is True
    assert state.own_cabinet_exists is False
    assert state.blocked_reason is None


def test_step_reports_own_cabinet_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """У клиента уже есть свой кабинет — шаг доступен, но создавать не нужно."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)

    async def scenario(session: AsyncSession) -> CabinetStepState:
        await add_account(
            session,
            1,
            "fake-access-token-for-tests-0000000000000000",
            client_id=100,
            settings=Settings(vk_ads_secret_key=SecretStr(Fernet.generate_key().decode())),
        )
        await session.commit()
        return await cabinet_step_state(session, 1, 1, settings=_settings())

    state = _run(_with_db(scenario))
    assert state.available is True
    assert state.own_cabinet_exists is True
    assert state.blocked_reason is None


def test_step_blocked_when_client_name_missing() -> None:
    async def scenario(session: AsyncSession) -> CabinetStepState:
        return await cabinet_step_state(session, 1, 1, settings=_settings())

    state = _run(_with_db(scenario, client_full_name=None))
    assert state.available is True
    assert state.blocked_reason == "missing_name"


def test_step_blocked_when_tax_id_missing() -> None:
    async def scenario(session: AsyncSession) -> CabinetStepState:
        return await cabinet_step_state(session, 1, 1, settings=_settings())

    state = _run(_with_db(scenario, brief_payload={}))
    assert state.available is True
    assert state.blocked_reason == "missing_tax_id"


def _run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)
