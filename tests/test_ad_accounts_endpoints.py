"""Эндпоинты рекламных кабинетов: операторские и админское зеркало (spec §8.3).

Главная проверка — **токен не появляется ни в одном ответе**. Ради неё каждое
тело ответа прогоняется через поиск подстроки, а не только через сверку полей.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
import services.agency_cabinets as agency_cabinets
from config.settings import Settings, get_settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from integrations.vk_api import VkAgencyClient, VkAgencyClientForbidden, VkApiAdapter
from integrations.vk_oauth import (
    VkOAuthInvalidCredentials,
    VkOAuthRejected,
    VkOAuthToken,
    VkOAuthUnavailable,
)
from pydantic import SecretStr
from services.admin_auth import generate_admin_session
from services.vk_identity import InvalidTokenError, VkIdentity, VkUnreachableError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_SECRET = get_settings().secret_key.get_secret_value()

IDENTITY = VkIdentity(
    external_id="10000001",
    username="a1b2c3d4e5@agency_client",
    title="Студия «Пример»",
    status="active",
)


@pytest.fixture(autouse=True)
def _mock_vk_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK замокан, ключ шифрования подставлен: тесты не ходят в сеть."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return "12345.67"

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)
    # Ключ фиксируем на весь тест: сгенерируй его внутри lambda — и каждый вызов
    # настроек получал бы новый ключ, а расшифровка ломалась бы на ровном месте.
    settings = Settings(_env_file=None, vk_ads_secret_key=SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(ad_accounts, "get_settings", lambda: settings)


async def _with_api(scenario: Callable[[AsyncClient], Awaitable[T]], *, authed: bool = False) -> T:
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
        session.add(Client(id=100, account_id=1, full_name="Клиент 1"))
        session.add(Client(id=200, account_id=1, full_name="Клиент 2"))
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        if authed:
            client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        result = await scenario(client)
    await engine.dispose()
    return result


def _body(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"token": TOKEN, **(payload or {})}


# --- операторский роутер ------------------------------------------------------


def test_post_creates_account_and_hides_token() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json=_body())
        assert resp.status_code == 201, resp.text
        assert TOKEN not in resp.text
        data = resp.json()
        assert data["title"] == "Студия «Пример»"
        assert data["external_id"] == "10000001"
        assert data["token_tail"] == TOKEN[-4:]
        assert data["is_usable"] is True
        assert "token" not in data

    asyncio.run(_with_api(scenario))


def test_list_returns_created_account_without_token() -> None:
    async def scenario(client: AsyncClient) -> None:
        await client.post("/api/v1/ad-accounts", json=_body())
        resp = await client.get("/api/v1/ad-accounts")
        assert resp.status_code == 200
        assert TOKEN not in resp.text
        assert len(resp.json()["items"]) == 1

    asyncio.run(_with_api(scenario))


def test_list_is_empty_initially() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.get("/api/v1/ad-accounts")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    asyncio.run(_with_api(scenario))


def test_post_invalid_token_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(token: str, **_: object) -> VkIdentity:
        raise InvalidTokenError("rejected")

    monkeypatch.setattr(ad_accounts, "fetch_identity", broken)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json=_body())
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_token"

    asyncio.run(_with_api(scenario))


def test_post_vk_down_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """503, а не 400: про сам токен ничего не известно, стоит повторить."""

    async def broken(token: str, **_: object) -> VkIdentity:
        raise VkUnreachableError("timeout")

    monkeypatch.setattr(ad_accounts, "fetch_identity", broken)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json=_body())
        assert resp.status_code == 503
        assert resp.json()["detail"] == "vk_unreachable"

    asyncio.run(_with_api(scenario))


def test_post_duplicate_returns_409() -> None:
    async def scenario(client: AsyncClient) -> None:
        assert (await client.post("/api/v1/ad-accounts", json=_body())).status_code == 201
        resp = await client.post("/api/v1/ad-accounts", json=_body())
        assert resp.status_code == 409
        assert resp.json()["detail"] == "duplicate_account"

    asyncio.run(_with_api(scenario))


def test_post_without_encryption_key_returns_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ad_accounts,
        "get_settings",
        lambda: Settings(_env_file=None, vk_ads_secret_key=SecretStr("")),
    )

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json=_body())
        assert resp.status_code == 500
        assert resp.json()["detail"] == "encryption_key_missing"

    asyncio.run(_with_api(scenario))


def test_post_rejects_empty_token() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json={"token": ""})
        assert resp.status_code == 422

    asyncio.run(_with_api(scenario))


def test_third_party_fields_round_trip() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/ad-accounts",
            json=_body(
                {
                    "advertiser_kind": "third_party",
                    "advertiser_name": "ООО «Ромашка»",
                    "advertiser_inn": "7701234567",
                }
            ),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["advertiser_kind"] == "third_party"
        assert data["advertiser_name"] == "ООО «Ромашка»"
        assert data["advertiser_inn"] == "7701234567"

    asyncio.run(_with_api(scenario))


def test_check_marks_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(client: AsyncClient) -> None:
        created = await client.post("/api/v1/ad-accounts", json=_body())
        account_id = created.json()["id"]

        async def broken(token: str, **_: object) -> VkIdentity:
            raise InvalidTokenError("revoked")

        monkeypatch.setattr(ad_accounts, "fetch_identity", broken)
        resp = await client.post(f"/api/v1/ad-accounts/{account_id}/check")
        assert resp.status_code == 200
        assert resp.json()["health"] == "unauthorized"
        assert resp.json()["is_usable"] is False

    asyncio.run(_with_api(scenario))


def test_check_missing_returns_404() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/999/check")
        assert resp.status_code == 404

    asyncio.run(_with_api(scenario))


def test_delete_removes_from_list() -> None:
    async def scenario(client: AsyncClient) -> None:
        created = await client.post("/api/v1/ad-accounts", json=_body())
        account_id = created.json()["id"]
        resp = await client.delete(f"/api/v1/ad-accounts/{account_id}")
        assert resp.status_code == 204
        assert (await client.get("/api/v1/ad-accounts")).json()["items"] == []

    asyncio.run(_with_api(scenario))


def test_delete_missing_returns_404() -> None:
    async def scenario(client: AsyncClient) -> None:
        assert (await client.delete("/api/v1/ad-accounts/999")).status_code == 404

    asyncio.run(_with_api(scenario))


# --- привязка к клиенту (spec 2026-08-25 §1.1) --------------------------------


def test_post_without_client_is_common() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json=_body())
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["client_id"] is None
        assert data["client_name"] is None

    asyncio.run(_with_api(scenario))


def test_post_with_client_binds_and_names_it() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json=_body({"client_id": 100}))
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["client_id"] == 100
        assert data["client_name"] == "Клиент 1"

    asyncio.run(_with_api(scenario))


def test_post_unknown_client_returns_422() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts", json=_body({"client_id": 999}))
        assert resp.status_code == 422
        assert resp.json()["detail"] == "client_not_found"

    asyncio.run(_with_api(scenario))


def test_list_with_client_id_narrows_to_common_and_own(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(client: AsyncClient) -> None:
        await client.post("/api/v1/ad-accounts", json=_body())  # общий

        async def other_identity(token: str, **_: object) -> VkIdentity:
            return VkIdentity("222", "b", "Клиентский", "active")

        # Второй кабинет — с другим external_id, чтобы не словить duplicate_account.
        monkeypatch.setattr(ad_accounts, "fetch_identity", other_identity)
        bound = await client.post(
            "/api/v1/ad-accounts", json=_body({"client_id": 100, "token": "another-token"})
        )
        assert bound.status_code == 201, bound.text

        for_owner = await client.get("/api/v1/ad-accounts", params={"client_id": 100})
        assert {a["external_id"] for a in for_owner.json()["items"]} == {"10000001", "222"}

        for_other = await client.get("/api/v1/ad-accounts", params={"client_id": 200})
        assert {a["external_id"] for a in for_other.json()["items"]} == {"10000001"}

    asyncio.run(_with_api(scenario))


def test_patch_client_binds_existing_account() -> None:
    async def scenario(client: AsyncClient) -> None:
        created = await client.post("/api/v1/ad-accounts", json=_body())
        account_id = created.json()["id"]

        resp = await client.patch(
            f"/api/v1/ad-accounts/{account_id}/client", json={"client_id": 100}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["client_id"] == 100
        assert resp.json()["client_name"] == "Клиент 1"

        freed = await client.patch(
            f"/api/v1/ad-accounts/{account_id}/client", json={"client_id": None}
        )
        assert freed.json()["client_id"] is None
        assert freed.json()["client_name"] is None

    asyncio.run(_with_api(scenario))


def test_patch_client_missing_account_returns_404() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.patch("/api/v1/ad-accounts/999/client", json={"client_id": 100})
        assert resp.status_code == 404

    asyncio.run(_with_api(scenario))


def test_patch_client_unknown_client_returns_422() -> None:
    async def scenario(client: AsyncClient) -> None:
        created = await client.post("/api/v1/ad-accounts", json=_body())
        account_id = created.json()["id"]
        resp = await client.patch(
            f"/api/v1/ad-accounts/{account_id}/client", json={"client_id": 999}
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "client_not_found"

    asyncio.run(_with_api(scenario))


# --- админское зеркало --------------------------------------------------------


def test_admin_requires_authentication() -> None:
    """Без сессии админки управлять токенами нельзя."""

    async def scenario(client: AsyncClient) -> None:
        assert (await client.get("/api/v1/admin/ad-accounts")).status_code == 401
        assert (await client.post("/api/v1/admin/ad-accounts", json=_body())).status_code == 401
        assert (await client.delete("/api/v1/admin/ad-accounts/1")).status_code == 401

    asyncio.run(_with_api(scenario, authed=False))


def test_admin_can_add_list_and_delete() -> None:
    """Веб обязан уметь ровно то же, что бот (требование задачи)."""

    async def scenario(client: AsyncClient) -> None:
        created = await client.post("/api/v1/admin/ad-accounts", json=_body())
        assert created.status_code == 201, created.text
        assert TOKEN not in created.text
        account_id = created.json()["id"]

        listed = await client.get("/api/v1/admin/ad-accounts")
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1

        checked = await client.post(f"/api/v1/admin/ad-accounts/{account_id}/check")
        assert checked.status_code == 200
        assert checked.json()["health"] == "healthy"

        assert (await client.delete(f"/api/v1/admin/ad-accounts/{account_id}")).status_code == 204
        assert (await client.get("/api/v1/admin/ad-accounts")).json()["items"] == []

    asyncio.run(_with_api(scenario, authed=True))


def test_bot_and_web_see_the_same_accounts() -> None:
    """Один источник правды: добавленное в вебе видно в боте и наоборот."""

    async def scenario(client: AsyncClient) -> None:
        await client.post("/api/v1/admin/ad-accounts", json=_body())
        operator_view = await client.get("/api/v1/ad-accounts")
        assert len(operator_view.json()["items"]) == 1

    asyncio.run(_with_api(scenario, authed=True))


# --- заведение клиенту кабинета через агентский API (B2/B3) -------------------

_VK_CLIENT = VkAgencyClient(
    client_id="777",
    username="new-client@agency_client",
    ad_account_id="10000002",
    balance="0",
    status="active",
    access_type="full_access",
)

_ISSUED_TOKEN = VkOAuthToken(
    access_token=SecretStr("fresh-access-token-000000000000"),
    refresh_token=SecretStr("fresh-refresh-token-000000000000"),
    expires_at=datetime.now(UTC) + timedelta(hours=24),
)

# Токен СОБСТВЕННОГО аккаунта агентства (`grant_type=client_credentials`) —
# им, а не токеном из окружения, теперь строится вызывающий адаптер (правка
# после ревью, см. services/agency_cabinets.py).
_OWN_TOKEN = VkOAuthToken(
    access_token=SecretStr("own-account-token-00000000000000"),
    refresh_token=SecretStr("own-account-refresh-00000000000000"),
    expires_at=datetime.now(UTC) + timedelta(hours=24),
)


def _agency_body(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "client_id": 100,
        "full_name": "Иван Иванов",
        "tax_id": "770123456789",
        **(payload or {}),
    }


def _mock_agency_settings(monkeypatch: pytest.MonkeyPatch, *, confirmed: bool = True) -> None:
    """Настройки для агентской операции: свой ключ у `agency_cabinets.get_settings`,
    отдельный от общего `ad_accounts.get_settings` (координата B2: последний тут не
    участвует — `create_client_cabinet` пробрасывает уже разрешённые настройки в
    `add_account` явным `settings=`). `vk_ads_access_token` не задан — он тут больше
    ни при чём: удостоверение агентства строится ключами приложения (ниже).
    """
    settings = Settings(
        _env_file=None,
        vk_agency_confirmed=confirmed,
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode()),
        vk_ads_client_id=SecretStr("app-id"),
        vk_ads_client_secret=SecretStr("app-secret"),
    )
    monkeypatch.setattr(agency_cabinets, "get_settings", lambda: settings)

    async def issue_own(client_id: str, client_secret: str, **_: object) -> VkOAuthToken:
        return _OWN_TOKEN

    monkeypatch.setattr(agency_cabinets, "request_own_account_token", issue_own)


async def _fake_create_agency_client(self: object, **_: object) -> VkAgencyClient:
    return _VK_CLIENT


def test_post_agency_cabinet_creates_and_binds_account(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_agency_settings(monkeypatch)
    monkeypatch.setattr(VkApiAdapter, "create_agency_client", _fake_create_agency_client)

    async def issue(*args: object, **kwargs: object) -> VkOAuthToken:
        return _ISSUED_TOKEN

    monkeypatch.setattr(agency_cabinets, "request_agency_client_token", issue)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 201, resp.text
        assert "fresh-access-token" not in resp.text
        data = resp.json()
        assert data["client_id"] == 100
        assert data["client_name"] == "Клиент 1"
        assert data["advertiser_kind"] == "third_party"
        assert data["advertiser_name"] == "Иван Иванов"
        assert data["advertiser_inn"] == "770123456789"

    asyncio.run(_with_api(scenario))


def test_post_agency_cabinet_disabled_flag_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_agency_settings(monkeypatch, confirmed=False)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 403
        assert resp.json()["detail"] == "agency_disabled"

    asyncio.run(_with_api(scenario))


def test_post_agency_cabinet_missing_tax_id_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_agency_settings(monkeypatch)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/ad-accounts/agency-cabinets", json=_agency_body({"tax_id": ""})
        )
        assert resp.status_code == 422

    asyncio.run(_with_api(scenario))


def test_post_agency_cabinet_unknown_client_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_agency_settings(monkeypatch)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/ad-accounts/agency-cabinets", json=_agency_body({"client_id": 999})
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "client_not_found"

    asyncio.run(_with_api(scenario))


def test_post_agency_cabinet_vk_forbidden_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_agency_settings(monkeypatch)

    async def forbidden(self: object, **_: object) -> VkAgencyClient:
        raise VkAgencyClientForbidden("agency status not confirmed")

    monkeypatch.setattr(VkApiAdapter, "create_agency_client", forbidden)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 403
        assert resp.json()["detail"] == "vk_agency_not_confirmed"

    asyncio.run(_with_api(scenario))


def test_post_agency_cabinet_token_issuance_failure_returns_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_agency_settings(monkeypatch)
    monkeypatch.setattr(VkApiAdapter, "create_agency_client", _fake_create_agency_client)

    async def issue(*args: object, **kwargs: object) -> VkOAuthToken:
        raise VkOAuthRejected("token limit exceeded")

    monkeypatch.setattr(agency_cabinets, "request_agency_client_token", issue)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"] == "token_issuance_failed"
        assert resp.json()["detail"]["vk_client_id"] == "777"

    asyncio.run(_with_api(scenario))


# --- ревью ветки §3: отказы выпуска СОБСТВЕННОГО токена агентства ----------------
#
# Шаг ДО создания клиента в VK (`_build_agency_adapter` внутри
# `create_client_cabinet`) — раньше эти три исключения не перехватывались ни
# одним блоком роутера, кроме `VkOAuthNotConfigured` (пустые учётные данные),
# и наружу уходила голая внутренняя ошибка (500 без опознанного кода).


def test_post_agency_cabinet_own_token_invalid_credentials_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_agency_settings(monkeypatch)

    async def issue_own(client_id: str, client_secret: str, **_: object) -> VkOAuthToken:
        raise VkOAuthInvalidCredentials("invalid_client")

    monkeypatch.setattr(agency_cabinets, "request_own_account_token", issue_own)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 500
        assert resp.json()["detail"] == "vk_oauth_invalid_credentials"

    asyncio.run(_with_api(scenario))


def test_post_agency_cabinet_own_token_rejected_returns_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_agency_settings(monkeypatch)

    async def issue_own(client_id: str, client_secret: str, **_: object) -> VkOAuthToken:
        raise VkOAuthRejected("token limit exceeded")

    monkeypatch.setattr(agency_cabinets, "request_own_account_token", issue_own)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 502
        assert resp.json()["detail"] == "vk_oauth_rejected"

    asyncio.run(_with_api(scenario))


def test_post_agency_cabinet_own_token_unavailable_after_retry_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_agency_settings(monkeypatch)

    async def issue_own(client_id: str, client_secret: str, **_: object) -> VkOAuthToken:
        raise VkOAuthUnavailable("still down")

    monkeypatch.setattr(agency_cabinets, "request_own_account_token", issue_own)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 503
        assert resp.json()["detail"] == "vk_oauth_unavailable"

    asyncio.run(_with_api(scenario))


# --- админское зеркало заведения кабинета (B2/B3) -----------------------------


def test_admin_agency_cabinet_requires_admin_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_agency_settings(monkeypatch)

    async def scenario(client: AsyncClient) -> int:
        resp = await client.post("/api/v1/admin/ad-accounts/agency-cabinets", json=_agency_body())
        return resp.status_code

    assert asyncio.run(_with_api(scenario, authed=False)) == 401


def test_admin_agency_cabinet_mirrors_the_operator_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Веб обязан уметь ровно то же, что бот: тот же сервис, тот же результат."""
    _mock_agency_settings(monkeypatch)
    monkeypatch.setattr(VkApiAdapter, "create_agency_client", _fake_create_agency_client)

    async def issue(*args: object, **kwargs: object) -> VkOAuthToken:
        return _ISSUED_TOKEN

    monkeypatch.setattr(agency_cabinets, "request_agency_client_token", issue)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/admin/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 201, resp.text
        assert "fresh-access-token" not in resp.text
        data = resp.json()
        assert data["client_id"] == 100
        assert data["advertiser_kind"] == "third_party"
        assert data["advertiser_name"] == "Иван Иванов"

        # Кабинет, заведённый через веб, виден и оператору (общий источник правды).
        operator_view = await client.get("/api/v1/ad-accounts")
        assert len(operator_view.json()["items"]) == 1

    asyncio.run(_with_api(scenario, authed=True))


def test_admin_agency_cabinet_disabled_flag_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Предохранитель `vk_agency_confirmed` веб честно транслирует, не обходит его."""
    _mock_agency_settings(monkeypatch, confirmed=False)

    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/admin/ad-accounts/agency-cabinets", json=_agency_body())
        assert resp.status_code == 403
        assert resp.json()["detail"] == "agency_disabled"

    asyncio.run(_with_api(scenario, authed=True))
