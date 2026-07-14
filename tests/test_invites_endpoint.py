"""Интеграционные тесты POST /api/v1/invites (spec §7.1): 3 канала ok/fail + supersede."""

import asyncio
from typing import Any

import core.api.v1.invites as invites_module
from core.app import create_app
from db.base import Base
from db.models import Account, BriefInvite
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.delivery.base import DeliveryChannel, DeliveryResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class _StubAdapter:
    def __init__(self, result: DeliveryResult) -> None:
        self._result = result

    async def send(self, contact: Any, invite_text: str) -> DeliveryResult:
        return self._result


class _StubRouter:
    def __init__(self, result: DeliveryResult) -> None:
        self._adapter = _StubAdapter(result)

    def route(self, contact: Any) -> _StubAdapter:
        return self._adapter


async def _post_invite(
    body: dict[str, Any], result: DeliveryResult
) -> tuple[int, dict[str, Any], async_sessionmaker[Any]]:
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
    # Подменяем сборку роутера доставки на стаб (без сети).
    app.dependency_overrides[invites_module.get_delivery_router] = lambda: _StubRouter(result)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/invites", json=body)
    data = response.json()
    # Движок не закрываем: in-memory БД нужна тестам, читающим строку после POST.
    return response.status_code, data, maker


def test_invite_telegram_ok() -> None:
    code, data, _ = asyncio.run(
        _post_invite(
            {"variant": "individual", "contact": "@ivanov", "operator_telegram_id": 555},
            DeliveryResult(ok=True, channel=DeliveryChannel.TELEGRAM),
        )
    )
    assert code == 201
    assert data["status"] == "sent"
    assert data["channel"] == "telegram"
    assert data["invite_id"] >= 1


def test_invite_email_ok() -> None:
    code, data, _ = asyncio.run(
        _post_invite(
            {"variant": "community", "contact": "user@example.com", "operator_telegram_id": 555},
            DeliveryResult(ok=True, channel=DeliveryChannel.EMAIL),
        )
    )
    assert code == 201
    assert data["status"] == "sent"
    assert data["channel"] == "email"


def test_invite_phone_manual_returns_fallback() -> None:
    code, data, _ = asyncio.run(
        _post_invite(
            {"variant": "individual", "contact": "+79991234567", "operator_telegram_id": 555},
            DeliveryResult(
                ok=True, channel=DeliveryChannel.MANUAL, fallback_text="перешлите вручную"
            ),
        )
    )
    assert code == 201
    assert data["status"] == "sent"
    assert data["channel"] == "manual"
    assert data["fallback_text"] == "перешлите вручную"


def test_invite_failed_returns_error_and_fallback() -> None:
    code, data, _ = asyncio.run(
        _post_invite(
            {"variant": "individual", "contact": "@nobody", "operator_telegram_id": 555},
            DeliveryResult(
                ok=False,
                channel=DeliveryChannel.TELEGRAM,
                fallback_text="перешлите вручную",
                error="username_not_occupied",
            ),
        )
    )
    assert code == 201
    assert data["status"] == "failed"
    assert data["error"] == "username_not_occupied"
    assert data["fallback_text"] == "перешлите вручную"


def test_invite_unrecognized_contact_returns_422() -> None:
    code, data, _ = asyncio.run(
        _post_invite(
            {"variant": "individual", "contact": "не контакт", "operator_telegram_id": 555},
            DeliveryResult(ok=True, channel=DeliveryChannel.TELEGRAM),
        )
    )
    assert code == 422


def test_invite_persists_row_in_db() -> None:
    async def scenario() -> None:
        code, data, maker = await _post_invite(
            {"variant": "individual", "contact": "@ivanov", "operator_telegram_id": 555},
            DeliveryResult(ok=True, channel=DeliveryChannel.TELEGRAM),
        )
        assert code == 201
        async with maker() as session:
            invite = (
                await session.execute(
                    select(BriefInvite).where(BriefInvite.id == data["invite_id"])
                )
            ).scalar_one()
            assert invite.status == "sent"
            assert invite.contact_value == "@ivanov"

    asyncio.run(scenario())
