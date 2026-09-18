"""Тесты эндпоинтов банковских реквизитов кабинета (`/api/v1/cabinet/bank-details`, spec §E).

Устройство теста — по образцу `tests/test_cabinet_auth.py`: session-cookie кабинета
выставляется через `client.cookies.set(...)` (готовый токен, не живой логин), НЕ
per-request `cookies=`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from config.settings import get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.admin_auth import generate_admin_session
from services.session_token import generate_session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_SECRET = get_settings().secret_key.get_secret_value()
_CABINET_COOKIE = "cabinet_session"

# Тот же валидный набор, что в tests/test_bank_details.py, но собран заново
# (маленький и легко читаемый прямо в теле теста, независимость файлов важнее
# переиспользования пары строк).
_BIK = "044525225"
_CORR = "30101810400000000225"
_ACCOUNT = "40702810200000012345"  # контрольный разряд подобран под _BIK

_VALID_PAYLOAD = {
    "payer_name": "ИП Иванов Иван Иванович",
    "bank_name": "ПАО Сбербанк",
    "bik": _BIK,
    "settlement_account": _ACCOUNT,
    "correspondent_account": _CORR,
}

_VALID_COMMUNITY_BRIEF = {
    "full_name": "Анна Петрова",
    "object_url": "https://vk.com/romashka",
    "audience_description": "женщины 25-45",
    "geo": "вся Россия",
    "budget": "50000",
    "term": "месяц",
    "niche": "доставка цветов",
    "org_type": "ИП",
    "product_description": "доставка букетов за 2 часа",
    "email": "anna@example.com",
    "phone": "+79990000002",
    "bank_details": "ИП Петрова, БИК 044525225, р/с 40702810200000012345",
}


async def _with_client(
    scenario: Callable[[AsyncClient], Awaitable[T]],
    *,
    extra_setup: Callable[[AsyncSession], Awaitable[None]] | None = None,
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
        session.add(Client(id=1, account_id=1, full_name="Клиент А"))
        session.add(Client(id=2, account_id=1, full_name="Клиент Б"))
        if extra_setup is not None:
            await extra_setup(session)
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


def _cabinet_cookie(client_id: int) -> str:
    return generate_session(client_id, _SECRET)


def test_get_bank_details_without_auth_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/cabinet/bank-details")
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 401


def test_put_bank_details_without_auth_rejected() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.put("/api/v1/cabinet/bank-details", json=_VALID_PAYLOAD)
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 401


def test_put_valid_then_get_returns_them() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        client.cookies.set(_CABINET_COOKIE, _cabinet_cookie(1))
        put_resp = await client.put("/api/v1/cabinet/bank-details", json=_VALID_PAYLOAD)
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["bank_details"]["bik"] == _BIK

        get_resp = await client.get("/api/v1/cabinet/bank-details")
        assert get_resp.status_code == 200, get_resp.text
        body: dict[str, Any] = get_resp.json()
        return body

    data = asyncio.run(_with_client(scenario))
    assert data["bank_details"]["payer_name"] == _VALID_PAYLOAD["payer_name"]
    assert data["bank_details"]["settlement_account"] == _ACCOUNT
    assert data["brief_hint"] is None


def test_put_invalid_returns_422_with_field_errors() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        client.cookies.set(_CABINET_COOKIE, _cabinet_cookie(1))
        bad_payload = dict(_VALID_PAYLOAD, bik=_BIK[:8])
        resp = await client.put("/api/v1/cabinet/bank-details", json=bad_payload)
        assert resp.status_code == 422, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_client(scenario))
    assert data["detail"]["errors"]["bik"] == "bik_format"


def test_get_without_saved_returns_null_and_brief_hint() -> None:
    async def extra_setup(session: AsyncSession) -> None:
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=1,
                variant="community",
                status="received",
                payload=_VALID_COMMUNITY_BRIEF,
            )
        )

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        client.cookies.set(_CABINET_COOKIE, _cabinet_cookie(1))
        resp = await client.get("/api/v1/cabinet/bank-details")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_client(scenario, extra_setup=extra_setup))
    assert data["bank_details"] is None
    assert data["brief_hint"] == _VALID_COMMUNITY_BRIEF["bank_details"]


def test_client_a_does_not_see_client_b_details() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        client.cookies.set(_CABINET_COOKIE, _cabinet_cookie(1))
        put_resp = await client.put("/api/v1/cabinet/bank-details", json=_VALID_PAYLOAD)
        assert put_resp.status_code == 200, put_resp.text

        client.cookies.set(_CABINET_COOKIE, _cabinet_cookie(2))
        get_resp = await client.get("/api/v1/cabinet/bank-details")
        assert get_resp.status_code == 200, get_resp.text
        body: dict[str, Any] = get_resp.json()
        return body

    data = asyncio.run(_with_client(scenario))
    assert data["bank_details"] is None


def test_admin_client_card_includes_bank_details() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        client.cookies.set(_CABINET_COOKIE, _cabinet_cookie(1))
        put_resp = await client.put("/api/v1/cabinet/bank-details", json=_VALID_PAYLOAD)
        assert put_resp.status_code == 200, put_resp.text

        client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        admin_resp = await client.get("/api/v1/admin/clients/1")
        assert admin_resp.status_code == 200, admin_resp.text
        body: dict[str, Any] = admin_resp.json()
        return body

    data = asyncio.run(_with_client(scenario))
    assert data["bank_details"]["bik"] == _BIK
    assert data["bank_details"]["settlement_account"] == _ACCOUNT
