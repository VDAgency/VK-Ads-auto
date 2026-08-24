import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

import pytest
from config.settings import get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Campaign, Client, Stat
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.auth_magiclink import generate_token
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


def test_request_link_known_client_sends_email(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, str] = {}

    async def fake_send(email: str, magic_link: str, **kwargs: Any) -> bool:
        sent["email"] = email
        sent["link"] = magic_link
        return True

    monkeypatch.setattr("core.api.v1.cabinet.send_login_link", fake_send)

    async def scenario(http: AsyncClient) -> tuple[int, dict[str, Any]]:
        resp = await http.post("/api/v1/cabinet/request-link", json={"email": "i@e.com"})
        return resp.status_code, resp.json()

    code, body = asyncio.run(_run(scenario))
    assert code == 200
    assert body == {"ok": True}
    assert sent["email"] == "i@e.com"
    assert "token=" in sent["link"]


def test_request_link_unknown_client_no_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_send(email: str, magic_link: str, **kwargs: Any) -> bool:
        calls["n"] += 1
        return True

    monkeypatch.setattr("core.api.v1.cabinet.send_login_link", fake_send)

    async def scenario(http: AsyncClient) -> tuple[int, dict[str, Any]]:
        resp = await http.post("/api/v1/cabinet/request-link", json={"email": "none@e.com"})
        return resp.status_code, resp.json()

    code, body = asyncio.run(_run(scenario))
    # Тот же ответ, что и для существующего клиента — наличие не раскрыто.
    assert code == 200
    assert body == {"ok": True}
    assert calls["n"] == 0  # письмо неизвестному не отправлялось


def test_view_cabinet_by_token() -> None:
    async def scenario(http: AsyncClient) -> tuple[int, dict[str, Any]]:
        token = generate_token(1, get_settings().secret_key.get_secret_value())
        resp = await http.get("/api/v1/cabinet", params={"token": token})
        return resp.status_code, resp.json()

    code, view = asyncio.run(_run(scenario))
    assert code == 200
    assert view["full_name"] == "Иван"
    assert view["email"] == "i@e.com"
    assert len(view["briefs"]) == 1
    assert view["report"]["campaigns"] == []  # кампаний нет — честный пустой список


def test_view_with_invalid_token() -> None:
    async def scenario(http: AsyncClient) -> int:
        resp = await http.get("/api/v1/cabinet", params={"token": "bad-token"})
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 401


def test_view_cabinet_without_session_or_token_is_401() -> None:
    """Без cookie-сессии и без magic-link токена в кабинет не попасть (B1)."""

    async def scenario(http: AsyncClient) -> int:
        resp = await http.get("/api/v1/cabinet")
        return resp.status_code

    assert asyncio.run(_run(scenario)) == 401


async def _run_with_client_campaign(scenario: Callable[[AsyncClient], Awaitable[T]]) -> T:
    """Тот же кабинет, что и `_run`, плюс одна запущенная кампания клиента (B1 — мини-отчёт)."""
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
        brief = Brief(
            account_id=1,
            client_id=client.id,
            variant="individual",
            status="received",
            source="web",
            payload={},
        )
        session.add(brief)
        await session.flush()
        session.add(
            Campaign(
                account_id=1,
                brief_id=brief.id,
                client_id=client.id,
                objective="socialengagement",
                status="launched",
                external_id="777",
                spec_json={"object_kind": "senler"},
            )
        )
        session.add(
            Stat(account_id=1, campaign_id="777", shows=100, clicks=8, spent=999, results=5)
        )
        await session.commit()
        assert client.id == 1  # первая строка client — на неё сгенерирован magic-link токен

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        result = await scenario(http)
    await engine.dispose()
    return result


def test_view_cabinet_report_shows_client_metrics_without_spend() -> None:
    """Авторизованный клиент получает свои метрики; расход в ответе не встречается."""

    async def scenario(http: AsyncClient) -> dict[str, Any]:
        token = generate_token(1, get_settings().secret_key.get_secret_value())
        resp = await http.get("/api/v1/cabinet", params={"token": token})
        assert resp.status_code == 200, resp.text
        return cast(dict[str, Any], resp.json())

    view = asyncio.run(_run_with_client_campaign(scenario))
    campaigns = view["report"]["campaigns"]
    assert [c["external_id"] for c in campaigns] == ["777"]
    row = campaigns[0]
    assert row["shows"] == 100
    assert row["results"] == 5
    assert row["goal"] == "Заявка через Senler"
    assert row["status"] == "запущена"
    assert "spent" not in row
    assert "spent" not in view["report"]
