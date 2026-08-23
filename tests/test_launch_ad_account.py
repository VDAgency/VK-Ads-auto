"""Запуск кампании в выбранный рекламный кабинет (spec 2026-07-27 §9).

Ключевые свойства:
- в VK идём токеном ВЫБРАННОГО кабинета, а не токеном из окружения;
- кампания запоминает кабинет — иначе её нечем остановить;
- отозванный токен помечает кабинет мёртвым, а не прячется за общим фолбэком;
- предохранители не ослаблены: живой кабинет сам по себе ничего не разрешает.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
import pytest
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Cabinet, Campaign, Client
from db.repositories import get_ad_account
from pydantic import SecretStr
from services.ad_accounts import (
    AccountNotFoundError,
    AmbiguousAdAccountError,
    NoAdAccountError,
    TokenUnavailableError,
    add_account,
    delete_account,
)
from services.launch_service import (
    CampaignStopError,
    UnsupportedGoalError,
    launch_from_creative,
    stop_campaign,
)
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
TOKEN_2 = "fake-access-token-for-tests-0000000000000002"
_KEY = Fernet.generate_key().decode()

IDENTITY = VkIdentity("10000001", "a1b2c3d4e5@agency_client", "Студия «Пример»", "active")
IDENTITY_2 = VkIdentity("10000002", "f6g7h8i9j0@agency_client", "Кабинет «Второй»", "active")

BRIEF_PAYLOAD = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    # Специально отличается от id рекламного кабинета: проверяем, что на запуск
    # влияет выбор оператора, а не это поле (оно про kotbot).
    "vk_ad_cabinet_id": "99999999",
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
    """Заглушка канала, запоминающая, каким токеном её собрали."""

    def __init__(self, token: str) -> None:
        self.token = token

    async def health_check(self) -> bool:
        return True

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "created-cabinet"

    async def create_campaign_from_spec(self, cabinet_id: str, spec: Any, **kwargs: Any) -> str:
        RecordingAdapter.last_cabinet_ref = cabinet_id
        return "vk-campaign-1"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def get_status(self, campaign_id: str) -> str:
        return "active"

    async def stop(self, campaign_id: str) -> None:
        RecordingAdapter.stopped_with = self.token

    last_cabinet_ref = ""
    stopped_with = ""
    last_token = ""


class FakeVkAdapter(RecordingAdapter):
    """Подмена `VkApiAdapter`. Именно класс, а не функция: `_resolve_cabinet`
    проверяет тип через `isinstance`, и лямбда сломала бы эту ветку.
    """

    def __init__(self, access_token: SecretStr, **_: object) -> None:
        super().__init__(access_token.get_secret_value())
        RecordingAdapter.last_token = access_token.get_secret_value()


@pytest.fixture(autouse=True)
def _mock_vk_and_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)
    monkeypatch.setattr(ad_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        launch_service, "save_creative", lambda *a, **k: "creative.jpg", raising=False
    )


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
        session.add(Account(id=1, name="tenant-one"))
        session.add(Client(id=100, account_id=1, full_name="Вячеслав", email="v@example.com"))
        session.add(
            Brief(
                id=500,
                account_id=1,
                client_id=100,
                variant="individual",
                status="received",
                payload=dict(BRIEF_PAYLOAD),
            )
        )
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


async def _add_cabinet(session: AsyncSession) -> int:
    view = await add_account(session, 1, TOKEN, settings=_settings())
    await session.commit()
    return view.id


async def _add_second_cabinet(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> int:
    """Второй активный кабинет — с другим токеном и другой личностью VK."""

    async def identity_2(token: str, **_: object) -> VkIdentity:
        return IDENTITY_2

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity_2)
    view = await add_account(session, 1, TOKEN_2, settings=_settings())
    await session.commit()
    return view.id


async def _launch(
    session: AsyncSession,
    *,
    ad_account_id: int | None,
    settings: Settings,
    goal: str | None = None,
) -> Any:
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
        goal=goal,
    )


# --- выбор кабинета -----------------------------------------------------------


def test_launch_uses_selected_cabinet_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """В VK идём токеном выбранного кабинета, а не токеном из окружения."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        await _launch(session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True))
        assert RecordingAdapter.last_token == TOKEN
        assert RecordingAdapter.last_token != "env-token"

    asyncio.run(_with_db(scenario))


def test_launch_without_selection_uses_the_only_active_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кабинет не выбран, но он единственный активный — запуск идёт его токеном.

    `VK_ADS_ACCESS_TOKEN` для запуска больше не годится ни при каких условиях:
    токен всегда приходит из строки `ad_account` в базе.
    """
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session, ad_account_id=None, settings=_settings(vk_live_campaigns=True)
        )
        assert RecordingAdapter.last_token == TOKEN
        assert RecordingAdapter.last_token != "env-token"
        campaign = await session.get(Campaign, outcome.campaign_id)
        assert campaign is not None
        assert campaign.ad_account_id == cabinet_id

    asyncio.run(_with_db(scenario))


def test_launch_without_selection_and_zero_cabinets_is_rejected() -> None:
    """Кабинетов ещё нет вовсе — запускать нечем, а не тихо окружением (реальный баг)."""

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(NoAdAccountError):
            await _launch(session, ad_account_id=None, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_launch_without_selection_and_two_cabinets_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кабинетов несколько, оператор не выбрал — угадывать за него нельзя."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        await _add_cabinet(session)
        await _add_second_cabinet(session, monkeypatch)
        with pytest.raises(AmbiguousAdAccountError):
            await _launch(session, ad_account_id=None, settings=_settings(vk_live_campaigns=True))

    asyncio.run(_with_db(scenario))


def test_campaign_remembers_its_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        campaign = await session.get(Campaign, outcome.campaign_id)
        assert campaign is not None
        assert campaign.ad_account_id == cabinet_id

    asyncio.run(_with_db(scenario))


def test_cabinet_ref_comes_from_ad_account_not_from_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Внешний ref кабинета берётся у `AdAccount`, а не у поля брифа.

    `vk_ad_cabinet_id` в брифе — историческое поле для kotbot; запасного пути
    через него для запуска больше нет (`_resolve_cabinet` его не читает).
    """
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        await _launch(session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True))
        assert RecordingAdapter.last_cabinet_ref == "10000001"
        assert RecordingAdapter.last_cabinet_ref != BRIEF_PAYLOAD["vk_ad_cabinet_id"]

    asyncio.run(_with_db(scenario))


def test_unknown_cabinet_is_rejected() -> None:
    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(AccountNotFoundError):
            await _launch(session, ad_account_id=999, settings=_settings())

    asyncio.run(_with_db(scenario))


def test_deleted_cabinet_cannot_be_used() -> None:
    """У архивного кабинета токена нет — запускать нечем."""

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        await delete_account(session, 1, cabinet_id)
        with pytest.raises(TokenUnavailableError):
            await _launch(session, ad_account_id=cabinet_id, settings=_settings())

    asyncio.run(_with_db(scenario))


# --- цель ---------------------------------------------------------------------


def test_supported_goal_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session,
            ad_account_id=cabinet_id,
            settings=_settings(vk_live_campaigns=True),
            goal="subscribers",
        )
        assert outcome.campaign_id > 0

    asyncio.run(_with_db(scenario))


def test_unimplemented_goal_is_rejected() -> None:
    """Цель без логики запуска не должна молча превращаться в «подписчиков»."""

    async def scenario(session: AsyncSession) -> None:
        with pytest.raises(UnsupportedGoalError):
            await _launch(session, ad_account_id=None, settings=_settings(), goal="senler")

    asyncio.run(_with_db(scenario))


def test_lead_form_goal_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Лид-форма доведена до боевого запуска — цель больше не отклоняется."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session,
            ad_account_id=cabinet_id,
            settings=_settings(vk_live_campaigns=True),
            goal="lead_form",
        )
        assert outcome.campaign_id > 0

    asyncio.run(_with_db(scenario))


# --- предохранители -----------------------------------------------------------


def test_healthy_cabinet_alone_does_not_enable_live_campaigns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ГЛАВНЫЙ ИНВАРИАНТ: живой кабинет с валидным токеном ничего не разрешает.

    При снятом `VK_LIVE_CAMPAIGNS` в VK не должно уйти ни одного запроса, а
    кампания обязана остаться `prepared`.
    """

    def explode(token: SecretStr, **_: object) -> RecordingAdapter:
        raise AssertionError("VkApiAdapter must not be built while VK_LIVE_CAMPAIGNS is off")

    monkeypatch.setattr(launch_service, "VkApiAdapter", explode)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=False)
        )
        assert outcome.campaign_status == "prepared"

    asyncio.run(_with_db(scenario))


def test_autostart_still_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Второй предохранитель: кампания создана, но не запущена — деньги не тратятся."""
    launched: list[str] = []

    class NoAutostart(FakeVkAdapter):
        async def launch(self, campaign_id: str) -> None:
            launched.append(campaign_id)

    monkeypatch.setattr(launch_service, "VkApiAdapter", NoAutostart)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session,
            ad_account_id=cabinet_id,
            settings=_settings(vk_live_campaigns=True, vk_campaign_autostart=False),
        )
        assert launched == []
        assert outcome.campaign_status == "prepared"

    asyncio.run(_with_db(scenario))


# --- отозванный токен ---------------------------------------------------------


def test_revoked_token_marks_cabinet_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 из боевого вызова — самый честный сигнал, что токен умер."""

    class Rejecting(FakeVkAdapter):
        async def create_campaign_from_spec(self, cabinet_id: str, spec: Any, **kw: Any) -> str:
            response = httpx.Response(401, request=httpx.Request("POST", "https://ads.vk.com"))
            raise httpx.HTTPStatusError("unauthorized", request=response.request, response=response)

    monkeypatch.setattr(launch_service, "VkApiAdapter", Rejecting)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        row = await get_ad_account(session, 1, cabinet_id)
        assert row is not None
        assert row.health == "unauthorized"
        # Успех не имитируем: кампания подготовлена, но не запущена.
        assert outcome.campaign_status == "prepared"

    asyncio.run(_with_db(scenario))


def test_network_failure_does_not_mark_cabinet_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сеть моргнула — кабинет не виноват, метку `unauthorized` не ставим."""

    class Flaky(FakeVkAdapter):
        async def create_campaign_from_spec(self, cabinet_id: str, spec: Any, **kw: Any) -> str:
            raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(launch_service, "VkApiAdapter", Flaky)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        await _launch(session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True))
        row = await get_ad_account(session, 1, cabinet_id)
        assert row is not None
        assert row.health == "healthy"

    asyncio.run(_with_db(scenario))


# --- остановка ----------------------------------------------------------------


def test_stop_uses_token_of_the_campaigns_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остановить кампанию можно только тем доступом, которым она создана."""
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(
            session, ad_account_id=cabinet_id, settings=_settings(vk_live_campaigns=True)
        )
        await session.commit()
        RecordingAdapter.stopped_with = ""
        await stop_campaign(
            session, 1, outcome.campaign_id, settings=_settings(vk_live_campaigns=True)
        )
        assert RecordingAdapter.stopped_with == TOKEN

    asyncio.run(_with_db(scenario))


async def _legacy_live_campaign(session: AsyncSession) -> Campaign:
    """Кампания «до мультикабинетности»: кабинет VK в БД есть, привязки к
    `ad_account_id` — ещё нет (колонка появилась позже её создания)."""
    cabinet = Cabinet(
        account_id=1,
        client_id=100,
        channel="vk_api",
        ad_object_url="https://vk.com/id1",
        external_ref="10000001",
    )
    session.add(cabinet)
    await session.flush()
    campaign = Campaign(
        account_id=1,
        brief_id=500,
        client_id=100,
        cabinet_id=cabinet.id,
        ad_account_id=None,
        status="launched",
        objective="socialengagement",
        external_id="old-1",
    )
    session.add(campaign)
    await session.commit()
    return campaign


def test_stop_of_legacy_campaign_uses_the_only_active_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кампания без `ad_account_id` останавливается токеном кабинета по умолчанию
    (единственного активного) — токен из `.env` для этого больше не годится.
    """
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)

    async def scenario(session: AsyncSession) -> None:
        await _add_cabinet(session)
        campaign = await _legacy_live_campaign(session)
        RecordingAdapter.stopped_with = ""
        stopped = await stop_campaign(
            session, 1, campaign.id, settings=_settings(vk_live_campaigns=True)
        )
        assert stopped is not None
        assert stopped.status == "stopped"
        assert RecordingAdapter.stopped_with == TOKEN

    asyncio.run(_with_db(scenario))


def test_stop_of_legacy_campaign_without_a_default_cabinet_fails_honestly() -> None:
    """Ноль кабинетов — останавливать нечем; честная ошибка, а не тихий «успех»."""

    async def scenario(session: AsyncSession) -> None:
        campaign = await _legacy_live_campaign(session)
        with pytest.raises(CampaignStopError):
            await stop_campaign(session, 1, campaign.id, settings=_settings())

    asyncio.run(_with_db(scenario))
