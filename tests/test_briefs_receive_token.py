"""POST /api/v1/briefs с токеном (spec §7.2): валидный / 404 / 409 / повтор + notify."""

import asyncio
from typing import Any

import core.api.v1.briefs as briefs_module
from core.app import create_app
from db.base import Base
from db.models import Account, BriefInvite, Operator
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tests.test_brief_intake import VALID_INDIVIDUAL


class _Harness:
    def __init__(self) -> None:
        self.notified: list[tuple[int, str]] = []

    async def notifier(self, chat_id: int, text: str) -> None:
        self.notified.append((chat_id, text))


async def _setup(seed_invite_status: str | None) -> tuple[Any, _Harness, async_sessionmaker[Any]]:
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
        if seed_invite_status is not None:
            session.add(
                BriefInvite(
                    id=1,
                    account_id=1,
                    operator_id=10,
                    token="tok-abc",
                    variant="individual",
                    contact_type="telegram",
                    contact_value="@ivanov",
                    channel="telegram",
                    status=seed_invite_status,
                )
            )
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    harness = _Harness()
    app = create_app()
    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[briefs_module.get_operator_notifier] = lambda: harness.notifier
    return app, harness, maker


async def _post(app: Any, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/briefs", json=body)
    return resp.status_code, resp.json()


def test_valid_token_receives_and_notifies() -> None:
    async def scenario() -> None:
        app, harness, maker = await _setup("sent")
        code, data = await _post(
            app, {"variant": "individual", "payload": VALID_INDIVIDUAL, "token": "tok-abc"}
        )
        assert code == 201
        assert data["status"] == "received"
        # Инвайт помечен received, бриф связан с ним.
        async with maker() as session:
            invite = (
                await session.execute(select(BriefInvite).where(BriefInvite.id == 1))
            ).scalar_one()
            assert invite.status == "received"
        # Оператор уведомлён его telegram_id.
        assert len(harness.notified) == 1
        assert harness.notified[0][0] == 555
        assert "Пришёл бриф" in harness.notified[0][1]

    asyncio.run(scenario())


def test_unknown_token_returns_404() -> None:
    async def scenario() -> None:
        app, _, _ = await _setup("sent")
        code, _data = await _post(
            app, {"variant": "individual", "payload": VALID_INDIVIDUAL, "token": "nope"}
        )
        assert code == 404

    asyncio.run(scenario())


def test_failed_invite_returns_409() -> None:
    async def scenario() -> None:
        app, _, _ = await _setup("failed")
        code, _data = await _post(
            app, {"variant": "individual", "payload": VALID_INDIVIDUAL, "token": "tok-abc"}
        )
        assert code == 409

    asyncio.run(scenario())


def test_double_submit_returns_409() -> None:
    async def scenario() -> None:
        app, harness, _ = await _setup("sent")
        first_code, _ = await _post(
            app, {"variant": "individual", "payload": VALID_INDIVIDUAL, "token": "tok-abc"}
        )
        second_code, _ = await _post(
            app, {"variant": "individual", "payload": VALID_INDIVIDUAL, "token": "tok-abc"}
        )
        assert first_code == 201
        assert second_code == 409
        # Уведомление ушло ровно один раз.
        assert len(harness.notified) == 1

    asyncio.run(scenario())


def test_no_token_still_works_without_notify() -> None:
    async def scenario() -> None:
        app, harness, _ = await _setup(None)
        code, data = await _post(app, {"variant": "individual", "payload": VALID_INDIVIDUAL})
        assert code == 201
        assert data["status"] == "received"
        assert harness.notified == []

    asyncio.run(scenario())
