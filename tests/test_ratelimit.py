"""core/ratelimit + интеграция с POST /briefs (spec §7.2: 30 rpm с IP)."""

import asyncio
from typing import Any

import core.api.v1.briefs as briefs_module
from core.app import create_app
from core.ratelimit import SlidingWindowRateLimiter
from db.base import Base
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tests.test_brief_intake import VALID_INDIVIDUAL


def test_limiter_blocks_after_limit() -> None:
    limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60)
    assert limiter.allow("1.2.3.4", now=0.0)
    assert limiter.allow("1.2.3.4", now=1.0)
    assert limiter.allow("1.2.3.4", now=2.0)
    assert not limiter.allow("1.2.3.4", now=3.0)


def test_limiter_window_slides() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    assert limiter.allow("ip", now=0.0)
    assert not limiter.allow("ip", now=30.0)
    # После окна снова можно.
    assert limiter.allow("ip", now=61.0)


def test_limiter_isolates_keys() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    assert limiter.allow("a", now=0.0)
    assert limiter.allow("b", now=0.0)
    assert not limiter.allow("a", now=1.0)


def test_briefs_endpoint_returns_429_over_limit() -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        from db.models import Account

        async with maker() as session:
            session.add(Account(id=1, name="default"))
            await session.commit()

        async def _override() -> Any:
            async with maker() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_session] = _override
        # Один общий лимитер на все запросы, маленький лимит — быстро упираемся.
        shared = SlidingWindowRateLimiter(limit=2, window_seconds=60)
        app.dependency_overrides[briefs_module.get_rate_limiter] = lambda: shared

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            body = {"variant": "individual", "payload": VALID_INDIVIDUAL}
            codes = [(await client.post("/api/v1/briefs", json=body)).status_code for _ in range(3)]
        assert codes[0] == 201
        assert codes[2] == 429

    asyncio.run(scenario())
