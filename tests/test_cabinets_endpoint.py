"""Тесты эндпоинтов `/api/v1/cabinets` (PR-C): list + detail + периоды + is_mock."""

from __future__ import annotations

import asyncio
from typing import Any

from core.app import create_app
from db.base import Base
from db.models import Account, Stat
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


async def _call(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    with_stats: bool = False,
) -> tuple[int, dict[str, Any]]:
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
        if with_stats:
            session.add(Stat(account_id=1, campaign_id="camp-1", shows=1000, clicks=50, spent=100))
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, params=params)
    await engine.dispose()
    return response.status_code, response.json()


def test_list_returns_mock_cabinets_when_empty() -> None:
    code, data = asyncio.run(_call("/api/v1/cabinets"))
    assert code == 200
    assert data["items"]
    assert all(item["is_mock"] for item in data["items"])


def test_list_returns_real_when_stats_exist() -> None:
    code, data = asyncio.run(_call("/api/v1/cabinets", with_stats=True))
    assert code == 200
    ids = [i["id"] for i in data["items"]]
    assert ids == ["camp-1"]
    assert data["items"][0]["is_mock"] is False


def test_stats_detail_real() -> None:
    code, data = asyncio.run(
        _call("/api/v1/cabinets/camp-1/stats", {"period": "all"}, with_stats=True)
    )
    assert code == 200
    assert data["shows"] == 1000
    assert data["ctr"] == 5.0
    assert data["cpc"] == 2.0
    assert data["is_mock"] is False


def test_stats_detail_mock() -> None:
    code, data = asyncio.run(_call("/api/v1/cabinets/demo-1/stats", {"period": "month"}))
    assert code == 200
    assert data["is_mock"] is True
    assert data["shows"] > 0


def test_invalid_period_rejected() -> None:
    code, _ = asyncio.run(_call("/api/v1/cabinets/demo-1/stats", {"period": "year"}))
    assert code == 422
