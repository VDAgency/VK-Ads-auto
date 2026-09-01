"""Тесты отзыва admin-сессий: /admin/logout-all и побочный отзыв при смене пароля."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from config.settings import get_settings
from core.app import create_app
from db.base import Base
from db.session import get_session
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


def test_logout_all_without_session_rejected() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.post("/api/v1/admin/logout-all")
        assert resp.status_code == 401

    asyncio.run(_run(scenario))


def test_logout_all_drops_the_current_device_too() -> None:
    """«Выйти на всех устройствах» означает и это устройство тоже."""

    async def scenario(client: AsyncClient) -> None:
        token = generate_admin_session(555, _SECRET)
        await asyncio.sleep(1.1)

        resp = await client.post("/api/v1/admin/logout-all", cookies={"admin_session": token})
        assert resp.status_code == 200
        assert resp.cookies.get("admin_session") is None

        after = await client.get("/api/v1/admin/me", cookies={"admin_session": token})
        assert after.status_code == 401

    asyncio.run(_run(scenario))


def test_password_change_keeps_the_operator_logged_in() -> None:
    async def scenario(client: AsyncClient) -> None:
        token = generate_admin_session(555, _SECRET)

        resp = await client.post(
            "/api/v1/admin/password",
            json={"password": "correct-horse-battery"},
            cookies={"admin_session": token},
        )
        assert resp.status_code == 200

    asyncio.run(_run(scenario))


def test_password_change_keeps_current_device_and_drops_the_others() -> None:
    """Смена пароля рвёт прежние входы, но не выкидывает того, кто её делает.

    Порядок в обработчике обязан быть: сохранить хеш → поднять границу → выдать
    новую cookie. Любой другой порядок ломает ровно этот тест.
    """

    async def scenario(client: AsyncClient) -> None:
        # Две сессии одного оператора: «текущее устройство» и «другое устройство».
        current = generate_admin_session(555, _SECRET)
        other = generate_admin_session(555, _SECRET)

        # Секунда паузы: отметка выпуска в секундах, и без неё граница совпала бы
        # с моментом выпуска, а тест проходил бы по случайности.
        await asyncio.sleep(1.1)

        resp = await client.post(
            "/api/v1/admin/password",
            json={"password": "correct-horse-battery"},
            cookies={"admin_session": current},
        )
        assert resp.status_code == 200

        fresh = resp.cookies.get("admin_session")
        assert fresh is not None, "после смены пароля обязана выдаваться новая cookie"

        # Устройство, с которого меняли пароль, остаётся в системе.
        stayed = await client.get("/api/v1/admin/me", cookies={"admin_session": fresh})
        assert stayed.status_code == 200

        # Другое устройство выпадает.
        dropped = await client.get("/api/v1/admin/me", cookies={"admin_session": other})
        assert dropped.status_code == 401

    asyncio.run(_run(scenario))


def test_authenticate_rejects_link_issued_before_logout_all() -> None:
    """Находка 2 (Important, аудит 2026-09-01): `/authenticate` обязан отвергать
    admin-ссылку (живёт 15 минут), выпущенную ДО «выйти на всех устройствах», —
    иначе оператор, подозревая компрометацию, жмёт `logout-all`, а держатель ещё
    не истёкшей ссылки всё равно обменивает её на новую полноценную сессию.
    """

    async def scenario(client: AsyncClient) -> tuple[int, int]:
        session_token = generate_admin_session(555, _SECRET)
        stale_link = generate_admin_link(555, _SECRET)

        await asyncio.sleep(1.1)  # отметка выпуска в секундах

        logout = await client.post(
            "/api/v1/admin/logout-all", cookies={"admin_session": session_token}
        )
        assert logout.status_code == 200
        client.cookies.clear()

        # Ссылка, выпущенная до logout-all, обязана быть отвергнута.
        stale_resp = await client.post("/api/v1/admin/authenticate", json={"token": stale_link})
        client.cookies.clear()  # не тащить cookie, если находка не пофикшена и запрос прошёл

        await asyncio.sleep(1.1)  # снова: граница отзыва — секунды

        # Ссылка, выпущенная ПОСЛЕ logout-all, обязана по-прежнему работать —
        # иначе оператор вообще не сможет войти заново.
        fresh_link = generate_admin_link(555, _SECRET)
        fresh_resp = await client.post("/api/v1/admin/authenticate", json={"token": fresh_link})
        return stale_resp.status_code, fresh_resp.status_code

    stale_status, fresh_status = asyncio.run(_run(scenario))
    assert stale_status == 401
    assert fresh_status == 200


def test_revocation_does_not_affect_another_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отзыв сессий одного оператора не задевает сессию другого."""

    async def scenario(client: AsyncClient) -> None:
        settings = get_settings()
        monkeypatch.setattr(
            settings, "operator_telegram_ids", settings.operator_telegram_ids | {556}
        )

        token_555 = generate_admin_session(555, _SECRET)
        token_556 = generate_admin_session(556, _SECRET)
        await asyncio.sleep(1.1)

        resp = await client.post("/api/v1/admin/logout-all", cookies={"admin_session": token_555})
        assert resp.status_code == 200

        other_operator = await client.get("/api/v1/admin/me", cookies={"admin_session": token_556})
        assert other_operator.status_code == 200
        assert other_operator.json()["operator_id"] == 556

    asyncio.run(_run(scenario))
