"""Тесты эндпоинтов данных админки (`/api/v1/admin/*` под `require_admin`)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

import core.api.v1.admin_data as admin_data_module
import pytest
from config.settings import Settings, get_settings
from core.app import create_app
from db.base import Base
from db.models import Account, AdAccount, Brief, BriefInvite, Campaign, Client, Operator
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.ad_accounts import AmbiguousAdAccountError, NoAdAccountError
from services.admin_auth import generate_admin_session
from services.launch_service import (
    AdAccountClientMismatchError,
    AdvertiserMismatchError,
    CampaignAlreadyExistsError,
    SenlerNotConnectedError,
)
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


def test_admin_creative_ad_account_client_mismatch_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кабинет закреплён за другим клиентом — 409, а не необработанное падение
    (пробел, найденный при ревью задачи 6: этот except-chain не зеркалил
    `core/api/v1/briefs.py`, хотя веб-админка ходит именно сюда)."""

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise AdAccountClientMismatchError(1, 2, 3)

    monkeypatch.setattr(admin_data_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/admin/briefs/1/creative",
            json={"media_b64": "AAAA", "media_type": "photo"},
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_admin(scenario))
    assert code == 409
    assert body["detail"] == "ad_account_client_mismatch"


def test_admin_creative_advertiser_mismatch_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """ИНН конечного рекламодателя кабинета разошёлся с брифом — 409, та же находка."""

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise AdvertiserMismatchError(1, "7700000000", "7700000001")

    monkeypatch.setattr(admin_data_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/admin/briefs/1/creative",
            json={"media_b64": "AAAA", "media_type": "photo"},
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_admin(scenario))
    assert code == 409
    assert body["detail"] == "advertiser_mismatch"


def test_admin_creative_campaign_already_exists_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """По брифу уже есть кампания (задача 6) — 409 `campaign_already_exists`,
    а не необработанное падение; `allow_relaunch` из тела доезжает до сервиса."""
    captured: dict[str, Any] = {}

    async def boom(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        raise CampaignAlreadyExistsError(1, "launched")

    monkeypatch.setattr(admin_data_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/admin/briefs/1/creative",
            json={"media_b64": "AAAA", "media_type": "photo", "allow_relaunch": True},
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_admin(scenario))
    assert code == 409
    assert body["detail"] == "campaign_already_exists"
    assert captured["allow_relaunch"] is True


def test_admin_creative_senler_not_connected_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """У сообщества нет подключённого чат-бота Senler — 422, а не необработанное
    падение (этот except-chain не зеркалил `core/api/v1/briefs.py`, хотя
    веб-админка ходит именно сюда)."""

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise SenlerNotConnectedError("club1")

    monkeypatch.setattr(admin_data_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/admin/briefs/1/creative",
            json={"media_b64": "AAAA", "media_type": "photo"},
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_admin(scenario))
    assert code == 422
    assert body["detail"] == "senler_not_connected"


def test_admin_briefs_all_includes_brief_without_invite() -> None:
    """Найденный дефект: бриф без приглашения (реферальная ссылка/лендинг, PRODUCT.md)
    не виден в pending/recent (источник там — `BriefInvite`), но должен попасть в
    `status=all`. Базовый бриф id=1 из фикстуры уже без `invite_id` — используем его
    и добавляем второй, чтобы явно проверить оба поля клиента."""

    async def extra_setup(session: AsyncSession) -> None:
        session.add(
            Brief(
                id=2,
                account_id=1,
                client_id=None,
                variant="individual",
                status="received",
                source="web",
                payload={},
            )
        )

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/briefs", params={"status": "all"})
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario, extra_setup=extra_setup))
    ids = {item["brief_id"] for item in data["items"]}
    assert {1, 2} <= ids
    item2 = next(i for i in data["items"] if i["brief_id"] == 2)
    assert item2["client_id"] is None
    assert item2["client_name"] is None
    assert item2["source"] == "web"
    assert item2["status"] == "received"


def test_admin_briefs_all_includes_invited_brief_without_duplication() -> None:
    """Бриф, пришедший ПО приглашению, тоже виден в `all` — и ровно один раз."""

    async def extra_setup(session: AsyncSession) -> None:
        session.add(Operator(id=10, account_id=1, telegram_id=777, full_name="Оператор"))
        session.add(
            BriefInvite(
                id=1,
                account_id=1,
                token="tok-invited",
                variant="individual",
                contact_type="email",
                contact_value="c@example.com",
                channel="email",
                status="received",
                operator_id=10,
            )
        )
        session.add(
            Brief(
                id=3,
                account_id=1,
                client_id=None,
                variant="individual",
                status="received",
                source="web",
                payload={},
                invite_id=1,
            )
        )

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/admin/briefs", params={"status": "all"})
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario, extra_setup=extra_setup))
    ids = [item["brief_id"] for item in data["items"]]
    assert ids.count(3) == 1


def test_admin_briefs_pending_and_recent_unaffected() -> None:
    """Существующая семантика `pending`/`recent` не сломана расширением `status`."""

    async def extra_setup(session: AsyncSession) -> None:
        session.add(Operator(id=10, account_id=1, telegram_id=777, full_name="Оператор"))
        session.add(
            BriefInvite(
                id=1,
                account_id=1,
                token="tok-pending",
                variant="individual",
                contact_type="email",
                contact_value="pending@example.com",
                channel="email",
                status="sent",
                operator_id=10,
                delivered_at=datetime.now(UTC),
            )
        )

    async def scenario(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
        pending_resp = await client.get("/api/v1/admin/briefs", params={"status": "pending"})
        recent_resp = await client.get("/api/v1/admin/briefs", params={"status": "recent"})
        assert pending_resp.status_code == 200, pending_resp.text
        assert recent_resp.status_code == 200, recent_resp.text
        return pending_resp.json(), recent_resp.json()

    pending, recent = asyncio.run(_with_admin(scenario, extra_setup=extra_setup))
    assert len(pending["items"]) == 1
    assert pending["items"][0]["contact"] == "pending@example.com"
    # Бриф id=1 из базовой фикстуры без invite_id — в recent (источник BriefInvite) не виден.
    assert recent["items"] == []


def test_admin_briefs_all_requires_session() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.get("/api/v1/admin/briefs", params={"status": "all"})
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_admin_briefs_all_orders_newest_first() -> None:
    async def extra_setup(session: AsyncSession) -> None:
        session.add(
            Brief(
                id=2,
                account_id=1,
                client_id=None,
                variant="community",
                status="received",
                source="bot",
                payload={},
            )
        )

    async def scenario(client: AsyncClient) -> list[int]:
        resp = await client.get("/api/v1/admin/briefs", params={"status": "all"})
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return [item["brief_id"] for item in body["items"]]

    ids = asyncio.run(_with_admin(scenario, extra_setup=extra_setup))
    assert ids == [2, 1]
