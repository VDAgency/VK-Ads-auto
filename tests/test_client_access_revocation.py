"""Отзыв доступа клиенту оператором (`POST /api/v1/admin/clients/{id}/revoke-access`).

Отзыв обязан гасить И session-cookie кабинета, И выданную magic-ссылку разом —
только сессию гасить недостаточно: утёкшая ссылка продолжила бы пускать в кабинет,
а оператор был бы уверен, что доступ закрыт (spec 2026-09-01, аудит безопасности,
пункт 4). Образец устройства теста — `tests/test_admin_data_endpoint.py`.
"""

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
from services.admin_auth import generate_admin_session
from services.auth_magiclink import generate_token
from services.session_token import generate_session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_SECRET = get_settings().secret_key.get_secret_value()

# Активный sessionmaker текущего прогона `_run` — нужен `_create_client`, чтобы
# заводить клиентов прямо из сценария, а не только до его запуска (тест 5 заводит
# двух клиентов и должен убедиться, что отзыв одному не задевает другого).
_maker_holder: list[async_sessionmaker[AsyncSession]] = []


async def _run(scenario: Callable[[AsyncClient], Awaitable[T]]) -> T:
    """Поднять in-memory sqlite, приложение с override `get_session` и Account(id=1)."""
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
    _maker_holder.append(maker)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            result = await scenario(client)
    finally:
        _maker_holder.pop()
    await engine.dispose()
    return result


async def _create_client(_client: AsyncClient) -> int:
    """Завести `Client` напрямую через сессию БД, вернуть его id.

    Принимает `AsyncClient` (не используется напрямую), чтобы вызов из сценария
    выглядел единообразно — `Account(id=1)` уже создан в `_run`.
    """
    maker = _maker_holder[-1]
    async with maker() as session:
        client = Client(account_id=1, full_name="Клиент")
        session.add(client)
        await session.commit()
        await session.refresh(client)
        return client.id


def test_revoke_without_admin_session_returns_401() -> None:
    async def scenario(client: AsyncClient) -> int:
        client_id = await _create_client(client)
        resp = await client.post(f"/api/v1/admin/clients/{client_id}/revoke-access")
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 401


def test_revoke_nonexistent_client_returns_404() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/admin/clients/999999/revoke-access",
            cookies={"admin_session": generate_admin_session(555, _SECRET)},
        )
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 404


def test_revoke_kills_the_magic_link_not_only_the_session() -> None:
    """Отзыв обязан гасить и выданные ссылки.

    Иначе отзыв — фикция: утёкшая ссылка продолжает пускать в кабинет, а оператор
    уверен, что доступ закрыт.
    """

    async def scenario(client: AsyncClient) -> None:
        client_id = await _create_client(client)  # хелпер теста, см. выше
        link_token = generate_token(client_id, _SECRET)
        cookie = generate_session(client_id, _SECRET)

        # Ссылка и сессия работают до отзыва.
        assert (await client.get(f"/api/v1/cabinet?token={link_token}")).status_code == 200
        assert (
            await client.get("/api/v1/cabinet", cookies={"cabinet_session": cookie})
        ).status_code == 200

        await asyncio.sleep(1.1)  # отметка выпуска в секундах

        revoked = await client.post(
            f"/api/v1/admin/clients/{client_id}/revoke-access",
            cookies={"admin_session": generate_admin_session(555, _SECRET)},
        )
        assert revoked.status_code == 200

        assert (await client.get(f"/api/v1/cabinet?token={link_token}")).status_code == 401
        assert (
            await client.get("/api/v1/cabinet", cookies={"cabinet_session": cookie})
        ).status_code == 401

    asyncio.run(_run(scenario))


def test_revoke_does_not_affect_other_client() -> None:
    async def scenario(client: AsyncClient) -> tuple[int, int]:
        client_a = await _create_client(client)
        client_b = await _create_client(client)
        cookie_a = generate_session(client_a, _SECRET)
        cookie_b = generate_session(client_b, _SECRET)

        await asyncio.sleep(1.1)

        revoked = await client.post(
            f"/api/v1/admin/clients/{client_a}/revoke-access",
            cookies={"admin_session": generate_admin_session(555, _SECRET)},
        )
        assert revoked.status_code == 200

        status_a = (
            await client.get("/api/v1/cabinet", cookies={"cabinet_session": cookie_a})
        ).status_code
        status_b = (
            await client.get("/api/v1/cabinet", cookies={"cabinet_session": cookie_b})
        ).status_code
        return status_a, status_b

    status_a, status_b = asyncio.run(_run(scenario))
    assert status_a == 401
    assert status_b == 200


def test_set_password_after_revoke_keeps_client_logged_in() -> None:
    """После отзыва прежняя ссылка гаснет, но свежая по-прежнему годится для
    `set-password` и оставляет клиента в кабинете на этом устройстве (spec §6.3)."""

    async def scenario(client: AsyncClient) -> tuple[int, int, int]:
        client_id = await _create_client(client)
        stale_link = generate_token(client_id, _SECRET)

        await asyncio.sleep(1.1)

        revoked = await client.post(
            f"/api/v1/admin/clients/{client_id}/revoke-access",
            cookies={"admin_session": generate_admin_session(555, _SECRET)},
        )
        assert revoked.status_code == 200

        stale_status = (await client.get(f"/api/v1/cabinet?token={stale_link}")).status_code

        fresh_link = generate_token(client_id, _SECRET)
        set_resp = await client.post(
            "/api/v1/cabinet/set-password",
            json={"token": fresh_link, "password": "новый_длинный_пароль"},
        )
        me_resp = await client.get("/api/v1/cabinet")
        return stale_status, set_resp.status_code, me_resp.status_code

    stale_status, set_status, me_status = asyncio.run(_run(scenario))
    assert stale_status == 401
    assert set_status == 200
    assert me_status == 200


def test_set_password_alone_revokes_prior_sessions_and_links() -> None:
    """Смена пароля сама по себе обязана поднимать границу отзыва.

    Ловит мутацию «убрать `revoke_client_sessions` из `set_password`»: без неё ни
    один из остальных тестов файла не краснеет — они все проверяют границу,
    поднятую эндпоинтом `revoke-access`, а не сменой пароля. Сценарий: сначала
    оператор отзывает доступ (граница №1), затем клиент заходит заново — новые
    cookie/ссылка выпущены ПОСЛЕ границы №1 и потому валидны. Смена пароля по
    отдельной свежей ссылке обязана поднять границу №2 и погасить именно их.
    """

    async def scenario(client: AsyncClient) -> tuple[int, int, int, int, int]:
        # Cookie ставим/чистим явно через `client.cookies`, а не через per-request
        # `cookies=` — httpx молча оседает такие cookie в общей джаре клиента
        # (см. DeprecationWarning), и ответ `set-password` со свежим `cabinet_session`
        # незаметно подмешался бы в последующий «только по токену» запрос, скрывая
        # именно ту регрессию, которую тест обязан ловить.
        client_id = await _create_client(client)

        client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        revoke_resp = await client.post(f"/api/v1/admin/clients/{client_id}/revoke-access")
        assert revoke_resp.status_code == 200
        client.cookies.clear()

        await asyncio.sleep(1.1)  # отметка выпуска в секундах

        # Клиент заходит заново уже после отзыва — эти cookie/ссылка валидны.
        leaked_cookie = generate_session(client_id, _SECRET)
        leaked_link = generate_token(client_id, _SECRET)

        link_before = (await client.get(f"/api/v1/cabinet?token={leaked_link}")).status_code

        client.cookies.set("cabinet_session", leaked_cookie)
        cookie_before = (await client.get("/api/v1/cabinet")).status_code
        client.cookies.clear()

        await asyncio.sleep(1.1)  # снова: граница смены пароля — секунды

        # Смена пароля идёт по ДРУГОЙ, отдельно выпущенной ссылке.
        fresh_link = generate_token(client_id, _SECRET)
        set_resp = await client.post(
            "/api/v1/cabinet/set-password",
            json={"token": fresh_link, "password": "ещё_один_длинный_пароль"},
        )
        client.cookies.clear()  # не тащить свежую cookie, выставленную set-password

        link_after = (await client.get(f"/api/v1/cabinet?token={leaked_link}")).status_code

        client.cookies.set("cabinet_session", leaked_cookie)
        cookie_after = (await client.get("/api/v1/cabinet")).status_code
        client.cookies.clear()

        return link_before, cookie_before, set_resp.status_code, link_after, cookie_after

    link_before, cookie_before, set_status, link_after, cookie_after = asyncio.run(_run(scenario))
    assert link_before == 200
    assert cookie_before == 200
    assert set_status == 200
    assert link_after == 401
    assert cookie_after == 401
