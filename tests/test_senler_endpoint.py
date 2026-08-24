"""Эндпоинт привязки токена сообщества (`core/api/v1/senler.py`, B2).

Оператор больше не вводит id сообщества руками: `groups.getById` без
`group_id` называет сообщество по одному лишь токену, ДО сохранения в БД —
не смогли опознать -> токен не сохраняем, честный отказ вместо мнимого успеха
(CLAUDE.md §7). Токен по-прежнему не должен появляться ни в одном ответе.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.senler as senler_endpoint
import pytest
from config.settings import Settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.community_tokens import get_decrypted_token
from db.models import Account
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from integrations.vk_community import CommunityIdentity, VkCommunityUnreachable
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-community-access-token-000000000000"
IDENTITY = CommunityIdentity(id="228817082", screen_name="djbeauty", name="DJ BEAUTY")
_KEY = Fernet.generate_key().decode()


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


async def _with_api(
    scenario: Callable[[AsyncClient, async_sessionmaker[AsyncSession]], Awaitable[T]],
) -> T:
    """Стенд с работающим FastAPI-приложением и БД. `scenario` получает и HTTP-клиент,
    и `maker` — чтобы после запроса проверить, что реально осело в БД."""
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
        result = await scenario(client, maker)
    await engine.dispose()
    return result


def test_post_identifies_community_and_hides_the_token() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> None:
        resp = await client.post("/api/v1/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text
        assert TOKEN not in resp.text
        data = resp.json()
        assert data["community_id"] == "228817082"
        assert data["community_name"] == "DJ BEAUTY"
        assert data["connected"] is True
        assert "token" not in data

    asyncio.run(_with_api(scenario))


def test_operator_no_longer_needs_to_provide_a_community_id() -> None:
    """Регресс: тело запроса больше не требует `community_id` — id всё равно
    приходит из VK, даже если оператор его и не прислал."""

    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> None:
        resp = await client.post("/api/v1/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text
        assert resp.json()["community_id"] == "228817082"

    asyncio.run(_with_api(scenario))


def test_token_is_saved_readable_under_the_identity_vk_returned() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> str | None:
        resp = await client.post("/api/v1/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text
        async with maker() as session:
            return await get_decrypted_token(session, 1, IDENTITY.id, settings=_settings())

    assert asyncio.run(_with_api(scenario)) == TOKEN


def test_unidentifiable_token_is_rejected_with_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """`groups.getById` не ответил/вернул ошибку — честный отказ, а не мнимый успех."""

    async def broken_identify(token: str, **_: object) -> CommunityIdentity:
        raise VkCommunityUnreachable("boom")

    monkeypatch.setattr(senler_endpoint, "fetch_own_community", broken_identify)

    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> None:
        resp = await client.post("/api/v1/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "community_unreachable"
        assert TOKEN not in resp.text

    asyncio.run(_with_api(scenario))


def test_unidentifiable_token_leaves_no_row_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Опознать не удалось -> в БД не остаётся ничего, что можно было бы найти."""

    async def broken_identify(token: str, **_: object) -> CommunityIdentity:
        raise VkCommunityUnreachable("boom")

    monkeypatch.setattr(senler_endpoint, "fetch_own_community", broken_identify)

    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> str | None:
        resp = await client.post("/api/v1/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 422
        async with maker() as session:
            return await get_decrypted_token(session, 1, IDENTITY.id, settings=_settings())

    assert asyncio.run(_with_api(scenario)) is None


def test_broken_callback_check_still_leaves_the_token_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сообщество опознано, но `groups.getCallbackServers` недоступен — токен
    всё равно сохранён (следующий запуск кампании проверит подключение сам)."""

    async def broken_callback(token: str, community_id: str, **_: object) -> list[dict[str, Any]]:
        raise VkCommunityUnreachable("boom")

    monkeypatch.setattr(senler_endpoint, "fetch_callback_servers", broken_callback)

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> tuple[dict[str, Any], str | None]:
        resp = await client.post("/api/v1/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text
        async with maker() as session:
            token = await get_decrypted_token(session, 1, IDENTITY.id, settings=_settings())
        return resp.json(), token

    data, token = asyncio.run(_with_api(scenario))
    assert data["connected"] is False
    assert token == TOKEN


def test_senler_not_connected_is_reported_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_senler(token: str, community_id: str, **_: object) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(senler_endpoint, "fetch_callback_servers", no_senler)

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        resp = await client.post("/api/v1/senler/community-token", json={"token": TOKEN})
        assert resp.status_code == 201, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_api(scenario))
    assert data["connected"] is False
    assert data["reason"]
