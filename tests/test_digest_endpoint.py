"""Тесты операторского эндпоинта `POST /api/v1/stats/digest` (задача 3А).

`dry_run=true` — только собрать текст, оператору ничего не отправлять (ручная
проверка воркфлоу без спама в Telegram); иначе — ровно один вызов
`notify_operator`. Путь закрыт снаружи так же, как `/api/v1/stats/sync`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Campaign, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.notifier import register_operator_notifier, reset_operator_notifier
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


async def _call(*, dry_run: bool | None = None, with_campaign: bool = True) -> tuple[int, Any]:
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
        session.add(Client(id=1, account_id=1, full_name="Иван Иванов"))
        session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
        if with_campaign:
            session.add(
                Campaign(
                    id=1,
                    account_id=1,
                    brief_id=1,
                    client_id=1,
                    status="launched",
                    objective="socialengagement",
                    external_id="stub-campaign-1",
                    spec_json={"name": "Подписчики · Иван Иванов"},
                )
            )
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    params = {} if dry_run is None else {"dry_run": str(dry_run).lower()}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/stats/digest", params=params)
    await engine.dispose()
    return response.status_code, response.json()


def test_dry_run_returns_text_and_does_not_notify() -> None:
    messages: list[str] = []

    async def capture(text: str) -> None:
        messages.append(text)

    register_operator_notifier(capture)
    try:
        code, data = asyncio.run(_call(dry_run=True))
    finally:
        reset_operator_notifier()

    assert code == 200
    assert data["sent"] is False
    assert data["campaigns"] == 1
    assert isinstance(data["text"], str)
    assert "Подписчики · Иван Иванов" in data["text"]
    assert messages == []


def test_real_run_notifies_operator_exactly_once() -> None:
    messages: list[str] = []

    async def capture(text: str) -> None:
        messages.append(text)

    register_operator_notifier(capture)
    try:
        code, data = asyncio.run(_call(dry_run=False))
    finally:
        reset_operator_notifier()

    assert code == 200
    assert data == {"sent": True, "campaigns": 1, "text": None}
    assert len(messages) == 1
    assert "Подписчики · Иван Иванов" in messages[0]


def test_default_without_dry_run_param_sends() -> None:
    """Без явного `dry_run` эндпоинт ведёт себя как боевой прогон n8n."""
    messages: list[str] = []

    async def capture(text: str) -> None:
        messages.append(text)

    register_operator_notifier(capture)
    try:
        code, data = asyncio.run(_call(dry_run=None, with_campaign=False))
    finally:
        reset_operator_notifier()

    assert code == 200
    assert data == {"sent": True, "campaigns": 0, "text": None}
    assert len(messages) == 1
    assert "Активных кампаний нет" in messages[0]


def test_digest_path_is_closed_on_ingress() -> None:
    from tests.test_ingress_trust_boundary import _caddy_matcher_patterns, _caddy_path_matches

    patterns = _caddy_matcher_patterns("public_api")
    assert not any(_caddy_path_matches(p, "/api/v1/stats/digest") for p in patterns)
