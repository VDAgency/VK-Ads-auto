"""Тесты эндпоинтов данных админки (`/api/v1/admin/*` под `require_admin`)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.admin_data as admin_data_module
import pytest
from config.settings import Settings, get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, AdAccount, Brief, Campaign, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.ad_accounts import AmbiguousAdAccountError, NoAdAccountError
from services.admin_auth import generate_admin_session
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_SECRET = get_settings().secret_key.get_secret_value()


@pytest.fixture(autouse=True)
def _unconfigured_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каналы доставки не сконфигурированы (детерминизм для `send_invite`, как в других тестах)."""
    monkeypatch.setattr(
        "services.delivery.factory.get_settings",
        lambda: Settings(_env_file=None),
    )


async def _with_admin(
    scenario: Callable[[AsyncClient], Awaitable[T]],
    *,
    authed: bool = True,
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
        session.add(
            Client(
                id=1,
                account_id=1,
                full_name="Вячеслав",
                email="v@example.com",
                phone="+79990000000",
            )
        )
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=1,
                variant="individual",
                status="received",
                payload={"full_name": "Вячеслав", "geo": "Самара"},
            )
        )
        session.add(
            Campaign(
                id=1,
                account_id=1,
                brief_id=1,
                client_id=1,
                status="prepared",
                objective="socialengagement",
                spec_json={},
            )
        )
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
        if authed:
            client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        result = await scenario(client)
    await engine.dispose()
    return result


def test_overview_counts() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/overview")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["clients"] == 1
    assert data["campaigns"] == 1


def test_clients_list_with_brief_count() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/clients")
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert len(data["items"]) == 1
    row = data["items"][0]
    assert row["full_name"] == "Вячеслав"
    assert row["brief_count"] == 1


def test_client_detail_with_briefs() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/clients/1")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["email"] == "v@example.com"
    assert len(data["briefs"]) == 1
    assert data["briefs"][0]["id"] == 1


def test_brief_detail_returns_card() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/briefs/1")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["brief_id"] == 1
    assert any(f["label"] == "Как обращаться" for f in data["fields"])


def test_brief_edit_applies() -> None:
    # Номер берётся из канонической карты, а не пишется константой: иначе тест
    # ломается при каждом изменении порядка полей формы.
    from services.brief_fields import fields_for

    geo_number = str(
        next(i for i, f in enumerate(fields_for("individual"), start=1) if f.key == "geo")
    )

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.patch("/api/v1/admin/briefs/1", json={"edits": {geo_number: "Москва"}})
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    geo = next(f for f in data["fields"] if f["label"] == "География")
    assert geo["value"] == "Москва"


def test_campaigns_list() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/campaigns")
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert len(data["items"]) == 1
    assert data["items"][0]["status"] == "prepared"
    assert data["items"][0]["client_name"] == "Вячеслав"


def test_campaigns_list_shows_funding_ad_account() -> None:
    """Кампания несёт кабинет, которым запущена — без этого расследовать ошибку задним

    числом можно только запросом в базу (spec 2026-08-25 §3)."""

    async def extra_setup(session: AsyncSession) -> None:
        session.add(
            AdAccount(
                id=1,
                account_id=1,
                title="Кабинет Долматова",
                external_id="10000042",
                token_tail="abcd",
            )
        )
        await session.execute(update(Campaign).where(Campaign.id == 1).values(ad_account_id=1))

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/campaigns")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario, extra_setup=extra_setup))
    row = data["items"][0]
    assert row["ad_account_title"] == "Кабинет Долматова"
    assert row["ad_account_external_id"] == "10000042"


def test_campaigns_list_without_ad_account_is_null() -> None:
    """Старые кампании (до миграции 0010) без кабинета — поля пустые, не 500."""

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/campaigns")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    row = data["items"][0]
    assert row["ad_account_title"] is None
    assert row["ad_account_external_id"] is None


def test_admin_endpoints_require_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/admin/clients")
        return resp.status_code

    # Без admin-cookie — 401.
    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_send_invite_creates_invite() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/admin/invites",
            json={"variant": "individual", "contact": "newclient@example.com"},
        )
        assert resp.status_code == 201, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    # Каналы не сконфигурированы → email отдаёт fallback (failed), но инвайт создан.
    assert data["channel"] == "email"
    assert data["invite_id"] >= 1
    assert data["fallback_text"]


def test_send_invite_bad_contact_422() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/admin/invites", json={"variant": "individual", "contact": "???"}
        )
        return resp.status_code

    assert asyncio.run(_with_admin(scenario)) == 422


def test_send_invite_requires_admin() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/admin/invites",
            json={"variant": "individual", "contact": "x@example.com"},
        )
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_admin_creative_no_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """Админка кабинет не выбирает: без единственного кабинета — 409, а не 500."""

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise NoAdAccountError("no active ad accounts")

    monkeypatch.setattr(admin_data_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/admin/briefs/1/creative",
            json={"media_b64": "AAAA", "media_type": "photo"},
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_admin(scenario))
    assert code == 409
    assert body["detail"] == "no_ad_account"


def test_admin_creative_ambiguous_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кабинетов несколько, выбора в админке нет — честный 409 вместо трассировки."""

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise AmbiguousAdAccountError("2 active ad accounts, none chosen")

    monkeypatch.setattr(admin_data_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/admin/briefs/1/creative",
            json={"media_b64": "AAAA", "media_type": "photo"},
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_admin(scenario))
    assert code == 409
    assert body["detail"] == "ambiguous_ad_account"
