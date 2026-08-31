"""Сервис рекламных кабинетов (spec 2026-07-27 §8.2).

VK замокан целиком: тесты не ходят в сеть. Проверяем добавление с валидным и
битым токеном, дубли, состояния health, TTL кеша, расшифровку токена, удаление
и посев из окружения.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import pytest
import services.ad_accounts as ad_accounts
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, AdAccount, Client
from db.repositories import get_ad_account
from integrations.vk_oauth import VkOAuthRejected, VkOAuthToken, VkOAuthUnavailable
from pydantic import SecretStr
from services.ad_accounts import (
    ADVERTISER_THIRD_PARTY,
    HEALTH_ERROR,
    HEALTH_HEALTHY,
    HEALTH_UNAUTHORIZED,
    AccountNotFoundError,
    ClientNotFoundError,
    DuplicateAccountError,
    TokenRefreshFailedError,
    TokenRefreshUnavailableError,
    TokenUnavailableError,
    add_account,
    check_health,
    delete_account,
    list_accounts,
    list_accounts_for_client,
    mark_unauthorized,
    refresh_account_token,
    resolve_token,
    seed_from_env,
    set_account_client,
)
from services.secret_box import NotConfiguredError
from services.vk_identity import InvalidTokenError, VkIdentity, VkUnreachableError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"

IDENTITY = VkIdentity(
    external_id="10000001",
    username="a1b2c3d4e5@agency_client",
    title="Студия «Пример»",
    status="active",
)


def _settings(
    *,
    key: str | None = None,
    token: str = "",
    ttl: int = 15,
    oauth_id: str = "",
    oauth_secret: str = "",
) -> Settings:
    return Settings(
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode() if key is None else key),
        vk_ads_access_token=SecretStr(token),
        ad_account_health_ttl_minutes=ttl,
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
        session.add(Account(id=2, name="tenant-two"))
        session.add(Client(id=100, account_id=1, full_name="Клиент 1"))
        session.add(Client(id=101, account_id=1, full_name=None))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


@pytest.fixture(autouse=True)
def _mock_vk(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию VK отвечает успехом. Отдельные тесты переопределяют."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return "12345.67"

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


def _fail_identity(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    async def broken(token: str, **_: object) -> VkIdentity:
        raise exc

    monkeypatch.setattr(ad_accounts, "fetch_identity", broken)


# --- добавление ---------------------------------------------------------------


def test_add_account_pulls_name_and_id_from_vk() -> None:
    """Оператор вставляет только токен — остальное берётся из VK (spec §2.1)."""

    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, settings=_settings())
        assert view.title == "Студия «Пример»"
        assert view.external_id == "10000001"
        assert view.username == "a1b2c3d4e5@agency_client"
        assert view.health == HEALTH_HEALTHY
        assert view.balance_rub == "12345.67"

    asyncio.run(_with_db(scenario))


def test_add_account_stores_token_encrypted_only() -> None:
    """В колонке обязан лежать шифротекст, а наружу уходить только хвост."""

    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, settings=_settings())
        row = await get_ad_account(session, 1, view.id)
        assert row is not None
        assert row.token_encrypted is not None
        assert TOKEN not in row.token_encrypted
        assert view.token_tail == TOKEN[-4:]

    asyncio.run(_with_db(scenario))


def test_view_never_exposes_token() -> None:
    """Свойство, ради которого всё затевалось: в представлении нет поля с токеном."""

    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, settings=_settings())
        assert TOKEN not in repr(view)

    asyncio.run(_with_db(scenario))


def test_add_account_rejects_invalid_token_without_writing_row() -> None:
    """Битый токен не должен оставить в базе «кабинет», который не работает."""

    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _fail_identity(monkeypatch, InvalidTokenError("rejected"))
        try:
            with pytest.raises(InvalidTokenError):
                await add_account(session, 1, "bad", settings=_settings())
        finally:
            monkeypatch.undo()
        assert await list_accounts(session, 1, refresh_stale=False) == []

    asyncio.run(_with_db(scenario))


def test_add_account_propagates_vk_unreachable() -> None:
    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _fail_identity(monkeypatch, VkUnreachableError("timeout"))
        try:
            with pytest.raises(VkUnreachableError):
                await add_account(session, 1, TOKEN, settings=_settings())
        finally:
            monkeypatch.undo()

    asyncio.run(_with_db(scenario))


def test_add_account_rejects_duplicate() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()
        with pytest.raises(DuplicateAccountError):
            await add_account(session, 1, TOKEN, settings=cfg)

    asyncio.run(_with_db(scenario))


def test_add_account_requires_encryption_key() -> None:
    """Без ключа шифрования секрет некуда положить — отказ, а не запись в открытую."""

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(NotConfiguredError):
            await add_account(session, 1, TOKEN, settings=_settings(key=""))

    asyncio.run(_with_db(scenario))


def test_custom_title_overrides_vk_name() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, title="Основной", settings=_settings())
        assert view.title == "Основной"

    asyncio.run(_with_db(scenario))


def test_blank_title_falls_back_to_vk_name() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, title="   ", settings=_settings())
        assert view.title == "Студия «Пример»"

    asyncio.run(_with_db(scenario))


def test_third_party_advertiser_is_stored() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await add_account(
            session,
            1,
            TOKEN,
            advertiser_kind=ADVERTISER_THIRD_PARTY,
            advertiser_name="ООО «Ромашка»",
            advertiser_inn="7701234567",
            settings=_settings(),
        )
        assert view.advertiser_kind == ADVERTISER_THIRD_PARTY
        assert view.advertiser_name == "ООО «Ромашка»"
        assert view.advertiser_inn == "7701234567"

    asyncio.run(_with_db(scenario))


def test_owner_kind_drops_advertiser_details() -> None:
    """Реклама владельца — данные третьего лица не сохраняем, чтобы не путать."""

    async def scenario(session: AsyncSession) -> None:
        view = await add_account(
            session,
            1,
            TOKEN,
            advertiser_kind="owner",
            advertiser_name="ООО «Ромашка»",
            advertiser_inn="7701234567",
            settings=_settings(),
        )
        assert view.advertiser_name is None
        assert view.advertiser_inn is None

    asyncio.run(_with_db(scenario))


def test_unknown_advertiser_kind_falls_back_to_owner() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, advertiser_kind="junk", settings=_settings())
        assert view.advertiser_kind == "owner"

    asyncio.run(_with_db(scenario))


def test_refresh_token_is_encrypted_too() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await add_account(
            session, 1, TOKEN, refresh_token="fake-refresh-token", settings=_settings()
        )
        row = await get_ad_account(session, 1, view.id)
        assert row is not None
        assert row.refresh_encrypted is not None
        assert "fake-refresh-token" not in row.refresh_encrypted

    asyncio.run(_with_db(scenario))


# --- health ------------------------------------------------------------------


def test_check_health_marks_unauthorized_on_rejected_token() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        monkeypatch = pytest.MonkeyPatch()
        _fail_identity(monkeypatch, InvalidTokenError("rejected"))
        try:
            checked = await check_health(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert checked.health == HEALTH_UNAUTHORIZED
        assert checked.is_usable is False

    asyncio.run(_with_db(scenario))


def test_check_health_marks_error_on_network_problem() -> None:
    """Сеть моргнула — это не «токен плохой». Кабинет остаётся пригодным."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        monkeypatch = pytest.MonkeyPatch()
        _fail_identity(monkeypatch, VkUnreachableError("HTTP 503"))
        try:
            checked = await check_health(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert checked.health == HEALTH_ERROR
        assert checked.is_usable is True

    asyncio.run(_with_db(scenario))


def test_check_health_recovers_to_healthy() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        await mark_unauthorized(session, 1, view.id, "revoked")
        recovered = await check_health(session, 1, view.id, settings=cfg)
        assert recovered.health == HEALTH_HEALTHY
        assert recovered.health_error is None

    asyncio.run(_with_db(scenario))


def test_check_health_flags_blocked_vk_account() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)

        async def blocked(token: str, **_: object) -> VkIdentity:
            return VkIdentity("10000001", "u", "T", "blocked")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ad_accounts, "fetch_identity", blocked)
        try:
            checked = await check_health(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert checked.health == HEALTH_ERROR
        assert "blocked" in (checked.health_error or "")

    asyncio.run(_with_db(scenario))


def test_check_health_of_missing_account_raises() -> None:
    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(AccountNotFoundError):
            await check_health(session, 1, 999, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_check_health_survives_key_rotation() -> None:
    """Ключ сменили — честно `error`, но приложение не падает."""

    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, settings=_settings())
        checked = await check_health(session, 1, view.id, settings=_settings())
        assert checked.health == HEALTH_ERROR

    asyncio.run(_with_db(scenario))


def test_list_refreshes_only_stale_entries() -> None:
    """Кеш: свежий health-check не тревожит VK повторно (лимит 3 rps)."""
    calls = {"n": 0}

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(ttl=15)
        await add_account(session, 1, TOKEN, settings=cfg)

        async def counting(token: str, **_: object) -> VkIdentity:
            calls["n"] += 1
            return IDENTITY

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ad_accounts, "fetch_identity", counting)
        try:
            await list_accounts(session, 1, settings=cfg)
            assert calls["n"] == 0  # проверка только что была при добавлении
            row = await get_ad_account(session, 1, 1)
            assert row is not None
            row.health_checked_at = datetime.now(UTC) - timedelta(minutes=30)
            await session.flush()
            await list_accounts(session, 1, settings=cfg)
            assert calls["n"] == 1
        finally:
            monkeypatch.undo()

    asyncio.run(_with_db(scenario))


def test_list_without_refresh_never_calls_vk() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        await add_account(session, 1, TOKEN, settings=cfg)
        row = await get_ad_account(session, 1, 1)
        assert row is not None
        row.health = "unknown"
        row.health_checked_at = None
        await session.flush()

        async def explode(token: str, **_: object) -> VkIdentity:
            raise AssertionError("VK must not be called")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ad_accounts, "fetch_identity", explode)
        try:
            views = await list_accounts(session, 1, refresh_stale=False, settings=cfg)
        finally:
            monkeypatch.undo()
        assert len(views) == 1

    asyncio.run(_with_db(scenario))


def test_list_is_scoped_to_tenant() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        await add_account(session, 1, TOKEN, settings=cfg)
        assert len(await list_accounts(session, 1, refresh_stale=False, settings=cfg)) == 1
        assert await list_accounts(session, 2, refresh_stale=False, settings=cfg) == []

    asyncio.run(_with_db(scenario))


# --- токен и удаление ---------------------------------------------------------


def test_resolve_token_returns_original_secret() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        secret = await resolve_token(session, 1, view.id, settings=cfg)
        assert secret.get_secret_value() == TOKEN
        assert TOKEN not in repr(secret)

    asyncio.run(_with_db(scenario))


def test_resolve_token_scoped_to_tenant() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        with pytest.raises(AccountNotFoundError):
            await resolve_token(session, 2, view.id, settings=cfg)

    asyncio.run(_with_db(scenario))


def test_resolve_token_after_delete_fails() -> None:
    """Удалённым кабинетом запустить кампанию нельзя — токена больше нет."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        await delete_account(session, 1, view.id)
        with pytest.raises(TokenUnavailableError):
            await resolve_token(session, 1, view.id, settings=cfg)

    asyncio.run(_with_db(scenario))


def test_delete_hides_account_and_wipes_secret() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        await delete_account(session, 1, view.id)
        assert await list_accounts(session, 1, refresh_stale=False, settings=cfg) == []
        row = await get_ad_account(session, 1, view.id)
        assert row is not None
        assert isinstance(row, AdAccount)
        assert row.token_encrypted is None

    asyncio.run(_with_db(scenario))


def test_delete_missing_account_raises() -> None:
    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(AccountNotFoundError):
            await delete_account(session, 1, 999)

    asyncio.run(_with_db(scenario))


# --- привязка к клиенту (spec 2026-08-25 §1.1) --------------------------------


def test_new_account_is_common_by_default() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, settings=_settings())
        assert view.client_id is None
        assert view.client_name is None

    asyncio.run(_with_db(scenario))


def test_add_account_with_client_binds_and_names_it() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, client_id=100, settings=_settings())
        assert view.client_id == 100
        assert view.client_name == "Клиент 1"

    asyncio.run(_with_db(scenario))


def test_add_account_rejects_client_from_another_tenant() -> None:
    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(ClientNotFoundError):
            await add_account(session, 2, TOKEN, client_id=100, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_add_account_rejects_client_without_writing_row() -> None:
    """Отказ по клиенту не должен оставить в базе кабинет — как и отказ VK."""

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(ClientNotFoundError):
            await add_account(session, 1, TOKEN, client_id=999, settings=_settings())
        assert await list_accounts(session, 1, refresh_stale=False) == []

    asyncio.run(_with_db(scenario))


def test_client_with_blank_full_name_shows_as_blank() -> None:
    """`Client.full_name=None` остаётся `None` — заглушку придумывает интерфейс."""

    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, client_id=101, settings=_settings())
        assert view.client_name is None

    asyncio.run(_with_db(scenario))


def test_set_account_client_updates_binding() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        bound = await set_account_client(session, 1, view.id, 100, settings=cfg)
        assert bound.client_id == 100
        assert bound.client_name == "Клиент 1"

    asyncio.run(_with_db(scenario))


def test_set_account_client_to_none_frees_it() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, client_id=100, settings=cfg)
        freed = await set_account_client(session, 1, view.id, None, settings=cfg)
        assert freed.client_id is None
        assert freed.client_name is None

    asyncio.run(_with_db(scenario))


def test_set_account_client_rejects_unknown_client() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)
        with pytest.raises(ClientNotFoundError):
            await set_account_client(session, 1, view.id, 999, settings=cfg)

    asyncio.run(_with_db(scenario))


def test_set_account_client_missing_account_raises() -> None:
    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(AccountNotFoundError):
            await set_account_client(session, 1, 999, 100, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_list_accounts_for_client_reflects_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()

        async def identity_a(token: str, **_: object) -> VkIdentity:
            return VkIdentity("111", "a", "Общий", "active")

        monkeypatch.setattr(ad_accounts, "fetch_identity", identity_a)
        common = await add_account(session, 1, TOKEN, settings=cfg)

        async def identity_b(token: str, **_: object) -> VkIdentity:
            return VkIdentity("222", "b", "Клиентский", "active")

        monkeypatch.setattr(ad_accounts, "fetch_identity", identity_b)
        bound = await add_account(session, 1, "another-token", client_id=100, settings=cfg)

        for_owner = {
            v.external_id for v in await list_accounts_for_client(session, 1, 100, settings=cfg)
        }
        assert for_owner == {common.external_id, bound.external_id}

        for_other = {
            v.external_id for v in await list_accounts_for_client(session, 1, 999, settings=cfg)
        }
        assert for_other == {common.external_id}

    asyncio.run(_with_db(scenario))


def test_view_never_exposes_token_alongside_client_fields() -> None:
    """Расширение представления клиентскими полями не задевает главный инвариант."""

    async def scenario(session: AsyncSession) -> None:
        view = await add_account(session, 1, TOKEN, client_id=100, settings=_settings())
        assert TOKEN not in repr(view)

    asyncio.run(_with_db(scenario))


# --- посев из окружения -------------------------------------------------------


def test_seed_creates_account_from_env_token() -> None:
    async def scenario(session: AsyncSession) -> None:
        view = await seed_from_env(session, 1, settings=_settings(token=TOKEN))
        assert view is not None
        assert view.external_id == "10000001"

    asyncio.run(_with_db(scenario))


def test_seed_does_nothing_when_accounts_exist() -> None:
    """Посев одноразовый: он не должен воскрешать удалённый оператором кабинет."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(token=TOKEN)
        await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()
        assert await seed_from_env(session, 1, settings=cfg) is None

    asyncio.run(_with_db(scenario))


def test_seed_without_env_token_does_nothing() -> None:
    async def scenario(session: AsyncSession) -> None:
        assert await seed_from_env(session, 1, settings=_settings(token="")) is None

    asyncio.run(_with_db(scenario))


def test_seed_without_encryption_key_does_nothing() -> None:
    async def scenario(session: AsyncSession) -> None:
        assert await seed_from_env(session, 1, settings=_settings(key="", token=TOKEN)) is None

    asyncio.run(_with_db(scenario))


def test_seed_survives_vk_being_down() -> None:
    """Недоступность VK не должна ронять старт ядра — попробуем в следующий раз."""

    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _fail_identity(monkeypatch, VkUnreachableError("timeout"))
        try:
            assert await seed_from_env(session, 1, settings=_settings(token=TOKEN)) is None
        finally:
            monkeypatch.undo()

    asyncio.run(_with_db(scenario))


def test_seed_survives_invalid_env_token() -> None:
    async def scenario(session: AsyncSession) -> None:
        monkeypatch = pytest.MonkeyPatch()
        _fail_identity(monkeypatch, InvalidTokenError("rejected"))
        try:
            assert await seed_from_env(session, 1, settings=_settings(token=TOKEN)) is None
        finally:
            monkeypatch.undo()

    asyncio.run(_with_db(scenario))


# --- обновление токена по ключу обновления (B3) --------------------------------

REFRESHED = VkOAuthToken(
    access_token=SecretStr("refreshed-access-token-0000000000"),
    refresh_token=SecretStr("refreshed-refresh-token-0000000000"),
    expires_at=datetime.now(UTC) + timedelta(hours=24),
)


def _mock_refresh(monkeypatch: pytest.MonkeyPatch, outcome: VkOAuthToken | Exception) -> None:
    async def refresh(*args: object, **kwargs: object) -> VkOAuthToken:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ad_accounts, "refresh_agency_token", refresh)


def test_refresh_account_token_replaces_both_secrets() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(oauth_id="app", oauth_secret="secret")
        view = await add_account(session, 1, TOKEN, refresh_token="old-refresh", settings=cfg)
        monkeypatch = pytest.MonkeyPatch()
        _mock_refresh(monkeypatch, REFRESHED)
        try:
            refreshed = await refresh_account_token(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert refreshed.health == HEALTH_HEALTHY
        assert refreshed.token_tail == REFRESHED.access_token.get_secret_value()[-4:]
        row = await get_ad_account(session, 1, view.id)
        assert row is not None
        assert row.token_encrypted is not None
        assert "refreshed-access-token" not in row.token_encrypted
        assert "refreshed-refresh-token" not in (row.refresh_encrypted or "")

    asyncio.run(_with_db(scenario))


def test_refresh_account_token_retries_once_on_network_failure() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(oauth_id="app", oauth_secret="secret")
        view = await add_account(session, 1, TOKEN, refresh_token="old-refresh", settings=cfg)
        calls = {"n": 0}

        async def refresh(*args: object, **kwargs: object) -> VkOAuthToken:
            calls["n"] += 1
            if calls["n"] == 1:
                raise VkOAuthUnavailable("timeout")
            return REFRESHED

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ad_accounts, "refresh_agency_token", refresh)
        try:
            refreshed = await refresh_account_token(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert refreshed.health == HEALTH_HEALTHY
        assert calls["n"] == 2

    asyncio.run(_with_db(scenario))


def test_refresh_account_token_does_not_retry_on_rejection() -> None:
    """Отказ по существу (лимит токенов/просрочен refresh) — повторять бессмысленно."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(oauth_id="app", oauth_secret="secret")
        view = await add_account(session, 1, TOKEN, refresh_token="old-refresh", settings=cfg)
        calls = {"n": 0}

        async def refresh(*args: object, **kwargs: object) -> VkOAuthToken:
            calls["n"] += 1
            raise VkOAuthRejected("invalid_grant")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ad_accounts, "refresh_agency_token", refresh)
        try:
            with pytest.raises(TokenRefreshFailedError) as excinfo:
                await refresh_account_token(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert calls["n"] == 1
        assert isinstance(excinfo.value.__cause__, VkOAuthRejected)

    asyncio.run(_with_db(scenario))


def test_refresh_account_token_without_refresh_token_is_unavailable() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(oauth_id="app", oauth_secret="secret")
        view = await add_account(session, 1, TOKEN, settings=cfg)  # без refresh_token
        with pytest.raises(TokenRefreshUnavailableError):
            await refresh_account_token(session, 1, view.id, settings=cfg)

    asyncio.run(_with_db(scenario))


def test_refresh_account_token_missing_account_raises() -> None:
    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(AccountNotFoundError):
            await refresh_account_token(session, 1, 999, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_check_health_recovers_via_refresh_after_401() -> None:
    """Токен протух (401), но обновление ключом — успешно: кабинет остаётся healthy,
    а не молча падает в unauthorized (B3 — мёртвое поле refresh_encrypted теперь читается)."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(oauth_id="app", oauth_secret="secret")
        view = await add_account(session, 1, TOKEN, refresh_token="old-refresh", settings=cfg)

        monkeypatch = pytest.MonkeyPatch()
        _mock_refresh(monkeypatch, REFRESHED)

        calls = {"n": 0}

        async def identity(token: str, **_: object) -> VkIdentity:
            calls["n"] += 1
            if token == TOKEN:
                raise InvalidTokenError("expired")
            return IDENTITY

        monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
        try:
            checked = await check_health(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert checked.health == HEALTH_HEALTHY
        assert checked.is_usable is True
        row = await get_ad_account(session, 1, view.id)
        assert row is not None
        assert row.token_encrypted is not None
        assert "refreshed-access-token" not in row.token_encrypted

    asyncio.run(_with_db(scenario))


def test_check_health_stays_unauthorized_when_refresh_also_fails() -> None:
    async def scenario(session: AsyncSession) -> None:
        cfg = _settings(oauth_id="app", oauth_secret="secret")
        view = await add_account(session, 1, TOKEN, refresh_token="old-refresh", settings=cfg)

        monkeypatch = pytest.MonkeyPatch()
        _mock_refresh(monkeypatch, VkOAuthRejected("invalid_grant"))
        _fail_identity(monkeypatch, InvalidTokenError("expired"))
        try:
            checked = await check_health(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert checked.health == HEALTH_UNAUTHORIZED
        assert checked.is_usable is False

    asyncio.run(_with_db(scenario))


def test_check_health_without_refresh_token_stays_unauthorized() -> None:
    """Без ключа обновления (старый кабинет) — прежнее поведение, без попыток обновить."""

    async def scenario(session: AsyncSession) -> None:
        cfg = _settings()
        view = await add_account(session, 1, TOKEN, settings=cfg)  # без refresh_token
        monkeypatch = pytest.MonkeyPatch()
        _fail_identity(monkeypatch, InvalidTokenError("rejected"))
        try:
            checked = await check_health(session, 1, view.id, settings=cfg)
        finally:
            monkeypatch.undo()
        assert checked.health == HEALTH_UNAUTHORIZED

    asyncio.run(_with_db(scenario))
