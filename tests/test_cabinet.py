import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


async def _run(scenario: Callable[[AsyncClient], Awaitable[T]]) -> T:
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
        client = Client(account_id=1, full_name="Иван", email="i@e.com")
        session.add(client)
        await session.flush()
        session.add(
            Brief(
                account_id=1,
                client_id=client.id,
                variant="individual",
                status="received",
                source="web",
                payload={},
            )
        )
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        result = await scenario(http)
    await engine.dispose()
    return result


def test_request_link_and_view_cabinet() -> None:
    async def scenario(http: AsyncClient) -> tuple[int, int, dict[str, Any]]:
        link_resp = await http.post("/api/v1/cabinet/request-link", json={"email": "i@e.com"})
        token = link_resp.json()["magic_link"].split("token=")[1]
        view_resp = await http.get("/api/v1/cabinet", params={"token": token})
        return link_resp.status_code, view_resp.status_code, view_resp.json()

    link_code, view_code, view = asyncio.run(_run(scenario))
    assert link_code == 201
    assert view_code == 200
    assert view["full_name"] == "Иван"
    assert len(view["briefs"]) == 1


def test_request_link_unknown_client() -> None:
    async def scenario(http: AsyncClient) -> int:
        resp = await http.post("/api/v1/cabinet/request-link", json={"email": "none@e.com"})
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 404


def test_view_with_invalid_token() -> None:
    async def scenario(http: AsyncClient) -> int:
        resp = await http.get("/api/v1/cabinet", params={"token": "bad-token"})
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 401
