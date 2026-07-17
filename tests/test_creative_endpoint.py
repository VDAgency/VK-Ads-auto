"""Тесты эндпоинта приёма креатива `POST /briefs/{id}/creative` (триггер запуска РК)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from config.settings import Settings
from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_VALID = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "vk_ad_cabinet_id": "13410929",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "личная страница",
}
_IMAGE_B64 = base64.b64encode(b"\xff\xd8\xff\x00" * 100).decode("ascii")


@pytest.fixture(autouse=True)
def _stub_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Детерминизм: креативы во временный каталог, запуск — заглушка (без VK-токена)."""
    settings = Settings(_env_file=None, creatives_dir=str(tmp_path))
    monkeypatch.setattr("services.creative_store.get_settings", lambda: settings)
    monkeypatch.setattr("services.launch_service.get_settings", lambda: settings)


async def _with_client(scenario: Callable[[AsyncClient], Awaitable[T]], *, seed: bool = True) -> T:
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
            session.add(Client(id=1, account_id=1, full_name="Вячеслав", email="v@example.com"))
            session.add(
                Brief(id=1, account_id=1, client_id=1, variant="individual", payload=dict(_VALID))
            )
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


def test_upload_creative_prepares_campaign_and_marks_card() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст объявления",
            },
        )
        assert resp.status_code == 201, resp.text
        card = await client.get("/api/v1/briefs/1")
        return {"upload": resp.json(), "card": card.json()}

    result = asyncio.run(_with_client(scenario))
    assert result["upload"]["campaign_status"] == "prepared"
    assert "подготовлена" in result["upload"]["message"]
    # Карточка теперь отражает загруженный креатив и статус кампании.
    assert result["card"]["has_creative"] is True
    assert result["card"]["campaign_status"] == "prepared"


def test_upload_creative_rejects_small_image() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={"media_b64": _IMAGE_B64, "media_type": "photo", "width": 100, "height": 100},
        )
        return resp.status_code

    # Минимальный размер 600×600 не выдержан → 422.
    assert asyncio.run(_with_client(scenario)) == 422


def test_upload_creative_invalid_base64() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={"media_b64": "not-base64!!!", "media_type": "photo", "width": 800, "height": 800},
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 422


def test_upload_creative_404_when_brief_missing() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/999/creative",
            json={"media_b64": _IMAGE_B64, "media_type": "photo", "width": 800, "height": 800},
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario, seed=False)) == 404
