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
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.community_tokens import save_community_token
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
from integrations.vk_community import VkCommunityUnreachable
from pydantic import SecretStr
from services import notifier
from services.ad_accounts import add_account
from services.launch_service import (
    LaunchOutcome,
    SenlerNotConnectedError,
    _build_adapters,
    _build_router,
    _channel_config,
    launch_from_creative,
    stop_campaign,
)
from services.mapping import CampaignSpec
from services.senler import SenlerCheck
from services.senler import detect_senler as _detect_senler_directly
from services.vk_identity import VkIdentity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_KOTBOT_URL = "http://kotbot:8002"

# Кабинет оператора, которым идёт запуск (spec 2026-07-27 §9): токен и внешний id
# кампании берутся из базы, а не из окружения/брифа — тесты заводят ровно один
# активный кабинет, чтобы `_resolve_ad_account` резолвил его по умолчанию.
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
    """VK всегда отвечает одной и той же личностью — кабинет в БД один и активен."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


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


def _settings(**overrides: object) -> Settings:
    """Настройки «канал по умолчанию — заглушка», с ключом шифрования кабинетов.

    Ключ обязателен ровно потому, что кабинет теперь заводится в БД (`_with_db`):
    без него `resolve_default_account`/`resolve_token` не расшифруют токен.
    """
    values: dict[str, object] = {"_env_file": None, "vk_ads_secret_key": SecretStr(_KEY)}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _live_settings(**overrides: object) -> Settings:
    """Настройки «боевой канал ожидается»: создание кампаний разрешено."""
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
        # Единственный активный кабинет оператора: запуск без явного `ad_account_id`
        # резолвится в него (`resolve_default_account`).
        await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


# --- сборка каналов по флагам -----------------------------------------------------


def test_vk_channel_is_stub_without_token() -> None:
    adapters = _build_adapters(Settings(_env_file=None), SecretStr(""))
    assert isinstance(adapters[Channel.VK_API], StubAdapter)


def test_vk_channel_is_stub_while_live_campaigns_disabled() -> None:
    # Токен есть, но боевое создание кампаний не разрешено → заглушка.
    adapters = _build_adapters(Settings(_env_file=None), SecretStr("tok"))
    assert isinstance(adapters[Channel.VK_API], StubAdapter)


def test_vk_channel_is_live_with_token_and_flag() -> None:
    adapters = _build_adapters(_live_settings(), SecretStr("tok"))
    assert isinstance(adapters[Channel.VK_API], VkApiAdapter)


def test_kotbot_channel_only_when_base_url_set() -> None:
    assert Channel.KOTBOT not in _build_adapters(Settings(_env_file=None), SecretStr(""))
    adapters = _build_adapters(Settings(_env_file=None, kotbot_base_url=_KOTBOT_URL), SecretStr(""))
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
    router = _build_router(Settings(_env_file=None), SecretStr(""))
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
            settings=_settings(),  # vk_live_campaigns выключен → заглушка
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
            session, 1, 1, "photo", "/x.jpg", None, None, settings=_settings()
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
            session, 1, 1, "photo", "/x.jpg", None, None, settings=_settings()
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
                session, 1, 1, "photo", "/x.jpg", None, None, settings=_settings()
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


def test_cabinet_external_ref_comes_from_ad_account_not_brief() -> None:
    """Внешний ref кабинета берётся у рекламного кабинета оператора, не у брифа.

    Поле `vk_ad_cabinet_id` в брифе удалено (кампании идут в кабинет оператора из
    базы): боевой канал в любом случае кабинет на площадке не пересоздаёт.
    """
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
    assert external_ref == IDENTITY.external_id
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
    return _settings(kotbot_base_url=_KOTBOT_URL, vk_campaign_autostart=True)


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
            activate: bool = False,
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
            settings=_settings(),
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
            session, 1, 1, "photo", "/x.jpg", None, None, settings=_settings()
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        return await stop_campaign(session, 999, campaign.id, settings=_settings())

    assert asyncio.run(_with_db(scenario)) is None


def test_stop_campaign_of_stub_is_noop() -> None:
    async def scenario(session: AsyncSession) -> str:
        await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=_settings()
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        stopped = await stop_campaign(session, 1, campaign.id, settings=_settings())
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
            activate: bool = False,
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


# --- Senler: проверка подключения чат-бота перед запуском --------------------
#
# Решение B2 (spec 2026-08-24 §7): токен есть, Senler явно не подключён ->
# отказ (кампания не создаётся). Токена нет ИЛИ проверка не удалась (сеть) ->
# честное предупреждение в outcome.message, запуск всё равно продолжается -
# требовать токен с каждого клиента не будем, но и молчать о непроверенном
# нельзя (CLAUDE.md §7).

SENLER_COMMUNITY_ID = "228817082"
# Короткий адрес сообщества, привязанный при заводе токена (`groups.getById`,
# `core/api/v1/senler.py`) — клиенты в брифе почти всегда присылают именно его,
# а не числовой id, поэтому проверка обязана находить токен и по нему.
SENLER_SCREEN_NAME = "djbeauty"
SENLER_COMMUNITY_NAME = "DJ BEAUTY"
_SENLER_VALID = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/club228817082",
    "email": "senler-client@example.com",
    "phone": "+79990000001",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "заявка через senler",
}
# Тот же бриф, но ссылка — короткий адрес без числового id (главный сценарий
# брифа на практике).
_SENLER_SHORT_ADDRESS = {**_SENLER_VALID, "object_url": "https://vk.ru/djbeauty"}


async def _with_senler_db(
    scenario: Callable[[AsyncSession], Awaitable[T]], *, payload: dict[str, str] | None = None
) -> T:
    """Тот же стенд, что `_with_db`, но с брифом на цель Senler."""
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
                id=2,
                account_id=1,
                client_id=1,
                variant="individual",
                payload=dict(payload or _SENLER_VALID),
            )
        )
        await session.commit()
        await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


async def _save_senler_token(
    session: AsyncSession,
    token: str,
    *,
    community_id: str = SENLER_COMMUNITY_ID,
    screen_name: str = SENLER_SCREEN_NAME,
) -> None:
    await save_community_token(
        session,
        1,
        community_id,
        token,
        screen_name=screen_name,
        community_name=SENLER_COMMUNITY_NAME,
        settings=_settings(),
    )


def _launch_senler(session: AsyncSession) -> Awaitable[LaunchOutcome]:
    return launch_from_creative(
        session,
        1,
        2,
        "photo",
        "/data/creatives/2/x.jpg",
        "Заголовок",
        "Текст",
        settings=_settings(),
    )


def test_senler_launch_is_blocked_when_chatbot_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Токен привязан, VK подтверждает, что Senler НЕ подключён - запуск отклоняется."""

    async def fake_fetch(token: str, community_id: str, **_: object) -> list[dict[str, object]]:
        return []  # groups.getCallbackServers без callback-сервера Senler

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fake_fetch)

    async def scenario(session: AsyncSession) -> None:
        await _save_senler_token(session, "community-token")
        await session.commit()
        with pytest.raises(SenlerNotConnectedError):
            await _launch_senler(session)

    asyncio.run(_with_senler_db(scenario))


def test_senler_launch_blocked_leaves_no_campaign_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(token: str, community_id: str, **_: object) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fake_fetch)

    async def scenario(session: AsyncSession) -> object:
        await _save_senler_token(session, "community-token")
        await session.commit()
        with pytest.raises(SenlerNotConnectedError):
            await _launch_senler(session)
        return await get_latest_campaign_for_brief(session, 1, 2)

    assert asyncio.run(_with_senler_db(scenario)) is None


def test_senler_launch_proceeds_silently_when_chatbot_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Токен привязан, Senler подключён - запуск идёт как обычно, без предупреждений."""

    async def fake_fetch(token: str, community_id: str, **_: object) -> list[dict[str, object]]:
        assert token == "community-token"
        assert community_id == SENLER_COMMUNITY_ID
        return [
            {
                "title": "Senler",
                "url": "https://callback.senler.ru/webhook/vk/1",
                "status": "ok",
            }
        ]

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fake_fetch)

    async def scenario(session: AsyncSession) -> str:
        await _save_senler_token(session, "community-token")
        await session.commit()
        outcome = await _launch_senler(session)
        return outcome.message

    message = asyncio.run(_with_senler_db(scenario))
    assert "не удал" not in message.lower()


def test_senler_launch_warns_operator_when_token_is_missing() -> None:
    """Токена для этого сообщества нет - запуск продолжается, но с честным предупреждением."""

    async def scenario(session: AsyncSession) -> str:
        outcome = await _launch_senler(session)
        return outcome.message

    message = asyncio.run(_with_senler_db(scenario))
    assert "senler" in message.lower() or "подключ" in message.lower()


def test_senler_launch_does_not_block_on_missing_token() -> None:
    """Регресс: отсутствие токена - не повод отказывать в запуске (кампания создаётся)."""

    async def scenario(session: AsyncSession) -> object:
        await _launch_senler(session)
        await session.commit()
        return await get_latest_campaign_for_brief(session, 1, 2)

    assert asyncio.run(_with_senler_db(scenario)) is not None


def test_senler_launch_warns_when_vk_check_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Токен есть, но сходить в VK не вышло - предупреждаем, а не блокируем запуск."""

    async def fake_fetch(token: str, community_id: str, **_: object) -> list[dict[str, object]]:
        raise VkCommunityUnreachable("boom")

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fake_fetch)

    async def scenario(session: AsyncSession) -> tuple[str, object]:
        await _save_senler_token(session, "community-token")
        await session.commit()
        outcome = await _launch_senler(session)
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 2)
        return outcome.message, campaign

    message, campaign = asyncio.run(_with_senler_db(scenario))
    assert campaign is not None
    assert "не удал" in message.lower()


def test_non_senler_launch_never_calls_the_senler_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регресс: цели, отличные от Senler, вообще не трогают проверку подключения."""

    def fail_if_called(*_a: object, **_k: object) -> None:
        raise AssertionError("fetch_callback_servers must not be called for non-Senler goals")

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fail_if_called)

    async def scenario(session: AsyncSession) -> None:
        await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/data/creatives/1/x.jpg",
            "Заголовок",
            "Текст",
            settings=_settings(),
        )

    asyncio.run(_with_db(scenario))


def test_detect_senler_is_reused_by_launch_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сшивка действительно передаёт ответ VK через services.senler.detect_senler,
    а не изобретает свой разбор внутри launch_service."""
    seen: list[list[dict[str, object]]] = []

    def spy(servers: list[dict[str, object]]) -> SenlerCheck:
        seen.append(servers)
        return _detect_senler_directly(servers)

    async def fake_fetch(token: str, community_id: str, **_: object) -> list[dict[str, object]]:
        return [
            {"title": "Senler", "url": "https://callback.senler.ru/webhook/vk/1", "status": "ok"}
        ]

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fake_fetch)
    monkeypatch.setattr(launch_service, "detect_senler", spy)

    async def scenario(session: AsyncSession) -> None:
        await _save_senler_token(session, "community-token")
        await session.commit()
        await _launch_senler(session)

    asyncio.run(_with_senler_db(scenario))
    assert seen and seen[0][0]["title"] == "Senler"


# --- главный сценарий доработки: сообщество из брифа опознаётся по короткому
# адресу, а не только по числовому id ------------------------------------------


def test_senler_check_finds_the_token_by_short_address_from_the_brief_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Клиенты в брифе почти всегда присылают короткий адрес (`vk.ru/djbeauty`),
    а не числовой id — раньше проверка на этом молча сдавалась (`url_object_id`
    для такой ссылки не извлекается). Токен привязан к сообществу с коротким
    адресом `djbeauty`; бриф ссылается на `https://vk.ru/djbeauty` — без
    числового id вовсе. Проверка обязана найти токен и пройти как обычно."""

    async def fake_fetch(token: str, community_id: str, **_: object) -> list[dict[str, object]]:
        assert token == "community-token"
        # Найдя по короткому адресу, звоним в VK каноническим числовым id,
        # сохранённым при привязке токена, а не тем, что было в ссылке брифа.
        assert community_id == SENLER_COMMUNITY_ID
        return [
            {"title": "Senler", "url": "https://callback.senler.ru/webhook/vk/1", "status": "ok"}
        ]

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fake_fetch)

    async def scenario(session: AsyncSession) -> str:
        await _save_senler_token(session, "community-token")
        await session.commit()
        outcome = await _launch_senler(session)
        return outcome.message

    message = asyncio.run(_with_senler_db(scenario, payload=_SENLER_SHORT_ADDRESS))
    assert "не удал" not in message.lower()


def test_senler_check_still_finds_the_token_by_numeric_id_from_the_brief_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Регресс: `https://vk.com/club228817082` — ссылка с числовым id —
    по-прежнему находит токен того же сообщества."""

    async def fake_fetch(token: str, community_id: str, **_: object) -> list[dict[str, object]]:
        assert token == "community-token"
        assert community_id == SENLER_COMMUNITY_ID
        return [
            {"title": "Senler", "url": "https://callback.senler.ru/webhook/vk/1", "status": "ok"}
        ]

    monkeypatch.setattr(launch_service, "fetch_callback_servers", fake_fetch)

    async def scenario(session: AsyncSession) -> str:
        await _save_senler_token(session, "community-token")
        await session.commit()
        outcome = await _launch_senler(session)
        return outcome.message

    # _SENLER_VALID уже ссылается на https://vk.com/club228817082.
    message = asyncio.run(_with_senler_db(scenario))
    assert "не удал" not in message.lower()
