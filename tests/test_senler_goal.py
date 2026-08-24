"""Цель «Заявка через Senler»: технически совпадает с уже проверенной «Сообщения».

Решение 2026-08-24: боевая кампания 28694299, прочитанная напрямую из VK, подтвердила
`{"objective": "socialengagement", "package_id": 3127}` — тот самый пакет, что уже
несёт `integrations.vk_surfaces.VK_MESSAGES` (боевой зонд 2026-08-23). Объект
рекламирования один и тот же — сообщество; Senler отличается от «Сообщений» только
смыслом для клиента: своё название, своя подсказка, свой префикс имени кампании.
Набор шаблонов объявления переиспользуется буквально (`_MESSAGES_PATTERNS`), а не
копируется — два экземпляра словаря расходятся при первой же правке.

`VK_SENLER.verified` стал `True` в тот же день: отдельный боевой прогон именно под
именем Senler руководитель провёл 2026-08-24 на сообществе DJ BEAUTY (228817082) —
токен привязан (`connected: true`), бриф с площадкой Senler принят, кампания создана
и прочитана напрямую из VK с верным пакетом/целью/префиксом имени, тестовые данные
удалены. Площадка теперь показывается клиенту как доступная — тем же путём, каким
уже раньше открылись «Сообщения». Единственный оставшийся непроверенный элемент
`services.goals.subscription_targets()` — `VK_CLIP` (ждёт настоящей ссылки на клип,
доступный кабинету).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from bot.handlers.creative import GOALS
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Campaign, Client
from integrations.vk_api import campaign_objective
from integrations.vk_surfaces import GOAL_SENLER, SURFACES, surface_for
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.brief_parser import Goal, TargetType, parse_target_type
from services.goals import goal_for_target_type, goal_titles, subscription_targets
from services.launch_service import SUPPORTED_GOALS, launch_from_creative
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()

IDENTITY = VkIdentity("10000001", "a1b2c3d4e5@agency_client", "Студия «Пример»", "active")


# --- справочник площадок ------------------------------------------------------


def test_senler_surface_reuses_the_verified_messages_package() -> None:
    """Senler запускается тем же пакетом, что и «Сообщения»: объект рекламы один."""
    senler = surface_for("senler")
    messages = surface_for("messages")
    assert senler.package_id == 3127
    assert senler.objective == "socialengagement"
    assert senler.default_cta == messages.default_cta
    assert senler.patterns == messages.patterns
    assert senler.goal == GOAL_SENLER


def test_senler_surface_is_registered_in_surfaces_tuple() -> None:
    assert "senler" in {surface.kind for surface in SURFACES}


def test_senler_surface_is_verified_after_the_dedicated_probe() -> None:
    """Флаг честный: боевой прогон конкретно под именем Senler проведён 2026-08-24."""
    assert surface_for("senler").verified is True


def test_senler_is_no_longer_unverified_only_the_clip_remains() -> None:
    """После боевого прогона Senler единственный непроверенный элемент каталога —
    `vk_clip` (он же был единственным до появления цели Senler): тот же паттерн
    «показываем, но выбрать не даём» продолжает работать хотя бы на одной площадке."""
    unverified = sorted(target.kind for target in subscription_targets() if not target.available)
    assert unverified == ["vk_clip"]


# --- разбор брифа --------------------------------------------------------------


def test_brief_wording_resolves_to_the_senler_target_type() -> None:
    assert parse_target_type("заявка через senler") is TargetType.SENLER
    assert parse_target_type("сенлер") is TargetType.SENLER


def test_senler_keyword_does_not_fall_into_lead_form() -> None:
    """«Заявка через Senler» содержит «заявк» — без верного порядка проверок в
    `parse_target_type` фраза утекла бы в TargetType.LEAD_FORM раньше, чем код
    доберётся до ключевого слова «senler»."""
    assert parse_target_type("заявка через senler") is not TargetType.LEAD_FORM


def test_goal_for_senler_target_type_is_senler() -> None:
    assert goal_for_target_type(TargetType.SENLER) is Goal.SENLER


def test_senler_goal_has_a_title() -> None:
    assert goal_titles()[GOAL_SENLER] == "Заявка через Senler"


# --- интерфейсы -----------------------------------------------------------------


def test_senler_goal_is_unlocked_in_the_bot_goal_keyboard() -> None:
    """Оператор может явно выбрать цель Senler при запуске — кнопка больше не «серая»."""
    entry = next(item for item in GOALS if item[0] == "senler")
    _code, _label, enabled = entry
    assert enabled is True


def test_supported_goals_contains_senler() -> None:
    assert "senler" in SUPPORTED_GOALS


# --- сквозной путь: бриф → раскладка → адаптер ----------------------------------

# Клиент выбрал площадку «заявка через Senler» и прислал ссылку на сообщество —
# тот же формат, что и у «Сообщений» (VK_SENLER.hint).
SENLER_PAYLOAD = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/club228817082",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "заявка через senler",
}

# Тот же бриф, но с площадкой подписки — регресс: цель и пакет должны остаться прежними.
SUBSCRIBERS_PAYLOAD = {
    **SENLER_PAYLOAD,
    "target_type": "личная страница",
    "object_url": "https://vk.com/id1",
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


def test_senler_brief_reaches_the_adapter_with_package_3127_and_socialengagement() -> None:
    """Бриф с площадкой «заявка через Senler» обязан доехать до адаптера с пакетом
    3127 и objective `socialengagement` — тем же, что и «Сообщения», не с пакетом
    сообщества (3122)."""

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        outcome = await _launch(session, cabinet_id, goal="senler")

        assert outcome.campaign_id > 0
        spec = RecordingAdapter.last_spec
        assert spec is not None
        assert spec.object_kind == "senler"
        assert campaign_objective(spec) == "socialengagement"
        # Свой префикс имени кампании — требование плана (отличие от «Сообщений»
        # только смысловое, но название обязано быть понятным клиенту).
        assert spec.name.startswith("Senler")

        campaign = await session.get(Campaign, outcome.campaign_id)
        assert campaign is not None
        assert campaign.status == "launched"

    asyncio.run(_with_db(SENLER_PAYLOAD, scenario))


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


def test_senler_operator_goal_is_no_longer_rejected() -> None:
    """Раньше `goal="senler"` в запуске отклонялся `UnsupportedGoalError` — теперь принят."""

    async def scenario(session: AsyncSession) -> None:
        cabinet_id = await _add_cabinet(session)
        # Не должно бросить UnsupportedGoalError.
        outcome = await _launch(session, cabinet_id, goal="senler")
        assert outcome.campaign_id > 0

    asyncio.run(_with_db(SENLER_PAYLOAD, scenario))
