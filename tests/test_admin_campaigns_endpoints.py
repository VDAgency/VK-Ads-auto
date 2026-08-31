"""Тесты веб-зеркал кампаний/кабинетов/статистики (`core/api/v1/admin_campaigns.py`).

Каждый эндпоинт — тонкое зеркало операторского пути (`cabinets.py`/`stats.py`/
`campaigns.py`) под `require_admin`: без admin-сессии 401, с сессией — тот же
сервис и та же модель ответа, что и у бота.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
import services.cabinet_stats as cabinet_stats_module
from config.settings import Settings, get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Campaign, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.admin_auth import generate_admin_session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_SECRET = get_settings().secret_key.get_secret_value()


def _mock_settings(*, mock_stats_enabled: bool) -> Settings:
    return Settings(_env_file=None, mock_stats_enabled=mock_stats_enabled)


async def _with_admin(
    scenario: Callable[[AsyncClient], Awaitable[T]],
    *,
    authed: bool = True,
    extra_setup: Callable[[AsyncSession], Awaitable[None]] | None = None,
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
        if extra_setup is not None:
            await extra_setup(session)
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


async def _with_launched_campaign(session: AsyncSession) -> None:
    session.add(Client(id=1, account_id=1, full_name="Вячеслав"))
    session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
    session.add(
        Campaign(
            id=1,
            account_id=1,
            brief_id=1,
            status="launched",
            objective="socialengagement",
            external_id="stub-campaign-1",
        )
    )


# --- GET /admin/cabinets ------------------------------------------------------


def test_cabinets_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        return (await client.get("/api/v1/admin/cabinets")).status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_cabinets_lists_real_cabinet_from_campaign() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/cabinets")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario, extra_setup=_with_launched_campaign))
    assert [i["id"] for i in data["items"]] == ["stub-campaign-1"]
    assert data["items"][0]["is_mock"] is False


# --- GET /admin/cabinets/{id}/stats --------------------------------------------


def test_cabinet_stats_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/admin/cabinets/demo-1/stats")
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_cabinet_stats_is_honest_zero_without_mock_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cabinet_stats_module, "get_settings", lambda: _mock_settings(mock_stats_enabled=False)
    )

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/cabinets/demo-1/stats", params={"period": "all"})
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["is_mock"] is False
    assert data["shows"] == 0.0


# --- POST /admin/cabinets/{id}/stats/sync --------------------------------------


def test_cabinet_sync_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post("/api/v1/admin/cabinets/stub-campaign-1/stats/sync")
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_cabinet_sync_updates_matching_campaign() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post("/api/v1/admin/cabinets/stub-campaign-1/stats/sync")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario, extra_setup=_with_launched_campaign))
    assert data["outcome"] == "updated"
    assert data["ok"] is True


# --- POST /admin/stats/sync -----------------------------------------------------


def test_stats_sync_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        return (await client.post("/api/v1/admin/stats/sync")).status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_stats_sync_persists_stat_for_active_campaigns() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post("/api/v1/admin/stats/sync")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario, extra_setup=_with_launched_campaign))
    assert data == {"synced": 1, "failed": 0, "results": {"1": "ok"}}


# --- POST /admin/campaigns/{id}/stop --------------------------------------------


def test_campaign_stop_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        return (await client.post("/api/v1/admin/campaigns/1/stop")).status_code

    result = asyncio.run(_with_admin(scenario, authed=False, extra_setup=_with_launched_campaign))
    assert result == 401


def test_campaign_stop_sets_campaign_status() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post("/api/v1/admin/campaigns/1/stop")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario, extra_setup=_with_launched_campaign))
    assert data == {"campaign_id": 1, "status": "stopped", "external_id": "stub-campaign-1"}


def test_campaign_stop_unknown_campaign_is_404() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post("/api/v1/admin/campaigns/777/stop")
        assert resp.json()["detail"] == "campaign_not_found"
        return resp.status_code

    assert asyncio.run(_with_admin(scenario)) == 404
