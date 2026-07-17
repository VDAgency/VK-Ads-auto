"""Тесты эндпоинтов данных админки (`/api/v1/admin/*` под `require_admin`)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from config.settings import get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Campaign, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.admin_auth import generate_admin_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_SECRET = get_settings().secret_key.get_secret_value()


async def _with_admin(scenario: Callable[[AsyncClient], Awaitable[T]], *, authed: bool = True) -> T:
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
        session.add(
            Client(
                id=1,
                account_id=1,
                full_name="Вячеслав",
                email="v@example.com",
                phone="+79990000000",
            )
        )
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=1,
                variant="individual",
                status="received",
                payload={"full_name": "Вячеслав", "geo": "Самара"},
            )
        )
        session.add(
            Campaign(
                id=1,
                account_id=1,
                brief_id=1,
                client_id=1,
                status="prepared",
                objective="socialengagement",
                spec_json={},
            )
        )
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


def test_overview_counts() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/overview")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["clients"] == 1
    assert data["campaigns"] == 1


def test_clients_list_with_brief_count() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/clients")
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert len(data["items"]) == 1
    row = data["items"][0]
    assert row["full_name"] == "Вячеслав"
    assert row["brief_count"] == 1


def test_client_detail_with_briefs() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/clients/1")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["email"] == "v@example.com"
    assert len(data["briefs"]) == 1
    assert data["briefs"][0]["id"] == 1


def test_brief_detail_returns_card() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/briefs/1")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["brief_id"] == 1
    assert any(f["label"] == "Как обращаться" for f in data["fields"])


def test_brief_edit_applies() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.patch("/api/v1/admin/briefs/1", json={"edits": {"7": "Москва"}})
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    geo = next(f for f in data["fields"] if f["label"] == "География")
    assert geo["value"] == "Москва"


def test_campaigns_list() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/campaigns")
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert len(data["items"]) == 1
    assert data["items"][0]["status"] == "prepared"
    assert data["items"][0]["client_name"] == "Вячеслав"


def test_admin_endpoints_require_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/admin/clients")
        return resp.status_code

    # Без admin-cookie — 401.
    assert asyncio.run(_with_admin(scenario, authed=False)) == 401
