"""Тесты эндпоинта `GET /api/v1/invites` (PR-B)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from core.app import create_app
from db.base import Base
from db.models import Account, Operator
from db.repositories import create_brief_invite, mark_invite_received_if_sent, mark_invite_sent
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


async def _get_invites(status: str) -> tuple[int, dict[str, Any]]:
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
        session.add(Operator(id=10, account_id=1, telegram_id=555, full_name="Оператор"))
        await session.commit()
        # Один ждём (sent), один пришёл (received).
        waiting = await create_brief_invite(
            session, 1, 10, "wait", "individual", "email", "wait@it.c", "email"
        )
        await mark_invite_sent(session, waiting.id)
        waiting.delivered_at = datetime.now(UTC) - timedelta(days=2)
        got = await create_brief_invite(
            session, 1, 10, "got", "community", "telegram", "@ivan", "telegram"
        )
        await mark_invite_sent(session, got.id)
        await mark_invite_received_if_sent(session, got.id)
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/invites", params={"status": status})
    await engine.dispose()
    return response.status_code, response.json()


def test_pending_returns_only_sent() -> None:
    code, data = asyncio.run(_get_invites("pending"))
    assert code == 200
    contacts = [i["contact"] for i in data["items"]]
    assert contacts == ["wait@it.c"]
    assert data["items"][0]["waiting_days"] >= 2


def test_recent_returns_received() -> None:
    code, data = asyncio.run(_get_invites("recent"))
    assert code == 200
    contacts = [i["contact"] for i in data["items"]]
    assert contacts == ["@ivan"]


def test_default_status_is_pending() -> None:
    engine_code, _ = asyncio.run(_get_invites("pending"))
    assert engine_code == 200


def test_invalid_status_rejected() -> None:
    code, _ = asyncio.run(_get_invites("garbage"))
    assert code == 422
