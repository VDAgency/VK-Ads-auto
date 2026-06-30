import asyncio
from typing import Any

from core.app import create_app
from db.base import Base
from db.models import Account
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tests.test_brief_intake import VALID_INDIVIDUAL


async def _post_brief(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
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
        response = await client.post("/api/v1/briefs", json=body)
    await engine.dispose()
    return response.status_code, response.json()


def test_submit_brief_ok() -> None:
    code, data = asyncio.run(_post_brief({"variant": "individual", "payload": VALID_INDIVIDUAL}))
    assert code == 201
    assert data["status"] == "received"
    assert data["brief_id"] >= 1
    assert data["client_id"] >= 1


def test_submit_brief_missing_returns_422() -> None:
    code, data = asyncio.run(_post_brief({"variant": "individual", "payload": {"full_name": "x"}}))
    assert code == 422
    assert "missing" in data["detail"]
