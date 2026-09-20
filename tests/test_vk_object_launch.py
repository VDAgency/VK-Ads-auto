"""Резолв числового id объекта при запуске кампании (задача 8, spec §D).

Проверяют интеграцию `integrations/vk_object.py` в `services/launch_service.py`:
резолв меняет площадку (сообщество/личная страница) вопреки ошибочной подсказке
брифа, результат сохраняется в бриф и не запрашивается повторно, неудача не
роняет запуск, а площадка «рассылка» резолвер вообще не зовёт. Резолвер здесь —
инжектируемая функция-шпион (`_ResolverSpy`), без сети (spec §D: «инжектируемый
параметр»).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
import services.ad_accounts as ad_accounts
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Client
from db.repositories import get_brief, lock_brief_for_launch
from integrations.adapter import PlatformAdapter
from integrations.channels import Channel, ChannelConfig, ChannelRouter
from integrations.vk_api import resolve_ad_object
from integrations.vk_object import ResolvedVkObject
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.launch_service import launch_from_creative
from services.mapping import CampaignSpec
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

# Короткий адрес + подсказка «сообщество», хотя по факту это личная страница
# (та же пара адрес/id, что у фикстур `tests/fixtures/vk_pages/`).
_PAYLOAD_COMMUNITY_HINT = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.ru/fin_dolm",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "сообщество",
}

_PAYLOAD_NEWSLETTER = dict(_PAYLOAD_COMMUNITY_HINT, target_type="рассылка")


class _ResolverSpy:
    """Инжектируемый резолвер-шпион: без сети, считает вызовы (spec §D)."""

    def __init__(self, result: ResolvedVkObject | None) -> None:
        self._result = result
        self.calls: list[str] = []

    async def __call__(self, url: str) -> ResolvedVkObject | None:
        self.calls.append(url)
        return self._result


class _RecordingAdapter(PlatformAdapter):
    """Боевой канал в тестах: без сети, запоминает спеку, ушедшую в площадку."""

    def __init__(self) -> None:
        self.specs: list[CampaignSpec] = []

    async def health_check(self) -> bool:
        return True

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return f"live-cabinet-{client_ref}"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return "live-camp-1"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return f"live-content-{campaign_id}"

    async def launch(self, campaign_id: str) -> None:
        pass

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {}

    async def create_campaign_from_spec(
        self,
        cabinet_id: str,
        spec: CampaignSpec,
        **kwargs: object,
    ) -> str:
        self.specs.append(spec)
        return await super().create_campaign_from_spec(cabinet_id, spec, **kwargs)  # type: ignore[arg-type]


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
                payload=dict(payload or _PAYLOAD_COMMUNITY_HINT),
            )
        )
        await session.commit()
        await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


@pytest.fixture(autouse=True)
def _mock_vk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


def test_resolved_personal_page_overrides_community_hint_and_is_saved() -> None:
    adapter = _RecordingAdapter()
    resolver = _ResolverSpy(ResolvedVkObject(numeric_id=808632468, kind="personal"))

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
            resolve_object=resolver,
        )
        assert outcome.campaign_status == "prepared"
        assert resolver.calls == ["https://vk.ru/fin_dolm"]

        spec = adapter.specs[-1]
        assert spec.object_url == "https://vk.com/id808632468"
        # Числовая форма перевешивает подсказку «сообщество» — площадка личной
        # страницы, пакет 3268 (боевая проверка 2026-07-26).
        ad_object = resolve_ad_object(spec.object_url, spec.object_kind)
        assert ad_object.package_id == 3268

        brief = await get_brief(session, 1, 1)
        assert brief is not None
        assert brief.object_numeric_id == 808632468
        assert brief.object_resolved_kind == "personal"
        assert brief.object_resolved_at is not None

    asyncio.run(_with_db(scenario))


def test_second_launch_does_not_call_the_resolver_again() -> None:
    adapter = _RecordingAdapter()
    first_resolver = _ResolverSpy(ResolvedVkObject(numeric_id=808632468, kind="personal"))
    # Если бы резолвер позвали снова, он бы соврал «сообщество» — тест ловит
    # именно повторный вызов, а не случайное совпадение результата.
    second_resolver = _ResolverSpy(ResolvedVkObject(numeric_id=1, kind="community"))

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
            resolve_object=first_resolver,
        )
        await session.commit()

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
            resolve_object=second_resolver,
            allow_relaunch=True,
        )
        await session.commit()

        assert second_resolver.calls == []  # результат уже в брифе — сеть не нужна
        spec = adapter.specs[-1]
        # Взято из кэша (Brief), а не из second_resolver — его результат не должен уйти в спеку.
        assert spec.object_url == "https://vk.com/id808632468"

    asyncio.run(_with_db(scenario))


def test_resolution_failure_keeps_previous_behaviour() -> None:
    """Резолв вернул `None` (сеть/таймаут/нет маркера) — запуск не падает, идёт
    по прежней подсказке брифа («сообщество»), в бриф ничего не пишется."""
    adapter = _RecordingAdapter()
    resolver = _ResolverSpy(None)

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
            resolve_object=resolver,
        )
        assert outcome.campaign_status == "prepared"
        assert resolver.calls == ["https://vk.ru/fin_dolm"]

        spec = adapter.specs[-1]
        assert spec.object_url == "https://vk.ru/fin_dolm"  # адрес брифа как есть
        ad_object = resolve_ad_object(spec.object_url, spec.object_kind)
        assert ad_object.package_id == 3122  # прежнее поведение — сообщество

        brief = await get_brief(session, 1, 1)
        assert brief is not None
        assert brief.object_numeric_id is None
        assert brief.object_resolved_kind is None
        assert brief.object_resolved_at is None

    asyncio.run(_with_db(scenario))


def test_resolver_runs_before_the_brief_row_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Резолв объекта — сетевой поход (HTTP GET к vk.com, таймаут 5 с) — не должен
    идти под блокировкой строки брифа (`lock_brief_for_launch`, `FOR UPDATE`):
    иначе конкурентный запуск по тому же брифу ждёт всё время сетевого похода
    впустую (найдено ревью: резолв стоял ПОСЛЕ блокировки). Шпион на резолвере и
    шпион на `lock_brief_for_launch` фиксируют порядок вызовов напрямую."""
    adapter = _RecordingAdapter()
    resolver = _ResolverSpy(ResolvedVkObject(numeric_id=808632468, kind="personal"))
    calls: list[str] = []

    async def spying_resolver(url: str) -> ResolvedVkObject | None:
        calls.append("resolve_object")
        return await resolver(url)

    async def spying_lock(session: AsyncSession, account_id: int, brief_id: int) -> Brief | None:
        calls.append("lock_brief_for_launch")
        return await lock_brief_for_launch(session, account_id, brief_id)

    # Патчим строкой (не через объект модуля): `lock_brief_for_launch` попадает в
    # `services.launch_service` обычным импортом, mypy `--no-implicit-reexport`
    # не считает его переэкспортированным атрибутом модуля.
    monkeypatch.setattr("services.launch_service.lock_brief_for_launch", spying_lock)

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
            resolve_object=spying_resolver,
        )
        assert outcome.campaign_status == "prepared"

    asyncio.run(_with_db(scenario))
    assert calls == ["resolve_object", "lock_brief_for_launch"]


def test_newsletter_surface_does_not_call_the_resolver() -> None:
    """Рассылка не входит в пару «сообщество/личная страница» — резолвер лишний."""
    adapter = _RecordingAdapter()
    resolver = _ResolverSpy(ResolvedVkObject(numeric_id=1, kind="personal"))

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
            resolve_object=resolver,
        )
        assert resolver.calls == []

    asyncio.run(_with_db(scenario, payload=_PAYLOAD_NEWSLETTER))
