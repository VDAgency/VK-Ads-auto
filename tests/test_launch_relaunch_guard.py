"""Тесты защиты от повторного запуска по брифу в ядре (задача 6, spec §F).

Второй запуск по брифу, у которого уже есть кампания в незавершённом статусе на
боевом канале, должен отказать ДО любых побочных эффектов (адаптер не вызван,
новых `Creative`/`Campaign` нет) — и на пути с креативом, и без него.
`allow_relaunch=True` снимает проверку; кампании на заглушке-фолбэке и в
статусах `stopped`/`failed` не мешают.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import core.api.v1.briefs as briefs_module
import pytest
import services.ad_accounts as ad_accounts
from config.settings import Settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Client
from db.repositories import list_campaigns_for_brief, set_campaign_status
from db.session import get_session
from httpx import ASGITransport, AsyncClient, Response
from integrations.adapter import PlatformAdapter
from integrations.channels import Channel, ChannelConfig, ChannelRouter
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.creative_intake import launch_without_creative
from services.launch_service import (
    CampaignAlreadyExistsError,
    LaunchOutcome,
    launch_from_creative,
)
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()
IDENTITY = VkIdentity(
    external_id="10000001",
    username="a1b2c3d4e5@agency_client",
    title="Кабинет «Пример»",
    status="active",
)

_VALID = {
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


@pytest.fixture(autouse=True)
def _mock_vk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


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


async def _with_db(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
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
            Brief(id=1, account_id=1, client_id=1, variant="individual", payload=dict(_VALID))
        )
        await session.commit()
        # Единственный активный кабинет оператора — запуск без `ad_account_id`
        # резолвится в него (`resolve_default_account`).
        await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


# --- ядро: путь с креативом ---------------------------------------------------


def test_second_launch_on_real_channel_is_blocked() -> None:
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> None:
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
        await session.commit()
        with pytest.raises(CampaignAlreadyExistsError) as excinfo:
            await launch_from_creative(
                session,
                1,
                1,
                "photo",
                "/y.jpg",
                None,
                None,
                settings=_live_settings(),
                router=_router(adapter),
            )
        assert excinfo.value.status == "prepared"
        campaigns = await list_campaigns_for_brief(session, 1, 1)
        assert len(campaigns) == 1  # второй запуск не создал новую строку
        assert adapter.calls == ["create_campaign"]  # площадку второй раз не звали

    asyncio.run(_with_db(scenario))


def test_second_launch_with_allow_relaunch_succeeds() -> None:
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> tuple[int, list[str]]:
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
        await session.commit()
        outcome = await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/y.jpg",
            None,
            None,
            settings=_live_settings(),
            router=_router(adapter),
            allow_relaunch=True,
        )
        await session.commit()
        campaigns = await list_campaigns_for_brief(session, 1, 1)
        assert outcome.campaign_status == "prepared"
        return len(campaigns), adapter.calls

    count, calls = asyncio.run(_with_db(scenario))
    assert count == 2
    assert calls.count("create_campaign") == 2


def test_relaunch_allowed_when_previous_campaign_stopped() -> None:
    """Кампания в терминальном статусе (`stopped`) не блокирует повторный запуск."""
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> int:
        first = await launch_from_creative(
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
        await session.commit()
        await set_campaign_status(session, 1, first.campaign_id, "stopped")
        await session.commit()
        # Без allow_relaunch — и без ошибки, статус старой кампании уже не активный.
        await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/y.jpg",
            None,
            None,
            settings=_live_settings(),
            router=_router(adapter),
        )
        await session.commit()
        campaigns = await list_campaigns_for_brief(session, 1, 1)
        return len(campaigns)

    assert asyncio.run(_with_db(scenario)) == 2


def test_relaunch_allowed_when_previous_campaign_is_stub() -> None:
    """Кампания заглушки-фолбэка не считается — иначе после честного отказа
    боевого канала повторить запуск было бы вообще нельзя."""

    async def scenario(session: AsyncSession) -> int:
        # Настройки по умолчанию (`_settings()`) → канал по умолчанию заглушка.
        await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=_settings()
        )
        await session.commit()
        await launch_from_creative(
            session, 1, 1, "photo", "/y.jpg", None, None, settings=_settings()
        )
        await session.commit()
        campaigns = await list_campaigns_for_brief(session, 1, 1)
        return len(campaigns)

    assert asyncio.run(_with_db(scenario)) == 2


# --- ядро: путь без креатива ---------------------------------------------------


def test_launch_without_creative_is_also_guarded() -> None:
    """Проверка живёт в общей `launch_from_creative` — путь без креатива
    (`services.creative_intake.launch_without_creative`) наследует её напрямую."""
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> None:
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
        await session.commit()
        with pytest.raises(CampaignAlreadyExistsError):
            await launch_without_creative(session, 1, 1, settings=_live_settings())

    asyncio.run(_with_db(scenario))


# --- эндпоинты: маппинг 409 -----------------------------------------------------


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


def test_launch_endpoint_maps_campaign_already_exists_to_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        captured.update(kwargs)
        raise CampaignAlreadyExistsError(1, "launched")

    monkeypatch.setattr(briefs_module, "launch_without_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "campaign_already_exists"
    assert captured["allow_relaunch"] is False


def test_launch_endpoint_forwards_allow_relaunch_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake(*args: Any, **kwargs: Any) -> LaunchOutcome:
        captured.update(kwargs)
        return LaunchOutcome(campaign_status="prepared", campaign_id=1, message="ok")

    monkeypatch.setattr(briefs_module, "launch_without_creative", fake)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/launch", json={"allow_relaunch": True})

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 201, resp.text
    assert captured["allow_relaunch"] is True


def _creative_body() -> dict[str, Any]:
    return {"media_b64": "AAAA", "media_type": "photo"}


def test_creative_endpoint_maps_campaign_already_exists_to_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def boom(*args: Any, **kwargs: Any) -> LaunchOutcome:
        captured.update(kwargs)
        raise CampaignAlreadyExistsError(1, "moderation")

    monkeypatch.setattr(briefs_module, "intake_creative", boom)

    async def scenario(client: AsyncClient) -> Response:
        return await client.post("/api/v1/briefs/5/creative", json=_creative_body())

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "campaign_already_exists"
    assert captured["allow_relaunch"] is False


def test_creative_endpoint_forwards_allow_relaunch_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake(*args: Any, **kwargs: Any) -> LaunchOutcome:
        captured.update(kwargs)
        return LaunchOutcome(campaign_status="prepared", campaign_id=1, message="ok")

    monkeypatch.setattr(briefs_module, "intake_creative", fake)

    async def scenario(client: AsyncClient) -> Response:
        body = _creative_body()
        body["allow_relaunch"] = True
        return await client.post("/api/v1/briefs/5/creative", json=body)

    resp = asyncio.run(_with_client(scenario))
    assert resp.status_code == 201, resp.text
    assert captured["allow_relaunch"] is True
