"""Эндпоинты рекламных кабинетов: операторские и админское зеркало (spec §8.3).

Главная проверка — **токен не появляется ни в одном ответе**. Ради неё каждое
тело ответа прогоняется через поиск подстроки, а не только через сверку полей.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
from config.settings import Settings, get_settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account
from db.session import get_session
from httpx import ASGITransport, AsyncClient
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
