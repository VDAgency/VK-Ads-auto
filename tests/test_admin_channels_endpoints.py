"""Тесты веб-зеркала токена сообщества Senler (`core/api/v1/admin_channels.py`).

Тонкое зеркало операторского роутера `senler.py` под `require_admin`: без
admin-сессии 401, с сессией — то же опознание сообщества и сохранение/удаление
токена, что и у бота. Токен, как и в операторском роутере, ни в одном ответе
не появляется.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.senler as senler_endpoint
import pytest
from config.settings import Settings, get_settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.community_tokens import get_decrypted_token
from db.models import Account
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from integrations.vk_community import CommunityIdentity, VkCommunityUnreachable
from pydantic import SecretStr
from services.admin_auth import generate_admin_session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-community-access-token-000000000000"
IDENTITY = CommunityIdentity(id="228817082", screen_name="djbeauty", name="DJ BEAUTY")
_KEY = Fernet.generate_key().decode()
_SECRET = get_settings().secret_key.get_secret_value()


def _settings() -> Settings:
    return Settings(_env_file=None, vk_ads_secret_key=SecretStr(_KEY))


@pytest.fixture(autouse=True)
def _mock_vk_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию VK опознаёт сообщество и подтверждает подключённый Senler."""

    async def identify(token: str, **_: object) -> CommunityIdentity:
        return IDENTITY

    async def callback_servers(token: str, community_id: str, **_: object) -> list[dict[str, Any]]:
        return [
            {"title": "Senler", "url": "https://callback.senler.ru/webhook/vk/1", "status": "ok"}
        ]

    monkeypatch.setattr(senler_endpoint, "fetch_own_community", identify)
    monkeypatch.setattr(senler_endpoint, "fetch_callback_servers", callback_servers)

    import db.community_tokens as community_tokens

    monkeypatch.setattr(community_tokens, "get_settings", _settings)


async def _with_admin(
    scenario: Callable[[AsyncClient, async_sessionmaker[AsyncSession]], Awaitable[T]],
    *,
    authed: bool = True,
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
        result = await scenario(client, maker)
    await engine.dispose()
    return result


# --- POST /admin/senler/community-token ---------------------------------------


def test_post_requires_admin_session() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> int:
        resp = await client.post("/api/v1/admin/senler/community-token", json={"token": TOKEN})
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_post_identifies_community_and_hides_the_token() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> None:
        resp = await client.post("/api/v1/admin/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text
        assert TOKEN not in resp.text
        data = resp.json()
        assert data["community_id"] == "228817082"
        assert data["community_name"] == "DJ BEAUTY"
        assert data["connected"] is True
        assert "token" not in data

    asyncio.run(_with_admin(scenario))


def test_post_saves_token_readable_under_returned_identity() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> str | None:
        resp = await client.post("/api/v1/admin/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text
        async with maker() as session:
            return await get_decrypted_token(session, 1, IDENTITY.id, settings=_settings())

    assert asyncio.run(_with_admin(scenario)) == TOKEN


def test_post_unidentifiable_token_is_rejected_with_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_identify(token: str, **_: object) -> CommunityIdentity:
        raise VkCommunityUnreachable("boom")

    monkeypatch.setattr(senler_endpoint, "fetch_own_community", broken_identify)

    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> None:
        resp = await client.post("/api/v1/admin/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "community_unreachable"

    asyncio.run(_with_admin(scenario))


# --- DELETE /admin/senler/community-token -------------------------------------


def test_delete_requires_admin_session() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> int:
        resp = await client.delete(
            "/api/v1/admin/senler/community-token", params={"reference": IDENTITY.screen_name}
        )
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_delete_removes_the_token_by_short_address() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> None:
        resp = await client.post("/api/v1/admin/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text

        resp = await client.delete(
            "/api/v1/admin/senler/community-token", params={"reference": IDENTITY.screen_name}
        )
        assert resp.status_code == 204, resp.text

        async with maker() as session:
            token = await get_decrypted_token(session, 1, IDENTITY.id, settings=_settings())
        assert token is None

    asyncio.run(_with_admin(scenario))


def test_delete_reports_not_found_when_nothing_matches() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> None:
        resp = await client.delete(
            "/api/v1/admin/senler/community-token", params={"reference": "no-such-address"}
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    asyncio.run(_with_admin(scenario))
