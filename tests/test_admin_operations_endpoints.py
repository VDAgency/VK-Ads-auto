"""Тесты веб-зеркал `/api/v1/admin/surfaces`, `/admin/briefs/{id}/launch` и
`/admin/ad-accounts/{id}/client` (`core/api/v1/admin_operations.py`).

Каждый эндпоинт — тонкое зеркало операторского пути под `require_admin`: без
admin-сессии 401, с сессией — тот же сервис, что и у бота.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.briefs as briefs_module
import pytest
from config.settings import get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, AdAccount, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.ad_accounts import AmbiguousAdAccountError, NoAdAccountError
from services.admin_auth import generate_admin_session
from services.goals import launch_goals, subscription_targets
from services.launch_service import LaunchOutcome
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_SECRET = get_settings().secret_key.get_secret_value()


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
        session.add(Client(id=100, account_id=1, full_name="Клиент 1"))
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=100,
                variant="individual",
                status="received",
                payload={"full_name": "Клиент 1"},
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


# --- GET /admin/surfaces ------------------------------------------------------


def test_surfaces_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/admin/surfaces")
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_surfaces_mirrors_the_bot_reference() -> None:
    """Тот же справочник, что у команды `/surfaces` бота (`services.goals`)."""

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/surfaces")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    targets = subscription_targets()
    assert len(data["items"]) == len(targets)
    kinds = {item["kind"] for item in data["items"]}
    assert kinds == {t.kind for t in targets}
    first = data["items"][0]
    assert {"kind", "title", "hint", "available", "goal", "goal_title", "needs_creative"} <= set(
        first
    )


# --- GET /admin/goals ----------------------------------------------------------


def test_goals_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/admin/goals")
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_goals_mirrors_the_bot_reference() -> None:
    """Тот же список целей запуска, что видит бот (`services.goals.launch_goals`)."""

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/goals")
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    goals = launch_goals()
    assert len(data["items"]) == len(goals)
    by_code = {item["code"]: item for item in data["items"]}
    assert by_code.keys() == {g.code for g in goals}
    for goal in goals:
        item = by_code[goal.code]
        assert item["title"] == goal.title
        assert item["implemented"] == goal.implemented


# --- POST /admin/briefs/{id}/launch -------------------------------------------


def _fake_launch(captured: dict[str, Any]) -> Callable[..., Awaitable[LaunchOutcome]]:
    async def fake(
        session: AsyncSession,
        account_id: int,
        brief_id: int,
        *,
        settings: Any = None,
        ad_account_id: int | None = None,
        goal: str | None = None,
        allow_relaunch: bool = False,
    ) -> LaunchOutcome:
        captured["brief_id"] = brief_id
        captured["ad_account_id"] = ad_account_id
        return LaunchOutcome(campaign_status="prepared", campaign_id=1, message="подготовлена")

    return fake


def test_admin_launch_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post("/api/v1/admin/briefs/1/launch", json={})
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_admin_launch_calls_the_same_service_as_the_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(briefs_module, "launch_without_creative", _fake_launch(captured))

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post("/api/v1/admin/briefs/1/launch", json={"ad_account_id": 42})
        assert resp.status_code == 201, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert captured["brief_id"] == 1
    assert captured["ad_account_id"] == 42
    assert data["campaign_status"] == "prepared"


def test_admin_launch_no_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise NoAdAccountError("no active ad accounts")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> int:
        resp = await client.post("/api/v1/admin/briefs/1/launch", json={})
        assert resp.json()["detail"] == "no_ad_account"
        return resp.status_code

    assert asyncio.run(_with_admin(scenario)) == 409


def test_admin_launch_ambiguous_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise AmbiguousAdAccountError("2 active ad accounts, none chosen")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> int:
        resp = await client.post("/api/v1/admin/briefs/1/launch", json={})
        assert resp.json()["detail"] == "ambiguous_ad_account"
        return resp.status_code

    assert asyncio.run(_with_admin(scenario)) == 409


# --- PATCH /admin/ad-accounts/{id}/client -------------------------------------


async def _with_ad_account(session: AsyncSession) -> None:
    session.add(Client(id=200, account_id=1, full_name="Клиент 2"))
    session.add(
        AdAccount(
            id=1,
            account_id=1,
            title="Кабинет",
            external_id="10000042",
            token_tail="abcd",
        )
    )


def test_patch_client_requires_admin_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.patch("/api/v1/admin/ad-accounts/1/client", json={"client_id": 100})
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False, extra_setup=_with_ad_account)) == 401


def test_patch_client_binds_and_unbinds_account() -> None:
    async def scenario(client: AsyncClient) -> None:
        resp = await client.patch("/api/v1/admin/ad-accounts/1/client", json={"client_id": 100})
        assert resp.status_code == 200, resp.text
        assert resp.json()["client_id"] == 100
        assert resp.json()["client_name"] == "Клиент 1"

        freed = await client.patch("/api/v1/admin/ad-accounts/1/client", json={"client_id": None})
        assert freed.json()["client_id"] is None

    asyncio.run(_with_admin(scenario, extra_setup=_with_ad_account))


def test_patch_client_unknown_client_returns_422() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.patch("/api/v1/admin/ad-accounts/1/client", json={"client_id": 999})
        assert resp.json()["detail"] == "client_not_found"
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, extra_setup=_with_ad_account)) == 422
