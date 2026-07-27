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
from db.models import Account, AdAccount
from db.repositories import get_ad_account
from pydantic import SecretStr
from services.ad_accounts import (
    ADVERTISER_THIRD_PARTY,
    HEALTH_ERROR,
    HEALTH_HEALTHY,
    HEALTH_UNAUTHORIZED,
    AccountNotFoundError,
    DuplicateAccountError,
    TokenUnavailableError,
    add_account,
    check_health,
    delete_account,
    list_accounts,
    mark_unauthorized,
    resolve_token,
    seed_from_env,
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


def _settings(*, key: str | None = None, token: str = "", ttl: int = 15) -> Settings:
    return Settings(
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode() if key is None else key),
        vk_ads_access_token=SecretStr(token),
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
        session.add(Account(id=2, name="tenant-two"))
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
