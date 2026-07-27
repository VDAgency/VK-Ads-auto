"""Тесты операторского эндпоинта `POST /api/v1/stats/sync`.

Ручной триггер синхронизации: роутер → сервис → адаптеры каналов. SQL в роутере
нет; снаружи путь закрыт на ingress, как соседние операторские ресурсы.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Cabinet, Campaign, Client, Stat
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


async def _call(*, status: str = "launched") -> tuple[int, dict[str, Any], int]:
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
        session.add(Client(id=1, account_id=1, full_name="Вячеслав"))
        session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
        session.add(
            Cabinet(
                id=1,
                account_id=1,
                client_id=1,
                channel="stub",
                ad_object_url="https://vk.com/id1",
            )
        )
        session.add(
            Campaign(
                id=1,
                account_id=1,
                brief_id=1,
                cabinet_id=1,
                status=status,
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
        response = await client.post("/api/v1/stats/sync")

    async with maker() as session:
        saved = len(list((await session.execute(select(Stat))).scalars().all()))
    await engine.dispose()
    return response.status_code, response.json(), saved


def test_sync_returns_summary_and_persists_stat() -> None:
    # Канал заглушки метрик не отдаёт, но срез фиксируется и синк отчитывается ok.
    code, data, saved = asyncio.run(_call())
    assert code == 200
    assert data == {"synced": 1, "failed": 0, "results": {"1": "ok"}}
    assert saved == 1


def test_sync_without_active_campaigns_is_empty() -> None:
    code, data, saved = asyncio.run(_call(status="prepared"))
    assert code == 200
    assert data == {"synced": 0, "failed": 0, "results": {}}
    assert saved == 0


def test_sync_path_is_closed_on_ingress() -> None:
    # Ручной триггер синхронизации — операторский, снаружи Caddy отдаёт 404.
    caddyfile = Path(__file__).resolve().parent.parent / "infra" / "Caddyfile"
    assert "/api/v1/stats/*" in caddyfile.read_text(encoding="utf-8")
