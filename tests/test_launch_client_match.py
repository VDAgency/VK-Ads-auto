"""Сверка кабинета с брифом при запуске (spec 2026-08-25-cabinet-client-binding-design §1.2-1.3).

Два жёстких правила отказа:
1. у кабинета указан клиент, и он не совпадает с клиентом брифа;
2. у кабинета и у брифа известен ИНН, и они различаются (по цифрам, без разделителей).

Отсутствие данных (клиент брифа неизвестен, ИНН неизвестен хоть с одной стороны) —
не повод отказывать. Проверка обязана сработать ДО любых записей в базу и до
обращения к площадке (критическое требование, доказывается отдельным тестом).

Сервисные тесты дублируют минимум инфраструктуры `tests/test_launch_ad_account.py`
(своя копия `RecordingAdapter`/`_with_db`/...), как уже сделано в `test_senler_goal.py` —
так тестовые файлы не зависят друг от друга по приватным хелперам. HTTP-тесты в конце
файла проверяют, что оба исключения доходят до обоих эндпоинтов запуска понятным
ответом, а не 500-й (по образцу `tests/test_launch_endpoint_account.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.briefs as briefs_module
import pytest
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from config.settings import Settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Campaign, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.launch_service import (
    AdAccountClientMismatchError,
    AdvertiserMismatchError,
    LaunchOutcome,
    launch_from_creative,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()

IDENTITY_ID = "10000001"
IDENTITY_TITLE = "Студия «Пример»"

BRIEF_CLIENT_ID = 100
OTHER_CLIENT_ID = 7

BRIEF_PAYLOAD = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "личная страница",
}


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "vk_ads_secret_key": SecretStr(_KEY),
        "vk_ads_access_token": SecretStr("env-token"),
        "creatives_dir": "",
    }
    base.update(over)
    return Settings(**base)


class RecordingAdapter:
    """Заглушка канала. Конструктор — единственное место, которое обязано не
    вызываться при отказе сверки: это и доказывает «до обращения к площадке»."""

    def __init__(self, token: str) -> None:
        self.token = token

    async def health_check(self) -> bool:
        return True

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "created-cabinet"

    async def create_campaign_from_spec(self, cabinet_id: str, spec: Any, **kwargs: Any) -> str:
        return "vk-campaign-1"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def get_status(self, campaign_id: str) -> str:
        return "active"

    async def stop(self, campaign_id: str) -> None:
        return None


class FakeVkAdapter(RecordingAdapter):
    """Подмена `VkApiAdapter` (класс, не функция — `_resolve_cabinet` смотрит на тип)."""

    def __init__(self, access_token: SecretStr, **_: object) -> None:
        super().__init__(access_token.get_secret_value())


class VkIdentityStub:
    def __init__(self, external_id: str, title: str) -> None:
        self.external_id = external_id
        self.username = "a1b2c3d4e5@agency_client"
        self.title = title
        self.status = "active"


@pytest.fixture(autouse=True)
def _mock_vk_and_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def identity(token: str, **_: object) -> VkIdentityStub:
        return VkIdentityStub(IDENTITY_ID, IDENTITY_TITLE)

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)
    monkeypatch.setattr(ad_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        launch_service, "save_creative", lambda *a, **k: "creative.jpg", raising=False
    )


async def _with_db(payload: dict[str, str], scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(Account(id=1, name="tenant-one"))
        session.add(
            Client(id=BRIEF_CLIENT_ID, account_id=1, full_name="Вячеслав", email="v@example.com")
        )
        session.add(
            Client(
                id=OTHER_CLIENT_ID,
                account_id=1,
                full_name="Другой клиент",
                email="other@example.com",
            )
        )
        session.add(
            Brief(
                id=500,
                account_id=1,
                client_id=BRIEF_CLIENT_ID,
                variant="individual",
                status="received",
                payload=dict(payload),
            )
        )
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


async def _add_cabinet(
    session: AsyncSession,
    *,
    client_id: int | None = None,
    advertiser_kind: str = "owner",
    advertiser_inn: str | None = None,
) -> int:
    view = await add_account(
        session,
        1,
        TOKEN,
        client_id=client_id,
        advertiser_kind=advertiser_kind,
        advertiser_name="ООО Ромашка" if advertiser_kind == "third_party" else None,
        advertiser_inn=advertiser_inn,
        settings=_settings(),
    )
    await session.commit()
    return view.id


async def _launch(session: AsyncSession, *, ad_account_id: int, settings: Settings) -> Any:
    return await launch_from_creative(
        session,
        1,
        500,
        "photo",
        "creative.jpg",
        "Заголовок",
        "Текст",
        settings=settings,
        ad_account_id=ad_account_id,
    )


async def _count_campaigns(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(Campaign))
    return result.scalar_one()


# --- правило 1: закреплённый чужой кабинет -------------------------------------


def test_launch_is_refused_when_account_belongs_to_another_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Чужой закреплённый кабинет — отказ, кампания не создаётся."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session, client_id=OTHER_CLIENT_ID)
        before = await _count_campaigns(session)
        with pytest.raises(AdAccountClientMismatchError):
            await _launch(
                session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
            )
        assert await _count_campaigns(session) == before

    asyncio.run(_with_db(BRIEF_PAYLOAD, scenario))


def test_launch_allowed_when_account_is_bound_to_the_same_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кабинет закреплён именно за клиентом брифа — совпадение, а не конфликт."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session, client_id=BRIEF_CLIENT_ID)
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        assert outcome.campaign_id is not None

    asyncio.run(_with_db(BRIEF_PAYLOAD, scenario))


def test_launch_is_refused_when_brief_has_no_client_and_account_is_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бриф без клиента (редкий случай) закреплённому кабинету тоже не подходит."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        brief = await session.get(Brief, 500)
        assert brief is not None
        brief.client_id = None
        await session.commit()
        cabinet_id = await _add_cabinet(session, client_id=OTHER_CLIENT_ID)
        with pytest.raises(AdAccountClientMismatchError):
            await _launch(
                session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
            )

    asyncio.run(_with_db(BRIEF_PAYLOAD, scenario))


# --- правило 2: ИНН конечного рекламодателя ------------------------------------


def test_launch_is_refused_when_advertiser_inn_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    """ИНН кабинета и ИНН брифа разошлись — конечный рекламодатель не тот."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)
    payload = {**BRIEF_PAYLOAD, "tax_id": "500100732259"}

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(
            session, advertiser_kind="third_party", advertiser_inn="770700000000"
        )
        before = await _count_campaigns(session)
        with pytest.raises(AdvertiserMismatchError):
            await _launch(
                session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
            )
        assert await _count_campaigns(session) == before

    asyncio.run(_with_db(payload, scenario))


def test_launch_allowed_when_inn_matches_despite_formatting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Одни и те же цифры, разное написание (пробелы/дефисы) — не отказ."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)
    payload = {**BRIEF_PAYLOAD, "tax_id": "7707 0000-0000"}

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(
            session, advertiser_kind="third_party", advertiser_inn="770700000000"
        )
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        assert outcome.campaign_id is not None

    asyncio.run(_with_db(payload, scenario))


def test_launch_allowed_when_inn_unknown_on_account_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """У кабинета ИНН нет (реклама владельца) — брифский ИНН это не блокирует."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)
    payload = {**BRIEF_PAYLOAD, "tax_id": "770700000000"}

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session, advertiser_kind="owner")
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        assert outcome.campaign_id is not None

    asyncio.run(_with_db(payload, scenario))


def test_launch_allowed_when_inn_unknown_on_brief_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """У брифа ИНН нет (обычный случай для физлица) — кабинетский ИНН это не блокирует."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(
            session, advertiser_kind="third_party", advertiser_inn="770700000000"
        )
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        assert outcome.campaign_id is not None

    asyncio.run(_with_db(BRIEF_PAYLOAD, scenario))


def test_launch_allowed_when_account_has_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Общий кабинет по-прежнему подходит — обратная совместимость (spec §1.1)."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        assert outcome.campaign_id is not None

    asyncio.run(_with_db(BRIEF_PAYLOAD, scenario))


# --- критическое требование: отказ до записей в базу и до площадки ------------


def test_refusal_happens_before_any_platform_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ обязан случиться раньше, чем построится адаптер канала: конструктор
    `VkApiAdapter` не должен быть вызван вовсе, если кабинет не подходит брифу."""

    def explode(token: SecretStr, **_: object) -> RecordingAdapter:
        raise AssertionError("VkApiAdapter must not be built when the ad account mismatches")

    monkeypatch.setattr(launch_service, "VkApiAdapter", explode)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session, client_id=OTHER_CLIENT_ID)
        with pytest.raises(AdAccountClientMismatchError):
            await _launch(
                session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
            )

    asyncio.run(_with_db(BRIEF_PAYLOAD, scenario))


def test_refusal_leaves_no_cabinet_row_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ не должен оставлять за собой и строку `Cabinet` (объект площадки в БД)."""
    from db.models import Cabinet

    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)
    payload = {**BRIEF_PAYLOAD, "tax_id": "500100732259"}

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(
            session, advertiser_kind="third_party", advertiser_inn="770700000000"
        )
        before = (await session.execute(select(func.count()).select_from(Cabinet))).scalar_one()
        with pytest.raises(AdvertiserMismatchError):
            await _launch(
                session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
            )
        after = (await session.execute(select(func.count()).select_from(Cabinet))).scalar_one()
        assert after == before

    asyncio.run(_with_db(payload, scenario))


# --- HTTP-контракт (оба эндпоинта запуска) -------------------------------------


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


def test_launch_endpoint_maps_client_mismatch_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/briefs/{id}/launch` (без креатива): `AdAccountClientMismatchError` — не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise AdAccountClientMismatchError(1, OTHER_CLIENT_ID, BRIEF_CLIENT_ID)

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={"ad_account_id": 1})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ad_account_client_mismatch"


def test_launch_endpoint_maps_advertiser_mismatch_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/briefs/{id}/launch` (без креатива): `AdvertiserMismatchError` — не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise AdvertiserMismatchError(1, "770700000000", "500100732259")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={"ad_account_id": 1})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "advertiser_mismatch"


def _creative_body() -> dict[str, Any]:
    return {"media_b64": "AAAA", "media_type": "photo"}


def test_creative_endpoint_maps_client_mismatch_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/briefs/{id}/creative` (с креативом): `AdAccountClientMismatchError` — не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise AdAccountClientMismatchError(1, OTHER_CLIENT_ID, BRIEF_CLIENT_ID)

    monkeypatch.setattr(briefs_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/creative", json=_creative_body())

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ad_account_client_mismatch"


def test_creative_endpoint_maps_advertiser_mismatch_to_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/briefs/{id}/creative` (с креативом): `AdvertiserMismatchError` — не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise AdvertiserMismatchError(1, "770700000000", "500100732259")

    monkeypatch.setattr(briefs_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/creative", json=_creative_body())

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "advertiser_mismatch"
