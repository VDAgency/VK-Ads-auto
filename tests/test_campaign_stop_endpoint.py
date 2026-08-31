"""Тесты операторского эндпоинта `POST /api/v1/campaigns/{id}/stop`.

Канал остановки: роутер → сервис → адаптер площадки. SQL в роутере нет,
скоуп тенанта проверяется сервисом (чужая кампания → 404).
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.app import create_app
from db.base import Base
from db.models import Account, Campaign
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


async def _call(path: str, *, account_id: int = 1) -> tuple[int, dict[str, Any], str | None]:
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
        session.add(Account(id=2, name="other"))
        session.add(
            Campaign(
                id=1,
                account_id=account_id,
                brief_id=1,
                status="launched",
                objective="socialengagement",
                external_id="stub-campaign-1",
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
        response = await client.post(path)

    async with maker() as session:
        campaign = await session.get(Campaign, 1)
        status = campaign.status if campaign is not None else None
    await engine.dispose()
    return response.status_code, response.json(), status


def test_stop_sets_campaign_status() -> None:
    code, data, status = asyncio.run(_call("/api/v1/campaigns/1/stop"))
    assert code == 200
    # `external_id` в ответе нужен боту: по нему видно, была ли кампания на площадке.
    assert data == {"campaign_id": 1, "status": "stopped", "external_id": "stub-campaign-1"}
    assert status == "stopped"


def test_stop_unknown_campaign_is_404() -> None:
    code, data, _ = asyncio.run(_call("/api/v1/campaigns/777/stop"))
    assert code == 404
    assert data["detail"] == "campaign_not_found"


def test_stop_does_not_cross_tenants() -> None:
    # Кампания чужого тенанта не видна: 404 и статус не меняется.
    code, _, status = asyncio.run(_call("/api/v1/campaigns/1/stop", account_id=2))
    assert code == 404
    assert status == "launched"


def test_stop_path_is_closed_on_ingress() -> None:
    # Снаружи операторский путь обязан отдавать 404 (Caddy), как соседние ресурсы.
    # С аудита 2026-09-01 периметр — белый список: путь закрыт тем, что его в
    # этом списке нет (было наоборот — перечислялись закрытые пути).
    from tests.test_ingress_trust_boundary import _caddy_matcher_patterns, _caddy_path_matches

    patterns = _caddy_matcher_patterns("public_api")
    assert not any(_caddy_path_matches(p, "/api/v1/campaigns/1/stop") for p in patterns)
