"""Тесты `POST /api/v1/invites` (PR#4).

Доставка идёт через `build_delivery_router()` из настроек; в тестах каналы не
сконфигурированы (пустые userbot/SMTP), поэтому email/telegram вернут failed с
fallback, а телефон (manual) — sent. Проверяем контракт ответа и запись инвайта.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from config.settings import Settings
from core.app import create_app
from db.base import Base
from db.models import Account
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def _unconfigured_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каналы доставки в этих тестах НЕ сконфигурированы — независимо от локального `.env`.

    Иначе тест зависит от окружения: с реальными SMTP/userbot-кредами в `.env` он
    попытался бы реально коннектиться. Пустые настройки (`_env_file=None`) детерминированы.
    """
    monkeypatch.setattr(
        "services.delivery.factory.get_settings",
        lambda: Settings(_env_file=None),
    )


async def _post_invite(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
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
        response = await client.post("/api/v1/invites", json=body)
    await engine.dispose()
    return response.status_code, response.json()


def test_create_invite_phone_manual_sent() -> None:
    # Телефон → manual-канал всегда ok (оператор перешлёт вручную).
    code, data = asyncio.run(
        _post_invite(
            {"variant": "individual", "contact": "+79991234567", "operator_telegram_id": 555}
        )
    )
    assert code == 201
    assert data["status"] == "sent"
    assert data["channel"] == "manual"
    assert data["fallback_text"]  # текст для ручной пересылки присутствует


def test_create_invite_email_unconfigured_smtp_fails_with_fallback() -> None:
    # SMTP не сконфигурирован в тестовом окружении → failed + fallback.
    code, data = asyncio.run(
        _post_invite(
            {"variant": "community", "contact": "client@example.com", "operator_telegram_id": 555}
        )
    )
    assert code == 201
    assert data["channel"] == "email"
    assert data["status"] == "failed"
    assert data["fallback_text"]
    assert data["error"] == "smtp_unreachable"


def test_create_invite_bad_contact_returns_422() -> None:
    code, _ = asyncio.run(
        _post_invite({"variant": "individual", "contact": "???", "operator_telegram_id": 555})
    )
    assert code == 422
