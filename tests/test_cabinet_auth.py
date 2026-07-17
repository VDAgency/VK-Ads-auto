"""Тесты авторизации кабинета (C3): установка пароля, вход, session-cookie."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from config.settings import get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.auth_magiclink import generate_token
from services.password import hash_password
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_SECRET = get_settings().secret_key.get_secret_value()


async def _with_client(
    scenario: Callable[[AsyncClient], Awaitable[T]],
    *,
    password_hash: str | None = None,
) -> T:
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
        session.add(
            Client(
                id=1,
                account_id=1,
                full_name="Вячеслав",
                email="v@example.com",
                phone="+79990000000",
                password_hash=password_hash,
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
        result = await scenario(client)
    await engine.dispose()
    return result


def test_set_password_then_cabinet_by_cookie() -> None:
    async def scenario(client: AsyncClient) -> int:
        token = generate_token(1, _SECRET)
        resp = await client.post(
            "/api/v1/cabinet/set-password", json={"token": token, "password": "goodpass1"}
        )
        assert resp.status_code == 200, resp.text
        assert "cabinet_session" in resp.cookies
        # Cookie сохранён в клиенте → кабинет доступен без токена.
        cab = await client.get("/api/v1/cabinet")
        assert cab.status_code == 200, cab.text
        assert cab.json()["password_set"] is True
        return int(cab.json()["client_id"])

    assert asyncio.run(_with_client(scenario)) == 1


def test_set_password_too_short_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        token = generate_token(1, _SECRET)
        resp = await client.post(
            "/api/v1/cabinet/set-password", json={"token": token, "password": "short"}
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 422


def test_set_password_invalid_token_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/cabinet/set-password", json={"token": "bogus", "password": "goodpass1"}
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 401


def test_login_with_correct_password() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/cabinet/login", json={"email": "v@example.com", "password": "goodpass1"}
        )
        assert resp.status_code == 200, resp.text
        assert "cabinet_session" in resp.cookies
        cab = await client.get("/api/v1/cabinet")
        return cab.status_code

    code = asyncio.run(_with_client(scenario, password_hash=hash_password("goodpass1")))
    assert code == 200


def test_login_wrong_password_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/cabinet/login", json={"email": "v@example.com", "password": "wrongpass"}
        )
        return resp.status_code

    code = asyncio.run(_with_client(scenario, password_hash=hash_password("goodpass1")))
    assert code == 401


def test_login_unknown_email_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/cabinet/login", json={"email": "nobody@example.com", "password": "goodpass1"}
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario, password_hash=hash_password("goodpass1"))) == 401


def test_login_without_password_set_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        # У клиента пароль не установлен (password_hash IS NULL) → вход невозможен.
        resp = await client.post(
            "/api/v1/cabinet/login", json={"email": "v@example.com", "password": "anything1"}
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 401


def test_cabinet_without_auth_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/cabinet")
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 401


def test_logout_clears_cookie() -> None:
    async def scenario(client: AsyncClient) -> bool:
        resp = await client.post("/api/v1/cabinet/logout")
        assert resp.status_code == 200
        # delete_cookie выставляет Set-Cookie с истёкшим сроком.
        return "cabinet_session" in resp.headers.get("set-cookie", "")

    assert asyncio.run(_with_client(scenario)) is True
