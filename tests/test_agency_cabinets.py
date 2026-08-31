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
    VkApiAdapter,
)
from integrations.vk_oauth import (
    VkOAuthNotConfigured,
    VkOAuthRejected,
    VkOAuthToken,
    VkOAuthUnavailable,
)
from pydantic import SecretStr
from services.ad_accounts import ADVERTISER_THIRD_PARTY, ClientNotFoundError, DuplicateAccountError
from services.agency_cabinets import (
    AgencyCabinetClientGoneError,
    AgencyCabinetDuplicateError,
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

# Токен СОБСТВЕННОГО аккаунта агентства (`grant_type=client_credentials`) — им
# по умолчанию (без `agency_adapter=`) строится вызывающий адаптер.
OWN_TOKEN = VkOAuthToken(
    access_token=SecretStr("own-account-token-00000000000000"),
    refresh_token=SecretStr("own-account-refresh-00000000000000"),
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
) -> Settings:
    # `vk_ads_access_token` намеренно не участвует (правка после ревью): агентство
    # удостоверяет себя парой ключей приложения через `client_credentials`, а не
    # долгоживущим токеном из окружения — см. модульный докстринг agency_cabinets.py.
    return Settings(
        vk_agency_confirmed=confirmed,
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode() if key is None else key),
        vk_ads_client_id=SecretStr(oauth_id),
        vk_ads_client_secret=SecretStr(oauth_secret),
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


# --- удостоверение агентства собственным токеном (правка после ревью) ------------


def test_default_adapter_authenticates_with_own_account_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без `agency_adapter=` вызывающий сам получает токен собственного аккаунта
    (`grant_type=client_credentials`) — не берёт его из окружения."""

    _issue_ok(monkeypatch)
    own_token_calls: list[tuple[str, str]] = []

    async def issue_own(client_id: str, client_secret: str, **_: object) -> VkOAuthToken:
        own_token_calls.append((client_id, client_secret))
        return OWN_TOKEN

    monkeypatch.setattr(agency_cabinets, "request_own_account_token", issue_own)

    create_calls: list[str] = []

    async def fake_create_agency_client(self: VkApiAdapter, **kwargs: object) -> VkAgencyClient:
        # Адаптер обязан быть построен на токене СОБСТВЕННОГО аккаунта, не на
        # токене клиента и не на чём-либо из окружения.
        create_calls.append(self._token.get_secret_value())
        return VK_CLIENT

    monkeypatch.setattr(VkApiAdapter, "create_agency_client", fake_create_agency_client)

    async def scenario(session: AsyncSession) -> None:
        view = await create_client_cabinet(
            session,
            1,
            100,
            full_name="Иван Иванов",
            tax_id="770123456789",
            settings=_settings(oauth_id="app-id", oauth_secret="app-secret"),
        )
        assert view.client_id == 100

    asyncio.run(_with_db(scenario))
    assert own_token_calls == [("app-id", "app-secret")]
    assert create_calls == [OWN_TOKEN.access_token.get_secret_value()]


def test_default_adapter_retries_own_account_token_once_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _issue_ok(monkeypatch)
    calls = {"n": 0}

    async def issue_own(client_id: str, client_secret: str, **_: object) -> VkOAuthToken:
        calls["n"] += 1
        if calls["n"] == 1:
            raise VkOAuthUnavailable("timeout")
        return OWN_TOKEN

    monkeypatch.setattr(agency_cabinets, "request_own_account_token", issue_own)

    async def fake_create_agency_client(self: VkApiAdapter, **kwargs: object) -> VkAgencyClient:
        return VK_CLIENT

    monkeypatch.setattr(VkApiAdapter, "create_agency_client", fake_create_agency_client)

    async def scenario(session: AsyncSession) -> None:
        view = await create_client_cabinet(
            session,
            1,
            100,
            full_name="Иван Иванов",
            tax_id="770123456789",
            settings=_settings(),
        )
        assert view.client_id == 100

    asyncio.run(_with_db(scenario))
    assert calls["n"] == 2


def test_default_adapter_does_not_retry_own_account_token_on_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ по существу (неверные ключи приложения) — повторять бессмысленно,
    и до создания клиента в VK дело даже не доходит."""

    calls = {"n": 0}

    async def issue_own(client_id: str, client_secret: str, **_: object) -> VkOAuthToken:
        calls["n"] += 1
        raise VkOAuthRejected("invalid_client")

    monkeypatch.setattr(agency_cabinets, "request_own_account_token", issue_own)

    create_calls: list[str] = []

    async def fake_create_agency_client(self: VkApiAdapter, **kwargs: object) -> VkAgencyClient:
        create_calls.append("called")
        return VK_CLIENT

    monkeypatch.setattr(VkApiAdapter, "create_agency_client", fake_create_agency_client)

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(VkOAuthRejected):
            await create_client_cabinet(
                session,
                1,
                100,
                full_name="Иван Иванов",
                tax_id="770123456789",
                settings=_settings(),
            )

    asyncio.run(_with_db(scenario))
    assert calls["n"] == 1
    assert create_calls == []


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


def test_persist_failure_duplicate_account_is_typed_and_carries_vk_client() -> None:
    """`add_account` находит уже активный кабинет с тем же внешним id VK — это
    другая история, чем «клиент пропал» (ниже), и должна различаться."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        monkeypatch = pytest.MonkeyPatch()
        _issue_ok(monkeypatch)
        # Кабинет с тем же external_id уже существует (тем же мокнутым
        # `fetch_identity` из автоиспользуемой фикстуры — IDENTITY.external_id).
        await ad_accounts.add_account(session, 1, "already-here-token", settings=cfg)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            with pytest.raises(AgencyCabinetDuplicateError) as excinfo:
                await create_client_cabinet(
                    session,
                    1,
                    100,
                    full_name="Иван Иванов",
                    tax_id="770123456789",
                    settings=cfg,
                    agency_adapter=adapter,
                )
        finally:
            monkeypatch.undo()
        assert excinfo.value.vk_client_id == "777"
        assert excinfo.value.vk_username == "new-client@agency_client"
        assert isinstance(excinfo.value.__cause__, DuplicateAccountError)
        # Половинчатый провал — тоже `AgencyCabinetPersistError`: код,
        # который ловит общий базовый класс, не должен сломаться.
        assert isinstance(excinfo.value, AgencyCabinetPersistError)

    asyncio.run(_with_db(scenario))


def test_persist_failure_client_gone_is_typed_and_carries_vk_client() -> None:
    """Клиент существовал на ранней проверке, но исчез к моменту сохранения
    (гонка/удаление) — `add_account` находит это сам через свою же проверку."""

    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _issue_ok(monkeypatch)

        # Ранняя проверка в create_client_cabinet идёт через СВОЙ импорт
        # get_client (agency_cabinets.get_client) — его не трогаем, клиент 100
        # для неё по-прежнему существует. А внутри add_account клиент ищется
        # через ОТДЕЛЬНЫЙ импорт (ad_accounts.get_client) — вот его и подменяем,
        # чтобы смоделировать «исчез между проверкой и сохранением».
        async def vanished(session_: AsyncSession, account_id_: int, client_id_: int) -> None:
            return None

        monkeypatch.setattr(ad_accounts, "get_client", vanished)
        adapter = FakeAgencyAdapter([VK_CLIENT])
        try:
            with pytest.raises(AgencyCabinetClientGoneError) as excinfo:
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
        assert isinstance(excinfo.value.__cause__, ClientNotFoundError)
        assert isinstance(excinfo.value, AgencyCabinetPersistError)

    asyncio.run(_with_db(scenario))
