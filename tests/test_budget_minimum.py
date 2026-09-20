"""Тесты минимального дневного бюджета площадки (задача 7, spec §C).

VK отклоняет `budget_limit_day` ниже порога целиком (боевая проверка 2026-07-27):
100 ₽/день у всех площадок ВК/ОК/MAX, 10 000 ₽/день у канала Дзен
(`integrations.vk_surfaces.Surface.min_daily_budget_rub`). Проверяются:
справочник площадок и его отражение в `services.goals`/карточке брифа; отказ
ядра ДО побочных эффектов на обоих путях запуска; предупреждение в превью
(`launch_preview`) без блокировки кнопки; коды отказа 422 `budget_below_minimum`
во всех местах маппинга; человеческий текст в боте (карточка подтверждения,
причина отказа, `/surfaces`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.admin_data as admin_data_module
import core.api.v1.briefs as briefs_module
import httpx
import pytest
import respx
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from bot import api_client
from bot.api_client import BriefCard, BriefFieldItem, CreativeRejected
from bot.handlers import creative
from config.settings import Settings, get_settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Client
from db.repositories import list_campaigns_for_brief
from db.session import get_session
from httpx import ASGITransport, AsyncClient, Response
from integrations.adapter import PlatformAdapter
from integrations.channels import Channel, ChannelConfig, ChannelRouter
from integrations.vk_surfaces import DZEN_CHANNEL, VK_COMMUNITY
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.admin_auth import generate_admin_session
from services.goals import subscription_targets
from services.launch_service import (
    BudgetBelowMinimumError,
    LaunchOutcome,
    launch_from_creative,
)
from services.mapping import CampaignSpec
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()
_ADMIN_SECRET = get_settings().secret_key.get_secret_value()
IDENTITY = VkIdentity(
    external_id="10000001",
    username="a1b2c3d4e5@agency_client",
    title="Кабинет «Пример»",
    status="active",
)


# --- integrations/vk_surfaces.py + services/goals.py --------------------------


def test_default_surface_minimum_is_100() -> None:
    assert VK_COMMUNITY.min_daily_budget_rub == 100


def test_dzen_surface_minimum_is_10000() -> None:
    assert DZEN_CHANNEL.min_daily_budget_rub == 10000


def test_subscription_targets_expose_the_surface_minimum() -> None:
    targets = {target.kind: target for target in subscription_targets()}
    assert targets["dzen_channel"].min_daily_budget_rub == 10000
    assert targets["community"].min_daily_budget_rub == 100


# --- services/launch_service.py: правило на голой CampaignSpec ----------------


def _spec(
    object_kind: str, budget_rub: int | None, *, needs_discussion: bool = False
) -> CampaignSpec:
    return CampaignSpec(
        objective="socialengagement",
        name="Тест",
        object_url="https://vk.com/test",
        geo_raw="Самара",
        object_kind=object_kind,
        budget_rub=budget_rub,
        needs_budget_discussion=needs_discussion,
    )


def test_below_minimum_on_default_surface_is_rejected() -> None:
    from services.launch_service import _check_daily_budget_meets_minimum

    spec = _spec("community", 2970)  # 2970 / 30 = 99 ₽/день
    with pytest.raises(BudgetBelowMinimumError) as excinfo:
        _check_daily_budget_meets_minimum(spec)
    assert excinfo.value.minimum == 100
    assert excinfo.value.actual == 99.0
    assert excinfo.value.surface_title == "Сообщество ВКонтакте"


def test_exactly_at_minimum_is_accepted() -> None:
    from services.launch_service import _check_daily_budget_meets_minimum

    spec = _spec("community", 3000)  # ровно 100 ₽/день
    _check_daily_budget_meets_minimum(spec)  # не бросает


def test_budget_to_discuss_does_not_block() -> None:
    from services.launch_service import _check_daily_budget_meets_minimum

    spec = _spec("community", None, needs_discussion=True)
    _check_daily_budget_meets_minimum(spec)  # не бросает — сравнивать не с чем


def test_dzen_below_its_own_minimum_is_rejected() -> None:
    from services.launch_service import _check_daily_budget_meets_minimum

    spec = _spec("dzen_channel", 50000)  # 50000 / 30 ≈ 1666.67 ₽/день < 10000
    with pytest.raises(BudgetBelowMinimumError) as excinfo:
        _check_daily_budget_meets_minimum(spec)
    assert excinfo.value.minimum == 10000
    assert excinfo.value.surface_title == "Канал Дзен"


def test_dzen_at_its_own_minimum_is_accepted() -> None:
    from services.launch_service import _check_daily_budget_meets_minimum

    spec = _spec("dzen_channel", 300000)  # 300000 / 30 = 10000 ₽/день
    _check_daily_budget_meets_minimum(spec)  # не бросает


# --- ядро: полный путь запуска, отказ ДО побочных эффектов --------------------

_VALID_LOW_BUDGET = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "2970",  # 2970 / 30 = 99 ₽/день — ниже минимума сообщества (100 ₽)
    "term": "1 месяц",
    "target_type": "личная страница",
}


@pytest.fixture(autouse=True)
def _mock_vk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)
    # `launch_preview` (эндпоинт превью, без явных `settings`) берёт настройки
    # своим собственным `get_settings()` — тем же ключом шифрования, что и
    # `add_account`/`_settings()` ниже, иначе расшифровка токена кабинета
    # сломается на ровном месте (свежий случайный ключ на вызов).
    monkeypatch.setattr(ad_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(launch_service, "get_settings", lambda: _settings())


class _FakeLiveAdapter(PlatformAdapter):
    """Боевой канал в тестах: без сети, с записью вызовов create_campaign."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._counter = 0

    async def health_check(self) -> bool:
        return True

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return f"live-cabinet-{client_ref}"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        self._counter += 1
        self.calls.append("create_campaign")
        return f"live-camp-{self._counter}"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return f"live-content-{campaign_id}"

    async def launch(self, campaign_id: str) -> None:
        self.calls.append("launch")

    async def stop(self, campaign_id: str) -> None:
        self.calls.append("stop")

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {}


def _router(adapter: PlatformAdapter) -> ChannelRouter:
    return ChannelRouter({Channel.VK_API: adapter}, ChannelConfig(default=Channel.VK_API))


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "vk_ads_secret_key": SecretStr(_KEY)}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _live_settings(**overrides: object) -> Settings:
    return _settings(vk_live_campaigns=True, **overrides)


async def _with_db(
    scenario: Callable[[AsyncSession], Awaitable[T]], *, payload: dict[str, str] | None = None
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
        session.add(Client(id=1, account_id=1, full_name="Вячеслав", email="v@example.com"))
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=1,
                variant="individual",
                payload=dict(payload or _VALID_LOW_BUDGET),
            )
        )
        await session.commit()
        await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_below_minimum_blocks_before_any_side_effect() -> None:
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(BudgetBelowMinimumError):
            await launch_from_creative(
                session,
                1,
                1,
                "photo",
                "/x.jpg",
                None,
                None,
                settings=_live_settings(),
                router=_router(adapter),
            )
        campaigns = await list_campaigns_for_brief(session, 1, 1)
        assert campaigns == []  # ни одной кампании не создано
        assert adapter.calls == []  # площадку вообще не звали

    asyncio.run(_with_db(scenario))


def test_budget_at_minimum_launches_normally() -> None:
    """Ровно на пороге (100 ₽/день) — проверка не срабатывает, площадку зовут.

    Автозапуск (`vk_campaign_autostart`) выключен по умолчанию (тот же
    предохранитель, что и в `test_launch_relaunch_guard.py`), поэтому кампания
    создаётся, но не запускается — статус `prepared`, а не `launched`.
    """
    adapter = _FakeLiveAdapter()
    payload = dict(_VALID_LOW_BUDGET, budget="3000")  # ровно 100 ₽/день

    async def scenario(session: AsyncSession) -> None:
        outcome = await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/x.jpg",
            None,
            None,
            settings=_live_settings(),
            router=_router(adapter),
        )
        assert outcome.campaign_status == "prepared"
        assert adapter.calls == ["create_campaign"]  # площадку позвали — проверка не заблокировала

    asyncio.run(_with_db(scenario, payload=payload))


def test_launch_without_creative_is_also_guarded_by_the_minimum() -> None:
    """Проверка живёт в общей `launch_from_creative` — путь без креатива
    (файл/тайтл/боди `None`) наследует её напрямую, как и защита от повтора (задача 6)."""
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(BudgetBelowMinimumError):
            await launch_from_creative(
                session,
                1,
                1,
                "",
                None,
                None,
                None,
                settings=_live_settings(),
                router=_router(adapter),
            )

    asyncio.run(_with_db(scenario))


# --- ядро: launch_preview показывает предупреждение, не блокирует ------------


BRIEF_PAYLOAD_OK = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",  # 30000 / 30 = 1000 ₽/день — выше минимума сообщества
    "term": "1 месяц",
    "target_type": "личная страница",
}

BRIEF_PAYLOAD_DZEN_LOW = dict(BRIEF_PAYLOAD_OK, budget="50000", target_type="канал Дзен")


async def _with_admin_client(
    scenario: Callable[[AsyncClient, async_sessionmaker[AsyncSession]], Awaitable[T]],
    *,
    payload: dict[str, str],
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
        session.add(Client(id=1, account_id=1, full_name="Клиент брифа", email="c@example.com"))
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=1,
                variant="individual",
                status="received",
                payload=dict(payload),
            )
        )
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("admin_session", generate_admin_session(555, _ADMIN_SECRET))
        result = await scenario(client, maker)
    await engine.dispose()
    return result


async def _add_cabinet(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        view = await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()
        return view.id


def test_launch_preview_reports_the_surface_minimum_and_no_warning_when_above() -> None:
    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin_client(scenario, payload=BRIEF_PAYLOAD_OK))
    assert data["min_daily_budget_rub"] == 100
    assert data["daily_budget_rub"] == 1000.0
    assert data["budget_below_minimum"] is False


def test_launch_preview_warns_when_budget_below_the_surface_minimum() -> None:
    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin_client(scenario, payload=BRIEF_PAYLOAD_DZEN_LOW))
    assert data["min_daily_budget_rub"] == 10000
    assert round(data["daily_budget_rub"], 2) == pytest.approx(1666.67, abs=0.01)
    assert data["budget_below_minimum"] is True


# --- API: 422 `budget_below_minimum` во всех местах маппинга ------------------


async def _with_bare_client(scenario: Callable[[AsyncClient], Awaitable[T]]) -> T:
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


def test_launch_endpoint_maps_budget_below_minimum_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise BudgetBelowMinimumError(100, 99.0, "Сообщество ВКонтакте")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={})

    resp = asyncio.run(_with_bare_client(scenario))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "budget_below_minimum"


def test_creative_endpoint_maps_budget_below_minimum_to_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise BudgetBelowMinimumError(10000, 1666.67, "Канал Дзен")

    monkeypatch.setattr(briefs_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post(
            "/api/v1/briefs/5/creative", json={"media_b64": "AAAA", "media_type": "photo"}
        )

    resp = asyncio.run(_with_bare_client(scenario))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "budget_below_minimum"


def test_admin_creative_endpoint_maps_budget_below_minimum_to_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Зеркало админки (`core/api/v1/admin_data.py`) — тот же код, не 500."""

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        raise BudgetBelowMinimumError(100, 50.0, "Сообщество ВКонтакте")

    monkeypatch.setattr(admin_data_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        client.cookies.set("admin_session", generate_admin_session(555, _ADMIN_SECRET))
        return await client.post(
            "/api/v1/admin/briefs/5/creative", json={"media_b64": "AAAA", "media_type": "photo"}
        )

    resp = asyncio.run(_with_bare_client(scenario))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "budget_below_minimum"


# --- бот: карточка подтверждения, причина отказа, /surfaces -------------------

_CORE = "http://api:8000"


def _card(**over: Any) -> BriefCard:
    base: dict[str, Any] = {
        "brief_id": 9,
        "variant": "individual",
        "status": "received",
        "client_name": "Иван Петров",
        "client_email": None,
        "client_phone": None,
        "client_telegram": None,
        "fields": [
            BriefFieldItem(n=13, label="Бюджет", value="2 970 ₽"),
            BriefFieldItem(n=14, label="Срок / период", value="месяц"),
        ],
        "has_creative": False,
        "campaign_status": None,
        "client_id": 42,
    }
    base.update(over)
    return BriefCard(**base)


def _account(**over: Any) -> Any:
    from bot.api_client import AdAccountItem

    base: dict[str, Any] = {
        "id": 3,
        "title": "Кабинет Ромашки",
        "external_id": "10000003",
        "username": None,
        "token_tail": "abcd",
        "advertiser_kind": "owner",
        "advertiser_name": None,
        "advertiser_inn": None,
        "status": "active",
        "health": "healthy",
        "health_checked_at": None,
        "health_error": None,
        "balance_rub": None,
        "is_usable": True,
        "client_id": None,
        "client_name": None,
    }
    base.update(over)
    return AdAccountItem(**base)


def test_confirmation_card_warns_when_budget_below_surface_minimum() -> None:
    """2 970 ₽/мес на сообществе = 99 ₽/день — ниже минимума 100 ₽."""
    card = _card(surface_title="Сообщество ВКонтакте", surface_min_daily_budget_rub=100)
    text = creative.render_launch_confirmation(card, _account(), "Подписчики")

    assert "ниже минимума" in text
    assert "Сообщество ВКонтакте" in text.split("ниже минимума")[1]
    assert "100 ₽" in text
    assert "Запуск будет отклонён" in text


def test_confirmation_card_silent_when_budget_meets_the_minimum() -> None:
    card = _card(
        fields=[BriefFieldItem(n=13, label="Бюджет", value="10 000 ₽")],
        surface_min_daily_budget_rub=100,
    )
    text = creative.render_launch_confirmation(card, _account(), "Подписчики")

    assert "ниже минимума" not in text


def test_confirmation_card_silent_when_budget_needs_discussion() -> None:
    card = _card(
        fields=[BriefFieldItem(n=13, label="Бюджет", value="готов обсудить")],
        surface_min_daily_budget_rub=100,
    )
    text = creative.render_launch_confirmation(card, _account(), "Подписчики")

    assert "ниже минимума" not in text


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("bot.api_client.get_settings", lambda: SimpleNamespace(core_base_url=_CORE))


def test_budget_below_minimum_shown_as_human_text_on_creative_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/briefs/9/creative").mock(
                return_value=httpx.Response(422, json={"detail": "budget_below_minimum"})
            )
            with pytest.raises(CreativeRejected) as excinfo:
                await api_client.upload_creative(
                    9, "YQ==", "photo", 800, 800, "T", "B", ad_account_id=3, goal="subscribers"
                )
        reason = excinfo.value.reason
        assert "budget_below_minimum" not in reason
        assert "минимум" in reason.lower()

    asyncio.run(scenario())


def test_budget_below_minimum_shown_as_human_text_on_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/briefs/9/launch").mock(
                return_value=httpx.Response(422, json={"detail": "budget_below_minimum"})
            )
            with pytest.raises(CreativeRejected) as excinfo:
                await api_client.launch_brief(9, ad_account_id=3)
        reason = excinfo.value.reason
        assert "budget_below_minimum" not in reason
        assert "минимум" in reason.lower()

    asyncio.run(scenario())


def test_surfaces_command_shows_dzen_minimum_but_not_the_default_one() -> None:
    from bot.handlers.surfaces import render_surfaces

    text = render_surfaces()
    assert "10 000 ₽/день" in text

    # Площадка со стандартным минимумом (100 ₽) не должна показывать цифру: три
    # строки этой площадки (заголовок, «в бриф: …», подсказка) без четвёртой.
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if "Сообщество ВКонтакте" in line)
    block = "\n".join(lines[start : start + 3])
    assert "минимальный бюджет" not in block
