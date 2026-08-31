"""Сервис заведения клиенту рекламного кабинета VK (B2/B3), план 2026-08-25.

VK замокан целиком (протокол `AgencyCabinetAdapter` + модульные функции
`integrations.vk_oauth`) — без сети, без respx: HTTP-поведение
`create_agency_client` уже покрыто задачей B1 (`test_vk_adapter.py`), здесь
важна только оркестрация вокруг него — предохранители, порядок вызовов, две
попытки при сетевом сбое, типизированные отказы на середине операции.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import pytest
import services.ad_accounts as ad_accounts
import services.agency_cabinets as agency_cabinets
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Client
from integrations.vk_api import (
    VkAgencyClient,
    VkAgencyClientForbidden,
    VkAgencyClientUnavailable,
)
from integrations.vk_oauth import (
    VkOAuthNotConfigured,
    VkOAuthRejected,
    VkOAuthToken,
    VkOAuthUnavailable,
)
from pydantic import SecretStr
from services.ad_accounts import ADVERTISER_THIRD_PARTY, ClientNotFoundError
from services.agency_cabinets import (
    AgencyCabinetPersistError,
    AgencyDisabledError,
    AgencyMissingTaxIdError,
    AgencyTokenIssuanceFailedError,
    create_client_cabinet,
)
from services.secret_box import NotConfiguredError
from services.vk_identity import InvalidTokenError, VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

IDENTITY = VkIdentity(
    external_id="10000002",
    username="new-client@agency_client",
    title="Клиентский кабинет",
    status="active",
)

VK_CLIENT = VkAgencyClient(
    client_id="777",
    username="new-client@agency_client",
    ad_account_id="10000002",
    balance="0",
    status="active",
    access_type="full_access",
)

TOKEN = VkOAuthToken(
    access_token=SecretStr("fresh-access-token-000000000000"),
    refresh_token=SecretStr("fresh-refresh-token-000000000000"),
    expires_at=datetime.now(UTC) + timedelta(hours=24),
)


class FakeAgencyAdapter:
    """Двойник `AgencyCabinetAdapter`: считает вызовы, отдаёт заготовленные ответы/ошибки
    по очереди (список `outcomes` — либо `VkAgencyClient`, либо исключение)."""

    def __init__(self, outcomes: list[VkAgencyClient | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    async def create_agency_client(
        self,
        *,
        client_name: str,
        client_info: str | None = None,
        additional_emails: Sequence[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ) -> VkAgencyClient:
        self.calls.append({"client_name": client_name, "client_info": client_info})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _settings(
    *,
    confirmed: bool = True,
    key: str | None = None,
    oauth_id: str = "app-id",
    oauth_secret: str = "app-secret",
    access_token: str = "agency-master-token",
) -> Settings:
    return Settings(
        vk_agency_confirmed=confirmed,
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode() if key is None else key),
        vk_ads_client_id=SecretStr(oauth_id),
        vk_ads_client_secret=SecretStr(oauth_secret),
        vk_ads_access_token=SecretStr(access_token),
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
        session.add(Client(id=100, account_id=1, full_name="Иван Иванов"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


@pytest.fixture(autouse=True)
def _mock_vk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """`add_account` внутри операции опознаёт свежий токен живым запросом — тоже мок."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return "500.00"

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


def _issue_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def issue(*args: object, **kwargs: object) -> VkOAuthToken:
        return TOKEN

    monkeypatch.setattr(agency_cabinets, "request_agency_client_token", issue)


# --- успешный путь ---------------------------------------------------------------


def test_success_creates_client_issues_token_and_saves_account() -> None:
    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _issue_ok(monkeypatch)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            view = await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
                agency_adapter=adapter,
            )
        finally:
            monkeypatch.undo()
        assert view.client_id == 100
        assert view.advertiser_kind == ADVERTISER_THIRD_PARTY
        assert view.advertiser_name == "Иван Иванов"
        assert view.advertiser_inn == "770123456789"
        assert view.external_id == "10000002"
        assert len(adapter.calls) == 1
        assert adapter.calls[0]["client_name"] == "Иван Иванов"
        assert adapter.calls[0]["client_info"] == "ИНН 770123456789"

    asyncio.run(_with_db(scenario))


def test_niche_is_appended_to_cabinet_name() -> None:
    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _issue_ok(monkeypatch)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                niche="доставка цветов",
                settings=_settings(),
                agency_adapter=adapter,
            )
        finally:
            monkeypatch.undo()
        assert adapter.calls[0]["client_name"] == "Иван Иванов — доставка цветов"

    asyncio.run(_with_db(scenario))


def test_missing_niche_leaves_name_as_full_name_only() -> None:
    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _issue_ok(monkeypatch)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
                agency_adapter=adapter,
            )
        finally:
            monkeypatch.undo()
        assert adapter.calls[0]["client_name"] == "Иван Иванов"

    asyncio.run(_with_db(scenario))


# --- предохранители ---------------------------------------------------------------


def test_disabled_flag_blocks_creation_without_touching_vk() -> None:
    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter([VK_CLIENT])
        with pytest.raises(AgencyDisabledError):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(confirmed=False),
                agency_adapter=adapter,
            )
        assert adapter.calls == []

    asyncio.run(_with_db(scenario))


def test_missing_tax_id_is_rejected_without_touching_vk() -> None:
    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter([VK_CLIENT])
        with pytest.raises(AgencyMissingTaxIdError):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id=None,
                settings=_settings(),
                agency_adapter=adapter,
            )
        assert adapter.calls == []

    asyncio.run(_with_db(scenario))


def test_blank_tax_id_is_rejected_too() -> None:
    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter([VK_CLIENT])
        with pytest.raises(AgencyMissingTaxIdError):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="   ",
                settings=_settings(),
                agency_adapter=adapter,
            )
        assert adapter.calls == []

    asyncio.run(_with_db(scenario))


def test_unknown_client_is_rejected_without_touching_vk() -> None:
    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter([VK_CLIENT])
        with pytest.raises(ClientNotFoundError):
            await create_client_cabinet(
                session,
                1,
                999,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
                agency_adapter=adapter,
            )
        assert adapter.calls == []

    asyncio.run(_with_db(scenario))


def test_missing_encryption_key_is_rejected_without_touching_vk() -> None:
    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter([VK_CLIENT])
        with pytest.raises(NotConfiguredError):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(key=""),
                agency_adapter=adapter,
            )
        assert adapter.calls == []

    asyncio.run(_with_db(scenario))


def test_missing_oauth_app_credentials_is_rejected_without_touching_vk() -> None:
    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter([VK_CLIENT])
        with pytest.raises(VkOAuthNotConfigured):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(oauth_id=""),
                agency_adapter=adapter,
            )
        assert adapter.calls == []

    asyncio.run(_with_db(scenario))


# --- две попытки при сетевом сбое, без повтора при отказе по существу -------------


def test_create_agency_client_retries_once_on_network_failure() -> None:
    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _issue_ok(monkeypatch)
        adapter = FakeAgencyAdapter([VkAgencyClientUnavailable("timeout"), VK_CLIENT])
        try:
            view = await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
                agency_adapter=adapter,
            )
        finally:
            monkeypatch.undo()
        assert view.client_id == 100
        assert len(adapter.calls) == 2

    asyncio.run(_with_db(scenario))


def test_create_agency_client_does_not_retry_a_third_time() -> None:
    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter(
            [VkAgencyClientUnavailable("timeout"), VkAgencyClientUnavailable("timeout again")]
        )
        with pytest.raises(VkAgencyClientUnavailable):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
                agency_adapter=adapter,
            )
        assert len(adapter.calls) == 2

    asyncio.run(_with_db(scenario))


def test_create_agency_client_does_not_retry_on_forbidden() -> None:
    """Отказ по существу (403 — нет прав агентства), а не сеть: повторять бессмысленно."""

    async def scenario(session: AsyncSession) -> None:
        adapter = FakeAgencyAdapter([VkAgencyClientForbidden("no rights"), VK_CLIENT])
        with pytest.raises(VkAgencyClientForbidden):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
                agency_adapter=adapter,
            )
        assert len(adapter.calls) == 1

    asyncio.run(_with_db(scenario))


def test_token_issuance_retries_once_on_network_failure() -> None:
    async def scenario(session: AsyncSession) -> None:
        calls = {"n": 0}

        async def issue(*args: object, **kwargs: object) -> VkOAuthToken:
            calls["n"] += 1
            if calls["n"] == 1:
                raise VkOAuthUnavailable("timeout")
            return TOKEN

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(agency_cabinets, "request_agency_client_token", issue)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            view = await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
                agency_adapter=adapter,
            )
        finally:
            monkeypatch.undo()
        assert view.client_id == 100
        assert calls["n"] == 2

    asyncio.run(_with_db(scenario))


def test_token_issuance_does_not_retry_a_third_time() -> None:
    async def scenario(session: AsyncSession) -> None:
        calls = {"n": 0}

        async def issue(*args: object, **kwargs: object) -> VkOAuthToken:
            calls["n"] += 1
            raise VkOAuthUnavailable("still down")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(agency_cabinets, "request_agency_client_token", issue)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            with pytest.raises(AgencyTokenIssuanceFailedError):
                await create_client_cabinet(
                    session,
                    1,
                    100,
                    full_name="Иван Иванов",
                    tax_id="770123456789",
                    settings=_settings(),
                    agency_adapter=adapter,
                )
        finally:
            monkeypatch.undo()
        assert calls["n"] == 2

    asyncio.run(_with_db(scenario))


def test_token_issuance_failure_is_typed_and_carries_vk_client() -> None:
    """Клиент в VK уже заведён — отказ должен быть внятным, не тихим провалом."""

    async def scenario(session: AsyncSession) -> None:
        async def issue(*args: object, **kwargs: object) -> VkOAuthToken:
            raise VkOAuthRejected("token limit exceeded")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(agency_cabinets, "request_agency_client_token", issue)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            with pytest.raises(AgencyTokenIssuanceFailedError) as excinfo:
                await create_client_cabinet(
                    session,
                    1,
                    100,
                    full_name="Иван Иванов",
                    tax_id="770123456789",
                    settings=_settings(),
                    agency_adapter=adapter,
                )
        finally:
            monkeypatch.undo()
        assert excinfo.value.vk_client_id == "777"
        assert excinfo.value.vk_username == "new-client@agency_client"
        assert isinstance(excinfo.value.__cause__, VkOAuthRejected)

    asyncio.run(_with_db(scenario))


def test_persist_failure_after_token_issuance_is_typed() -> None:
    """Токен выпущен, но проверка живым запросом (внутри `add_account`) не прошла."""

    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _issue_ok(monkeypatch)

        async def broken_identity(token: str, **_: object) -> VkIdentity:
            raise InvalidTokenError("rejected")

        monkeypatch.setattr(ad_accounts, "fetch_identity", broken_identity)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            with pytest.raises(AgencyCabinetPersistError) as excinfo:
                await create_client_cabinet(
                    session,
                    1,
                    100,
                    full_name="Иван Иванов",
                    tax_id="770123456789",
                    settings=_settings(),
                    agency_adapter=adapter,
                )
        finally:
            monkeypatch.undo()
        assert excinfo.value.vk_client_id == "777"
        assert isinstance(excinfo.value.__cause__, InvalidTokenError)

    asyncio.run(_with_db(scenario))
