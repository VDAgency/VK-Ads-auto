"""Тесты операторских эндпоинтов брифа: `GET /briefs/{id}` и `PATCH /briefs/{id}`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_PAYLOAD = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "vk_ad_cabinet_id": "13410929",
    "email": "v@example.com",
    "phone": "+79990000000",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
}


async def _seed_brief(session: AsyncSession) -> int:
    client = Client(id=1, account_id=1, full_name="Вячеслав", email="v@example.com")
    session.add(client)
    brief = Brief(
        id=1,
        account_id=1,
        client_id=1,
        variant="individual",
        status="received",
        source="web",
        payload=dict(_PAYLOAD),
    )
    session.add(brief)
    await session.flush()
    return brief.id


async def _with_client(
    scenario: Callable[[AsyncClient], Awaitable[T]],
    *,
    seed: bool = True,
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
        if seed:
            await _seed_brief(session)
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        result = await scenario(client)
    await engine.dispose()
    return result


def test_get_brief_card_returns_numbered_fields() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        response = await client.get("/api/v1/briefs/1")
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        return body

    data = asyncio.run(_with_client(scenario))
    assert data["brief_id"] == 1
    assert data["variant"] == "individual"
    assert data["status"] == "received"
    assert data["client"]["full_name"] == "Вячеслав"
    assert data["client"]["email"] == "v@example.com"
    # Поля пронумерованы с 1, по порядку формы; первое — ФИО.
    assert data["fields"][0] == {"n": 1, "label": "Как обращаться", "value": "Вячеслав"}
    # Незаполненное поле присутствует с пустым значением (стабильная нумерация).
    tg = next(f for f in data["fields"] if f["label"] == "Telegram")
    assert tg["value"] == ""
    assert data["has_creative"] is False
    assert data["campaign_status"] is None


def test_get_brief_card_404_when_missing() -> None:
    async def scenario(client: AsyncClient) -> int:
        response = await client.get("/api/v1/briefs/999")
        return response.status_code

    assert asyncio.run(_with_client(scenario)) == 404


def test_patch_brief_applies_edits_and_persists() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        # Правка поля №1 (ФИО) и №8 (география; после вставки ID кабинета сдвиг +1).
        patch = await client.patch(
            "/api/v1/briefs/1", json={"edits": {"1": "Иван Петров", "8": "Москва"}}
        )
        assert patch.status_code == 200
        # Перечитываем карточку — правки сохранились в БД.
        fresh = await client.get("/api/v1/briefs/1")
        return {"patch": patch.json(), "fresh": fresh.json()}

    result = asyncio.run(_with_client(scenario))
    patch, fresh = result["patch"], result["fresh"]
    assert patch["unknown"] == []
    assert patch["fields"][0]["value"] == "Иван Петров"
    geo = next(f for f in fresh["fields"] if f["label"] == "География")
    assert geo["value"] == "Москва"
    name = next(f for f in fresh["fields"] if f["label"] == "Как обращаться")
    assert name["value"] == "Иван Петров"


def test_patch_brief_reports_unknown_numbers() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        response = await client.patch("/api/v1/briefs/1", json={"edits": {"99": "x"}})
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        return body

    data = asyncio.run(_with_client(scenario))
    assert data["unknown"] == [99]


def test_patch_brief_404_when_missing() -> None:
    async def scenario(client: AsyncClient) -> int:
        response = await client.patch("/api/v1/briefs/999", json={"edits": {"1": "x"}})
        return response.status_code

    assert asyncio.run(_with_client(scenario, seed=False)) == 404
