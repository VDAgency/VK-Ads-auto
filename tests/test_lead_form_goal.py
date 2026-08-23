"""Сквозной путь для цели «Заявки — лид-форма» (закрытие разрыва goal/target_type).

Механика лид-формы в адаптере уже боевая (площадка `lead_form`, `objective=leadads`,
пять текстовых слотов) — проверено `tests/test_subscription_surfaces.py`. Здесь
закрепляем, что весь путь ОТ БРИФА (клиент выбрал площадку «лид-форма» и прислал
ссылку вида `leadads://<id>/`) ЧЕРЕЗ `launch_from_creative` реально доезжает до
адаптера с этим objective, а не молча превращается в кампанию на подписчиков.
Плюс регресс: бриф на подписчиков продолжает работать как раньше.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Campaign, Client
from integrations.vk_api import campaign_objective
from integrations.vk_surfaces import surface_for
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.launch_service import launch_from_creative
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()

IDENTITY = VkIdentity("10000001", "a1b2c3d4e5@agency_client", "Студия «Пример»", "active")

# Клиент выбрал площадку «лид-форма» и прислал готовую ссылку на форму из
# кабинета VK (реальный формат, docs/VK_API_REFERENCE.md, `integrations/vk_surfaces.LEAD_FORMS`).
LEAD_FORM_PAYLOAD = {
    "full_name": "Вячеслав",
    "object_url": "leadads://857898/",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "📋 Лид-форма ВКонтакте",
}

# Тот же бриф, но с площадкой подписки — регресс: цель должна остаться прежней.
SUBSCRIBERS_PAYLOAD = {
    **LEAD_FORM_PAYLOAD,
    "object_url": "https://vk.com/id1",
    "target_type": "личная страница",
}


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "vk_ads_secret_key": SecretStr(_KEY),
        "vk_ads_access_token": SecretStr("env-token"),
        "creatives_dir": "",
        "vk_live_campaigns": True,
        "vk_campaign_autostart": True,
    }
    base.update(over)
    return Settings(**base)


class RecordingAdapter:
    """Заглушка VK-канала: запоминает спеку, с которой её позвали, — то, что
    реально уйдёт в `campaign_objective()` и, тем самым, в тело запроса VK."""

    last_spec: Any = None

    def __init__(self, token: str) -> None:
        self.token = token

    async def health_check(self) -> bool:
        return True

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "created-cabinet"

    async def create_campaign_from_spec(self, cabinet_id: str, spec: Any, **kwargs: Any) -> str:
        RecordingAdapter.last_spec = spec
        return "vk-campaign-1"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def get_status(self, campaign_id: str) -> str:
        return "active"

    async def stop(self, campaign_id: str) -> None:
        return None


class FakeVkAdapter(RecordingAdapter):
    """Подмена `VkApiAdapter`. Класс, а не функция: `_resolve_cabinet` смотрит
    на тип через `isinstance`, лямбда/фабрика эту ветку сломает."""

    def __init__(self, access_token: SecretStr, **_: object) -> None:
        super().__init__(access_token.get_secret_value())


@pytest.fixture(autouse=True)
def _mock_vk_and_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)
    monkeypatch.setattr(ad_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)
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
        session.add(Client(id=100, account_id=1, full_name="Вячеслав", email="v@example.com"))
        session.add(
            Brief(
                id=500,
                account_id=1,
                client_id=100,
                variant="individual",
                status="received",
                payload=dict(payload),
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


async def _launch(session: AsyncSession, ad_account_id: int, goal: str) -> Any:
    return await launch_from_creative(
        session,
        1,
        500,
        "photo",
        "creative.jpg",
        "Заголовок",
        "Текст",
        settings=_settings(),
        ad_account_id=ad_account_id,
        goal=goal,
    )


def test_lead_form_brief_reaches_the_adapter_with_leadads_objective() -> None:
    """Бриф с площадкой «лид-форма» обязан доехать до адаптера с VK objective `leadads`."""

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(session, cabinet_id, goal="lead_form")

        assert outcome.campaign_id > 0
        spec = RecordingAdapter.last_spec
        assert spec is not None
        # Площадка в спеке — «лид-форма», а не молчаливая подмена на подписку.
        assert spec.object_kind == "lead_form"
        # Это ровно та функция, которой живой `VkApiAdapter` считает objective
        # для тела запроса VK (integrations/vk_api.campaign_objective) — код
        # адаптера не менялся, но раньше до него просто не доезжал верный object_kind.
        assert campaign_objective(spec) == "leadads"

        campaign = await session.get(Campaign, outcome.campaign_id)
        assert campaign is not None
        assert campaign.status == "launched"

    asyncio.run(_with_db(LEAD_FORM_PAYLOAD, scenario))


def test_subscribers_brief_still_works_as_before() -> None:
    """Регресс: бриф на подписчиков продолжает разбираться и запускаться как раньше."""

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(session, cabinet_id, goal="subscribers")

        assert outcome.campaign_id > 0
        spec = RecordingAdapter.last_spec
        assert spec is not None
        assert spec.object_kind == "personal_page"
        assert campaign_objective(spec) == surface_for("personal_page").objective

        campaign = await session.get(Campaign, outcome.campaign_id)
        assert campaign is not None
        assert campaign.status == "launched"

    asyncio.run(_with_db(SUBSCRIBERS_PAYLOAD, scenario))


def test_lead_form_operator_goal_is_no_longer_rejected() -> None:
    """Раньше `goal="lead_form"` в запуске отклонялся `UnsupportedGoalError` — теперь принят."""

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        # Не должно бросить UnsupportedGoalError.
        outcome = await _launch(session, cabinet_id, goal="lead_form")
        assert outcome.campaign_id > 0

    asyncio.run(_with_db(LEAD_FORM_PAYLOAD, scenario))
