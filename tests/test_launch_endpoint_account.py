"""Тесты выбора кабинета в `POST /briefs/{id}/launch` (запуск без креатива).

Ядро уже умеет принимать `ad_account_id` и разбирать `NoAdAccountError`/
`AmbiguousAdAccountError` (services.ad_accounts) — эти тесты проверяют, что
эндпоинт доносит выбор оператора до сервиса и не даёт этим ошибкам улететь 500-й.
Сервис `launch_without_creative` подменяется — тесты эндпоинта не про его
внутреннюю логику (она покрыта tests/test_launch_ad_account.py и
tests/test_launch_without_creative.py), а про HTTP-контракт.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.briefs as briefs_module
import pytest
from core.app import create_app
from db.base import Base
from db.session import get_session
from httpx import ASGITransport, AsyncClient, Response
from services.ad_accounts import AmbiguousAdAccountError, NoAdAccountError
from services.launch_service import BriefNotFoundError, LaunchOutcome
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


async def _with_client(scenario: Callable[[AsyncClient], Awaitable[T]]) -> T:
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


def _fake_launch(
    captured: dict[str, Any],
) -> Callable[..., Awaitable[LaunchOutcome]]:
    """Заглушка `launch_without_creative`: запоминает переданный `ad_account_id`."""

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
        captured["account_id"] = account_id
        captured["brief_id"] = brief_id
        captured["ad_account_id"] = ad_account_id
        return LaunchOutcome(campaign_status="prepared", campaign_id=1, message="🚀 подготовлена")

    return fake


def test_launch_forwards_chosen_ad_account_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тело `{"ad_account_id": N}` доносит выбор оператора до сервиса."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(briefs_module, "launch_without_creative", _fake_launch(captured))

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={"ad_account_id": 42})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 201, resp.text
    assert captured["brief_id"] == 5
    assert captured["ad_account_id"] == 42
    assert resp.json()["campaign_status"] == "prepared"


def test_launch_with_empty_body_behaves_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустое тело `{}` (как шлёт текущий бот) — кабинет не выбран, сервис решает сам."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(briefs_module, "launch_without_creative", _fake_launch(captured))

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 201, resp.text
    assert captured["ad_account_id"] is None


def test_launch_no_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NoAdAccountError` — 409 `no_ad_account`, а не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise NoAdAccountError("no active ad accounts")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "no_ad_account"


def test_launch_ambiguous_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AmbiguousAdAccountError` — 409 `ambiguous_ad_account`, а не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise AmbiguousAdAccountError("2 active ad accounts, none chosen")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ambiguous_ad_account"


def test_launch_brief_not_found_is_still_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Существующее поведение (404 при отсутствующем брифе) не сломано новым телом."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise BriefNotFoundError("999")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/999/launch", json={"ad_account_id": 7})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 404


def _creative_body() -> dict[str, Any]:
    """Минимальное валидное тело загрузки креатива (сам приём подменён заглушкой)."""
    return {"media_b64": "AAAA", "media_type": "photo"}


def test_creative_no_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """Приём креатива: `NoAdAccountError` — 409, а не 500.

    Веб-админка кабинет не выбирает, поэтому в неё эта ветка прилетит первой,
    как только у оператора появится второй активный кабинет.
    """

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise NoAdAccountError("no active ad accounts")

    monkeypatch.setattr(briefs_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/creative", json=_creative_body())

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "no_ad_account"


def test_creative_ambiguous_ad_account_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """Приём креатива: `AmbiguousAdAccountError` — 409, а не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise AmbiguousAdAccountError("2 active ad accounts, none chosen")

    monkeypatch.setattr(briefs_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/creative", json=_creative_body())

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ambiguous_ad_account"
