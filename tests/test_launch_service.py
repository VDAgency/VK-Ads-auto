"""Тесты сшивки запуска РК (`services/launch_service`).

Покрывают: сборку `ChannelRouter` по флагам боевого режима, порядок
«Creative → Cabinet → кампания», статусы кампании (prepared/launched/moderation),
честный фолбэк на заглушку с уведомлением оператора и остановку кампании.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
import pytest
import respx
from config.settings import Settings
from db.base import Base
from db.models import Account, Brief, Cabinet, Client
from db.repositories import (
    get_creative_for_brief,
    get_latest_campaign_for_brief,
)
from integrations.adapter import PlatformAdapter
from integrations.channels import Channel, ChannelConfig, ChannelRouter
from integrations.kotbot_http import KotbotAdapter
from integrations.stub import StubAdapter
from integrations.vk_api import VkApiAdapter
from services import notifier
from services.launch_service import (
    _build_adapters,
    _build_router,
    _channel_config,
    launch_from_creative,
    stop_campaign,
)
from services.mapping import CampaignSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_KOTBOT_URL = "http://kotbot:8002"

_VALID = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "vk_ad_cabinet_id": "13410929",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "личная страница",
}


class _FakeLiveAdapter(PlatformAdapter):
    """Боевой канал в тестах: без сети, с записью вызовов."""

    def __init__(self, *, healthy: bool = True, status: str = "active") -> None:
        self._healthy = healthy
        self._status = status
        self.calls: list[tuple[str, str]] = []

    async def health_check(self) -> bool:
        return self._healthy

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        self.calls.append(("create_cabinet", client_ref))
        return f"live-cabinet-{client_ref}"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        self.calls.append(("create_campaign", cabinet_id))
        return "live-camp-1"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        self.calls.append(("upload_creative", creative_ref))
        return "live-content-1"

    async def launch(self, campaign_id: str) -> None:
        self.calls.append(("launch", campaign_id))

    async def stop(self, campaign_id: str) -> None:
        self.calls.append(("stop", campaign_id))

    async def get_status(self, campaign_id: str) -> str:
        return self._status

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {}


def _router(adapter: PlatformAdapter) -> ChannelRouter:
    return ChannelRouter({Channel.VK_API: adapter}, ChannelConfig(default=Channel.VK_API))


def _live_settings(**overrides: object) -> Settings:
    """Настройки «боевой канал ожидается»: токен есть и создание кампаний разрешено."""
    values: dict[str, object] = {
        "_env_file": None,
        "vk_ads_access_token": "tok",
        "vk_live_campaigns": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


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
        result = await scenario(session)
    await engine.dispose()
    return result


# --- сборка каналов по флагам -----------------------------------------------------


def test_vk_channel_is_stub_without_token() -> None:
    adapters = _build_adapters(Settings(_env_file=None))
    assert isinstance(adapters[Channel.VK_API], StubAdapter)


def test_vk_channel_is_stub_while_live_campaigns_disabled() -> None:
    # Токен есть, но боевое создание кампаний не разрешено → заглушка.
    adapters = _build_adapters(Settings(_env_file=None, vk_ads_access_token="tok"))
    assert isinstance(adapters[Channel.VK_API], StubAdapter)


def test_vk_channel_is_live_with_token_and_flag() -> None:
    adapters = _build_adapters(_live_settings())
    assert isinstance(adapters[Channel.VK_API], VkApiAdapter)


def test_kotbot_channel_only_when_base_url_set() -> None:
    assert Channel.KOTBOT not in _build_adapters(Settings(_env_file=None))
    adapters = _build_adapters(Settings(_env_file=None, kotbot_base_url=_KOTBOT_URL))
    assert isinstance(adapters[Channel.KOTBOT], KotbotAdapter)


def test_default_channel_is_kotbot_when_configured() -> None:
    config = _channel_config(Settings(_env_file=None, kotbot_base_url="http://kotbot:8002"))
    assert config.default is Channel.KOTBOT
    assert config.forced is None


def test_forced_channel_from_settings() -> None:
    config = _channel_config(Settings(_env_file=None, integration_forced_channel="kotbot"))
    assert config.forced is Channel.KOTBOT


def test_unknown_forced_channel_is_ignored() -> None:
    config = _channel_config(Settings(_env_file=None, integration_forced_channel="telepathy"))
    assert config.forced is None


def test_stub_is_guaranteed_healthy_fallback() -> None:
    router = _build_router(Settings(_env_file=None))
    channel, adapter = asyncio.run(router.select())
    assert channel is Channel.VK_API
    assert isinstance(adapter, StubAdapter)


# --- запуск: заглушка --------------------------------------------------------------


def test_launch_from_creative_prepares_campaign_and_persists() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, str, str]:
        outcome = await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/data/creatives/1/x.jpg",
            "Заголовок",
            "Текст",
            settings=Settings(_env_file=None),  # без токена → заглушка
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        creative = await get_creative_for_brief(session, 1, 1)
        assert campaign is not None and creative is not None
        return outcome.campaign_status, campaign.status, creative.file_path

    outcome_status, campaign_status, file_path = asyncio.run(_with_db(scenario))
    assert outcome_status == "prepared"
    assert campaign_status == "prepared"
    assert file_path == "/data/creatives/1/x.jpg"


def test_launch_persists_spec_and_stub_external_id() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, dict[str, object]]:
        await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=Settings(_env_file=None)
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        return campaign.external_id or "", dict(campaign.spec_json)

    external_id, spec = asyncio.run(_with_db(scenario))
    assert external_id.startswith("stub-campaign")
    assert spec["objective"] == "socialengagement"


# --- кабинет: reuse-or-create до создания кампании ---------------------------------


def test_cabinet_row_is_created_and_linked_to_campaign() -> None:
    async def scenario(session: AsyncSession) -> tuple[int, str, int | None]:
        await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=Settings(_env_file=None)
        )
        await session.commit()
        cabinets = list((await session.execute(select(Cabinet))).scalars().all())
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        return len(cabinets), cabinets[0].channel, campaign.cabinet_id

    count, channel, cabinet_id = asyncio.run(_with_db(scenario))
    assert count == 1
    assert channel == "stub"
    assert cabinet_id is not None


def test_second_launch_reuses_existing_cabinet() -> None:
    async def scenario(session: AsyncSession) -> tuple[int, set[int | None]]:
        cabinet_ids: set[int | None] = set()
        for _ in range(2):
            await launch_from_creative(
                session, 1, 1, "photo", "/x.jpg", None, None, settings=Settings(_env_file=None)
            )
            await session.commit()
            campaign = await get_latest_campaign_for_brief(session, 1, 1)
            assert campaign is not None
            cabinet_ids.add(campaign.cabinet_id)
        cabinets = list((await session.execute(select(Cabinet))).scalars().all())
        return len(cabinets), cabinet_ids

    count, cabinet_ids = asyncio.run(_with_db(scenario))
    assert count == 1
    assert len(cabinet_ids) == 1


def test_cabinet_from_brief_is_not_created_on_platform() -> None:
    # В брифе указан существующий кабинет VK — боевой канал его не пересоздаёт.
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> str:
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
        cabinet = (await session.execute(select(Cabinet))).scalars().one()
        return cabinet.external_ref or ""

    external_ref = asyncio.run(_with_db(scenario))
    assert external_ref == "13410929"
    assert not any(call[0] == "create_cabinet" for call in adapter.calls)


# --- статусы боевого канала --------------------------------------------------------


def test_live_channel_without_autostart_prepares_but_does_not_launch() -> None:
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> tuple[str, str]:
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
        await session.commit()
        return outcome.campaign_status, outcome.message

    status, message = asyncio.run(_with_db(scenario))
    assert status == "prepared"
    assert not any(call[0] == "launch" for call in adapter.calls)
    assert "НЕ запущена" in message


def test_live_channel_with_autostart_launches() -> None:
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> tuple[str, str | None]:
        outcome = await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/x.jpg",
            None,
            None,
            settings=_live_settings(vk_campaign_autostart=True),
            router=_router(adapter),
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        return outcome.campaign_status, campaign.external_id

    status, external_id = asyncio.run(_with_db(scenario))
    assert status == "launched"
    assert external_id == "live-camp-1"
    assert ("launch", "live-camp-1") in adapter.calls


def test_moderation_status_is_refined_from_platform() -> None:
    adapter = _FakeLiveAdapter(status="moderation")

    async def scenario(session: AsyncSession) -> str:
        outcome = await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/x.jpg",
            None,
            None,
            settings=_live_settings(vk_campaign_autostart=True),
            router=_router(adapter),
        )
        await session.commit()
        return outcome.campaign_status

    assert asyncio.run(_with_db(scenario)) == "moderation"


# --- честный фолбэк на заглушку ----------------------------------------------------


def test_fallback_to_stub_warns_operator() -> None:
    sent: list[str] = []

    async def sender(text: str) -> None:
        sent.append(text)

    notifier.register_operator_notifier(sender)
    unhealthy = ChannelRouter(
        {Channel.VK_API: _FakeLiveAdapter(healthy=False)},
        ChannelConfig(default=Channel.VK_API),
    )

    async def scenario(session: AsyncSession) -> tuple[str, str]:
        outcome = await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/x.jpg",
            None,
            None,
            settings=_live_settings(vk_campaign_autostart=True),
            router=unhealthy,
        )
        await session.commit()
        return outcome.campaign_status, outcome.message

    try:
        status, message = asyncio.run(_with_db(scenario))
    finally:
        notifier.reset_operator_notifier()

    assert status == "prepared"
    assert "⚠️" in message
    assert sent and "⚠️" in sent[0]


def _kotbot_settings() -> Settings:
    """Настройки «канал kotbot включён»: боевой канал ожидается, автозапуск разрешён."""
    return Settings(_env_file=None, kotbot_base_url=_KOTBOT_URL, vk_campaign_autostart=True)


async def _launch_with_kotbot(session: AsyncSession, action: httpx.Response) -> tuple[str, str]:
    """Запуск при живом `/health` kotbot и заданном ответе action-эндпоинта.

    Проверяем заодно, что канал kotbot действительно был выбран и опрошен, —
    иначе фолбэк доказывал бы только нездоровый health, а не отказ действия.
    """
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_KOTBOT_URL}/health").mock(
            return_value=httpx.Response(200, json={"healthy": True, "strategies": {}})
        )
        campaigns = router.post(f"{_KOTBOT_URL}/campaigns").mock(return_value=action)
        outcome = await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=_kotbot_settings()
        )
        assert campaigns.called
    await session.commit()
    return outcome.campaign_status, outcome.message


def _collect_notifications() -> list[str]:
    """Подписаться на уведомления оператора и вернуть накопитель."""
    sent: list[str] = []

    async def sender(text: str) -> None:
        sent.append(text)

    notifier.register_operator_notifier(sender)
    return sent


def test_kotbot_not_implemented_falls_back_to_stub_without_fake_success() -> None:
    # Сервис kotbot жив (health ok), но живые флоу ещё не написаны: action → 501.
    # Запуск обязан выглядеть как «подготовлена, но не запущена», а не как успех.
    sent = _collect_notifications()

    async def scenario(session: AsyncSession) -> tuple[str, str, str, str]:
        status, message = await _launch_with_kotbot(
            session, httpx.Response(501, json={"detail": "not_implemented"})
        )
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        return status, message, campaign.status, campaign.external_id or ""

    try:
        status, message, persisted_status, external_id = asyncio.run(_with_db(scenario))
    finally:
        notifier.reset_operator_notifier()

    assert status == "prepared"
    assert persisted_status == "prepared"
    assert external_id.startswith("stub-campaign")
    assert "не запущена" in message
    assert sent and "⚠️" in sent[0]


def test_kotbot_reauth_required_falls_back_to_stub() -> None:
    # 409 = сессия протухла, нужен оператор: тот же честный путь, без «успеха».
    sent = _collect_notifications()

    async def scenario(session: AsyncSession) -> tuple[str, str, str]:
        status, message = await _launch_with_kotbot(
            session, httpx.Response(409, json={"detail": "reauth_required"})
        )
        cabinets = list((await session.execute(select(Cabinet))).scalars().all())
        return status, message, cabinets[-1].channel

    try:
        status, message, cabinet_channel = asyncio.run(_with_db(scenario))
    finally:
        notifier.reset_operator_notifier()

    assert status == "prepared"
    assert "⚠️" in message
    assert cabinet_channel == "stub"
    assert sent


def test_stub_failure_is_not_swallowed() -> None:
    # Падение самой заглушки — это баг, а не отказ канала: наружу должно лететь.
    class _BrokenStub(StubAdapter):
        async def create_campaign_from_spec(
            self,
            cabinet_id: str,
            spec: CampaignSpec,
            *,
            creative_ref: str | None = None,
            title: str | None = None,
            body: str | None = None,
            budget_limit_day: float | None = None,
        ) -> str:
            raise RuntimeError("boom")

    async def scenario(session: AsyncSession) -> None:
        await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/x.jpg",
            None,
            None,
            settings=Settings(_env_file=None),
            router=ChannelRouter(
                {Channel.VK_API: _BrokenStub()}, ChannelConfig(default=Channel.VK_API)
            ),
        )

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_with_db(scenario))


# --- остановка кампании ------------------------------------------------------------


def test_stop_campaign_sets_status_and_calls_adapter() -> None:
    adapter = _FakeLiveAdapter()

    async def scenario(session: AsyncSession) -> str:
        await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/x.jpg",
            None,
            None,
            settings=_live_settings(vk_campaign_autostart=True),
            router=_router(adapter),
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        stopped = await stop_campaign(
            session, 1, campaign.id, settings=_live_settings(), adapter=adapter
        )
        await session.commit()
        assert stopped is not None
        return stopped.status

    assert asyncio.run(_with_db(scenario)) == "stopped"
    assert ("stop", "live-camp-1") in adapter.calls


def test_stop_campaign_returns_none_for_other_tenant() -> None:
    async def scenario(session: AsyncSession) -> object:
        await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=Settings(_env_file=None)
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        return await stop_campaign(session, 999, campaign.id, settings=Settings(_env_file=None))

    assert asyncio.run(_with_db(scenario)) is None


def test_stop_campaign_of_stub_is_noop() -> None:
    async def scenario(session: AsyncSession) -> str:
        await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=Settings(_env_file=None)
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        stopped = await stop_campaign(session, 1, campaign.id, settings=Settings(_env_file=None))
        await session.commit()
        assert stopped is not None
        return stopped.status

    assert asyncio.run(_with_db(scenario)) == "stopped"


def test_spec_is_passed_to_adapter_untouched() -> None:
    # Ядро отдаёт адаптеру всю спеку (бюджет/таргетинг/объект), а не одну цель.
    seen: list[CampaignSpec] = []

    class _SpecCapture(_FakeLiveAdapter):
        async def create_campaign_from_spec(
            self,
            cabinet_id: str,
            spec: CampaignSpec,
            *,
            creative_ref: str | None = None,
            title: str | None = None,
            body: str | None = None,
            budget_limit_day: float | None = None,
        ) -> str:
            seen.append(spec)
            assert budget_limit_day is not None
            assert creative_ref == "/x.jpg"
            return "live-camp-1"

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
            router=_router(_SpecCapture()),
        )
        await session.commit()

    asyncio.run(_with_db(scenario))
    assert seen and seen[0].geo_raw == "Самара"
