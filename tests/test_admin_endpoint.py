"""Тесты эндпоинтов авторизации админки: authenticate / me / logout / login / password."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from config.settings import get_settings
from core.app import create_app
from db.base import Base
from db.session import get_session
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from services.admin_auth import generate_admin_link, generate_admin_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_SECRET = get_settings().secret_key.get_secret_value()

T = TypeVar("T")


async def _run(scenario: Callable[[AsyncClient], Awaitable[T]]) -> T:
    """Поднять in-memory sqlite и приложение с override get_session (для /login, /password)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

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


def test_authenticate_sets_cookie_and_me_returns_operator() -> None:
    client = TestClient(create_app())
    token = generate_admin_link(555, _SECRET)
    resp = client.post("/api/v1/admin/authenticate", json={"token": token})
    assert resp.status_code == 200
    assert "admin_session" in resp.cookies
    me = client.get("/api/v1/admin/me")
    assert me.status_code == 200
    assert me.json()["operator_id"] == 555


def test_authenticate_invalid_token_rejected() -> None:
    client = TestClient(create_app())
    resp = client.post("/api/v1/admin/authenticate", json={"token": "bogus-token"})
    assert resp.status_code == 401


def test_me_without_session_rejected() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/admin/me").status_code == 401


def test_link_token_not_accepted_as_session_cookie() -> None:
    # Прямая подстановка magic-link в cookie не должна пускать (нужна метка admsess).
    client = TestClient(create_app())
    client.cookies.set("admin_session", generate_admin_link(555, _SECRET))
    assert client.get("/api/v1/admin/me").status_code == 401


def test_logout_clears_session() -> None:
    client = TestClient(create_app())
    client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
    assert client.get("/api/v1/admin/me").status_code == 200
    client.post("/api/v1/admin/logout")
    client.cookies.clear()
    assert client.get("/api/v1/admin/me").status_code == 401


def test_password_without_session_rejected() -> None:
    """Смена пароля без admin-сессии — 401 (require_admin), в БД не ходили."""
    client = TestClient(create_app())
    resp = client.post("/api/v1/admin/password", json={"password": "неважно_какой"})
    assert resp.status_code == 401


def test_set_password_then_login_succeeds_and_me_works() -> None:
    """Ставим пароль под admin-сессией → выходим → входим паролем → /me отвечает."""

    async def scenario(http: AsyncClient) -> tuple[int, int, dict[str, Any]]:
        http.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        set_resp = await http.post(
            "/api/v1/admin/password", json={"password": "новый_длинный_пароль"}
        )
        http.cookies.clear()
        login_resp = await http.post(
            "/api/v1/admin/login",
            json={"telegram_id": 555, "password": "новый_длинный_пароль"},
        )
        me_resp = await http.get("/api/v1/admin/me")
        return set_resp.status_code, login_resp.status_code, me_resp.json()

    set_code, login_code, me_body = asyncio.run(_run(scenario))
    assert set_code == 200
    assert login_code == 200
    assert me_body["operator_id"] == 555


def test_login_wrong_password_rejected() -> None:
    async def scenario(http: AsyncClient) -> tuple[int, int]:
        http.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        set_resp = await http.post(
            "/api/v1/admin/password", json={"password": "правильный_длинный_пароль"}
        )
        http.cookies.clear()
        login_resp = await http.post(
            "/api/v1/admin/login",
            json={"telegram_id": 555, "password": "неверный_пароль"},
        )
        return set_resp.status_code, login_resp.status_code

    set_code, login_code = asyncio.run(_run(scenario))
    assert set_code == 200
    assert login_code == 401


def test_login_unknown_operator_rejected() -> None:
    async def scenario(http: AsyncClient) -> int:
        resp = await http.post(
            "/api/v1/admin/login",
            json={"telegram_id": 424242, "password": "любой_длинный_пароль"},
        )
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 401


def test_set_password_too_short_rejected() -> None:
    async def scenario(http: AsyncClient) -> int:
        http.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        resp = await http.post("/api/v1/admin/password", json={"password": "short"})
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 422


def test_login_rejected_when_removed_from_operator_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пароль в базе верный, но Telegram ID убрали из OPERATOR_TELEGRAM_IDS.

    Например, оператора уволили: возвратный вход паролем всё равно отказан,
    тем же текстом, что при неверном пароле (spec 2026-08-31).
    """

    async def scenario(http: AsyncClient) -> tuple[int, int]:
        http.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        set_resp = await http.post(
            "/api/v1/admin/password", json={"password": "пароль_уволенного_оператора"}
        )
        http.cookies.clear()
        # Уволили: ID убран из списка операторов, пароль в базе остался как есть.
        monkeypatch.setattr(get_settings(), "operator_telegram_ids", frozenset())
        login_resp = await http.post(
            "/api/v1/admin/login",
            json={"telegram_id": 555, "password": "пароль_уволенного_оператора"},
        )
        return set_resp.status_code, login_resp.status_code

    set_code, login_code = asyncio.run(_run(scenario))
    assert set_code == 200
    assert login_code == 401


def test_require_admin_rejects_valid_session_when_removed_from_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Подпись admin-сессии валидна и ещё не истекла, но ID вне списка операторов.

    Доступ отозван немедленно, не дожидаясь TTL сессии (30 суток) — это и есть
    прицельный способ отзыва из spec 2026-08-31.
    """
    client = TestClient(create_app())
    client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
    assert client.get("/api/v1/admin/me").status_code == 200  # пока оператор в списке

    monkeypatch.setattr(get_settings(), "operator_telegram_ids", frozenset())
    assert client.get("/api/v1/admin/me").status_code == 401
