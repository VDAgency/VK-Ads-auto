"""Тесты синхронизации статистики кампаний (`services/stats_sync`, spec §9).

Покрывают: сохранение среза `Stat` по активным кампаниям, обновление статуса по
ответу площадки (moderation→launched, →stopped), выбор адаптера по каналу
кабинета, изоляцию ошибок (одна кампания не роняет остальные), скоуп тенанта и
(spec 2026-07-27 §9) — токен кабинета КОНКРЕТНОЙ кампании вместо окружения,
кэш адаптеров с учётом кабинета, честный пропуск кампании без кабинета.
Живой VK API не дёргается — адаптеры подменяются в тестах.
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
from db.models import Account, Brief, Cabinet, Campaign, Client, Stat
from integrations.adapter import PlatformAdapter
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.stats_sync import cabinet_sync_outcome, sync_cabinet_stats, sync_campaign_stats
from services.vk_identity import VkIdentity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
TOKEN_2 = "fake-access-token-for-tests-0000000000000002"
_KEY = Fernet.generate_key().decode()

IDENTITY = VkIdentity("10000001", "a1b2c3d4e5@agency_client", "Студия «Пример»", "active")
IDENTITY_2 = VkIdentity("10000002", "f6g7h8i9j0@agency_client", "Кабинет «Второй»", "active")


def _live_settings(**over: Any) -> Settings:
    """Настройки с ключом шифрования и разрешённым боевым каналом (для кабинетов)."""
    base: dict[str, Any] = {
        "_env_file": None,
        "vk_ads_secret_key": SecretStr(_KEY),
        "vk_live_campaigns": True,
    }
    base.update(over)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _mock_vk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK по умолчанию отвечает первой личностью; тест с двумя кабинетами переключает."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


class _RecordingVkAdapter(PlatformAdapter):
    """Подмена `VkApiAdapter`: запоминает токены, которыми её собрали."""

    tokens_seen: list[str] = []

    def __init__(self, access_token: SecretStr, **_: object) -> None:
        self.token = access_token.get_secret_value()
        _RecordingVkAdapter.tokens_seen.append(self.token)

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return "camp"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return "creative"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {"shows": 1.0}

    async def get_status(self, campaign_id: str) -> str:
        return "active"


class _FakeAdapter(PlatformAdapter):
    """Площадка в тестах: отдаёт заданные метрики/статус, копит вызовы."""

    def __init__(
        self,
        *,
        status: str = "active",
        stats: dict[str, float] | None = None,
        broken: bool = False,
    ) -> None:
        self._status = status
        self._stats = stats if stats is not None else {"shows": 100.0, "clicks": 5.0}
        self._broken = broken
        self.stats_calls: list[str] = []
        self.status_calls: list[str] = []

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return "camp"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return "creative"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        self.stats_calls.append(campaign_id)
        if self._broken:
            raise RuntimeError("platform is down")
        return dict(self._stats)

    async def get_status(self, campaign_id: str) -> str:
        self.status_calls.append(campaign_id)
        return self._status


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _with_db(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Пустая БД с тенантом, клиентом, брифом и двумя кабинетами разных каналов."""
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
        session.add(Account(id=2, name="other"))
        session.add(Client(id=1, account_id=1, full_name="Вячеслав"))
        session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
        session.add(
            Cabinet(
                id=1,
                account_id=1,
                client_id=1,
                channel="vk_api",
                ad_object_url="https://vk.com/id1",
            )
        )
        session.add(
            Cabinet(
                id=2,
                account_id=1,
                client_id=1,
                channel="kotbot",
                ad_object_url="https://vk.com/club1",
            )
        )
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def _campaign(
    campaign_id: int,
    *,
    account_id: int = 1,
    status: str = "launched",
    external_id: str | None = "ext-1",
    cabinet_id: int | None = 1,
    ad_account_id: int | None = None,
) -> Campaign:
    return Campaign(
        id=campaign_id,
        account_id=account_id,
        brief_id=1,
        cabinet_id=cabinet_id,
        ad_account_id=ad_account_id,
        status=status,
        objective="socialengagement",
        external_id=external_id,
    )


# --- сохранение среза метрик ------------------------------------------------------


def test_sync_saves_stat_row_for_active_campaign() -> None:
    adapter = _FakeAdapter(stats={"shows": 100.0, "clicks": 5.0, "spent": 250.0, "goals": 10.0})

    async def scenario(session: AsyncSession) -> tuple[dict[int, str], list[Stat]]:
        session.add(_campaign(1))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )
        await session.commit()
        stats = list((await session.execute(select(Stat))).scalars().all())
        return summary, stats

    summary, stats = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok"}
    assert len(stats) == 1
    assert stats[0].campaign_id == "ext-1"
    assert stats[0].shows == 100.0
    assert stats[0].results == 10.0
    assert adapter.stats_calls == ["ext-1"]


def test_empty_platform_stats_are_stored_as_zeroes() -> None:
    # Площадка ещё не отдала метрики — срез нулевой, но синк не считается ошибкой.
    adapter = _FakeAdapter(stats={})

    async def scenario(session: AsyncSession) -> tuple[dict[int, str], float]:
        session.add(_campaign(1))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )
        await session.commit()
        stat = (await session.execute(select(Stat))).scalars().one()
        return summary, stat.shows

    summary, shows = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok"}
    assert shows == 0.0


# --- обновление статуса по площадке -----------------------------------------------


def test_moderation_becomes_launched_when_platform_is_active() -> None:
    adapter = _FakeAdapter(status="active")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1, status="moderation"))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "launched"


def test_blocked_on_platform_becomes_stopped() -> None:
    adapter = _FakeAdapter(status="blocked")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "stopped"


def test_launched_becomes_moderation_when_platform_moderates() -> None:
    adapter = _FakeAdapter(status="moderation")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "moderation"


def test_unknown_platform_status_keeps_current_status() -> None:
    # Канал статус не сообщает (`unknown` по умолчанию контракта адаптера) — не трогаем.
    adapter = _FakeAdapter(status="unknown")

    async def scenario(session: AsyncSession) -> str:
        session.add(_campaign(1, status="moderation"))
        await session.commit()
        await sync_campaign_stats(session, 1, settings=_settings(), adapters={"vk_api": adapter})
        await session.commit()
        campaign = await session.get(Campaign, 1)
        assert campaign is not None
        return campaign.status

    assert asyncio.run(_with_db(scenario)) == "moderation"


# --- выбор канала и скоуп ----------------------------------------------------------


def test_adapter_is_chosen_by_cabinet_channel() -> None:
    vk = _FakeAdapter()
    kotbot = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        session.add(_campaign(2, external_id="kot-1", cabinet_id=2))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": vk, "kotbot": kotbot}
        )
        await session.commit()
        return summary

    summary = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok", 2: "ok"}
    assert vk.stats_calls == ["vk-1"]
    assert kotbot.stats_calls == ["kot-1"]


def test_prepared_campaign_is_not_synced() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, status="prepared"))
        await session.commit()
        return await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )

    assert asyncio.run(_with_db(scenario)) == {}
    assert adapter.stats_calls == []


def test_other_tenant_campaigns_are_not_synced() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        # Кампания чужого тенанта: кабинет не указываем — FK кабинета за тенантом.
        session.add(_campaign(1, account_id=2, cabinet_id=None))
        await session.commit()
        return await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": adapter}
        )

    assert asyncio.run(_with_db(scenario)) == {}
    assert adapter.stats_calls == []


# --- изоляция ошибок ---------------------------------------------------------------


def test_error_on_one_campaign_does_not_break_the_rest() -> None:
    broken = _FakeAdapter(broken=True)
    healthy = _FakeAdapter()

    async def scenario(session: AsyncSession) -> tuple[dict[int, str], int]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        session.add(_campaign(2, external_id="kot-1", cabinet_id=2))
        await session.commit()
        summary = await sync_campaign_stats(
            session, 1, settings=_settings(), adapters={"vk_api": broken, "kotbot": healthy}
        )
        await session.commit()
        stats = list((await session.execute(select(Stat))).scalars().all())
        return summary, len(stats)

    summary, saved = asyncio.run(_with_db(scenario))
    assert summary == {1: "error", 2: "ok"}
    assert saved == 1


# --- токен берётся у кабинета кампании, не из окружения (spec 2026-07-27 §9) -------


def test_sync_uses_campaigns_own_cabinet_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Синк идёт токеном кабинета КОНКРЕТНОЙ кампании — не окружением, не первым попавшимся."""
    _RecordingVkAdapter.tokens_seen = []
    monkeypatch.setattr(launch_service, "VkApiAdapter", _RecordingVkAdapter)

    async def scenario(session: AsyncSession) -> dict[int, str]:
        cfg = _live_settings()
        ad_account = await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1, ad_account_id=ad_account.id))
        await session.commit()
        return await sync_campaign_stats(session, 1, settings=cfg)

    summary = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok"}
    assert _RecordingVkAdapter.tokens_seen == [TOKEN]


def test_sync_falls_back_to_default_cabinet_for_legacy_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кампания без своего `ad_account_id` (легаси) синкается кабинетом по умолчанию."""
    _RecordingVkAdapter.tokens_seen = []
    monkeypatch.setattr(launch_service, "VkApiAdapter", _RecordingVkAdapter)

    async def scenario(session: AsyncSession) -> dict[int, str]:
        cfg = _live_settings()
        await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1, ad_account_id=None))
        await session.commit()
        return await sync_campaign_stats(session, 1, settings=cfg)

    summary = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok"}
    assert _RecordingVkAdapter.tokens_seen == [TOKEN]


def test_sync_does_not_mix_tokens_of_different_cabinets_on_same_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кэш адаптеров учитывает кабинет: одинаковый канал, разные кабинеты — разные токены.

    Общий кэш по имени канала подсунул бы кампании чужой доступ (реальный баг,
    который чинит эта задача) — ключ кэша обязан включать `ad_account_id`.
    """
    _RecordingVkAdapter.tokens_seen = []
    monkeypatch.setattr(launch_service, "VkApiAdapter", _RecordingVkAdapter)

    async def scenario(session: AsyncSession) -> dict[int, str]:
        cfg = _live_settings()
        acc1 = await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()

        async def identity_2(token: str, **_: object) -> VkIdentity:
            return IDENTITY_2

        monkeypatch.setattr(ad_accounts, "fetch_identity", identity_2)
        acc2 = await add_account(session, 1, TOKEN_2, settings=cfg)
        await session.commit()

        # Оба на кабинете id=1 (канал vk_api), но с разными ad_account_id.
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1, ad_account_id=acc1.id))
        session.add(_campaign(2, external_id="vk-2", cabinet_id=1, ad_account_id=acc2.id))
        await session.commit()
        return await sync_campaign_stats(session, 1, settings=cfg)

    summary = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok", 2: "ok"}
    assert sorted(_RecordingVkAdapter.tokens_seen) == sorted([TOKEN, TOKEN_2])


def test_sync_reuses_adapter_for_same_channel_and_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Две кампании одного кабинета — адаптер собирается один раз, не на каждую."""
    _RecordingVkAdapter.tokens_seen = []
    monkeypatch.setattr(launch_service, "VkApiAdapter", _RecordingVkAdapter)

    async def scenario(session: AsyncSession) -> dict[int, str]:
        cfg = _live_settings()
        ad_account = await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1, ad_account_id=ad_account.id))
        session.add(_campaign(2, external_id="vk-2", cabinet_id=1, ad_account_id=ad_account.id))
        await session.commit()
        return await sync_campaign_stats(session, 1, settings=cfg)

    summary = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok", 2: "ok"}
    # Адаптер собран РОВНО один раз: второй вызов достался из кэша.
    assert _RecordingVkAdapter.tokens_seen == [TOKEN]


# --- нет кабинета — честный пропуск, не выдуманный успех ---------------------------


def test_sync_skips_vk_campaign_without_a_resolvable_ad_account() -> None:
    """VK-кампания без своего кабинета и без кабинета по умолчанию — честный пропуск."""

    async def scenario(session: AsyncSession) -> dict[int, str]:
        # Кабинет №1 в фикстуре — канала `vk_api`, то есть токен реально нужен.
        session.add(_campaign(1, cabinet_id=1, ad_account_id=None))
        await session.commit()
        return await sync_campaign_stats(session, 1, settings=_live_settings())

    assert asyncio.run(_with_db(scenario)) == {1: "skipped"}


def test_sync_of_stub_campaign_needs_no_ad_account() -> None:
    """Кампании на заглушке токен VK не нужен: кабинетов нет, а синк проходит.

    Иначе оператор не смог бы синхронизировать подготовленные кампании, пока не
    заведёт рекламный кабинет, — хотя площадку при этом никто не трогает.
    """

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, cabinet_id=None, ad_account_id=None))
        await session.commit()
        return await sync_campaign_stats(session, 1, settings=_live_settings())

    assert asyncio.run(_with_db(scenario)) == {1: "ok"}


def test_sync_skips_campaign_when_default_cabinet_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кабинетов несколько, у кампании своего нет — угадывать нельзя, честный пропуск."""

    async def scenario(session: AsyncSession) -> dict[int, str]:
        cfg = _live_settings()
        await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()

        async def identity_2(token: str, **_: object) -> VkIdentity:
            return IDENTITY_2

        monkeypatch.setattr(ad_accounts, "fetch_identity", identity_2)
        await add_account(session, 1, TOKEN_2, settings=cfg)
        await session.commit()

        session.add(_campaign(1, cabinet_id=1, ad_account_id=None))
        await session.commit()
        return await sync_campaign_stats(session, 1, settings=cfg)

    assert asyncio.run(_with_db(scenario)) == {1: "skipped"}


# --- дефект 1: синк ОДНОГО кабинета (вход в кабинет в боте), не всех активных ------


def test_sync_cabinet_stats_only_syncs_the_matching_campaign() -> None:
    """Два кабинета (две кампании) в тенанте — синк одного не должен трогать другой."""
    vk = _FakeAdapter(stats={"shows": 10.0})
    kotbot = _FakeAdapter(stats={"shows": 20.0})

    async def scenario(session: AsyncSession) -> tuple[dict[int, str], list[str]]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        session.add(_campaign(2, external_id="kot-1", cabinet_id=2))
        await session.commit()
        summary = await sync_cabinet_stats(
            session, 1, "vk-1", settings=_settings(), adapters={"vk_api": vk, "kotbot": kotbot}
        )
        await session.commit()
        return summary, vk.stats_calls

    summary, vk_calls = asyncio.run(_with_db(scenario))
    assert summary == {1: "ok"}
    assert vk_calls == ["vk-1"]
    assert kotbot.stats_calls == []  # другой кабинет синк не тронул


def test_sync_cabinet_stats_returns_empty_for_unknown_cabinet() -> None:
    """Кабинета с таким внешним id среди активных кампаний нет — честный пустой синк."""
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        await session.commit()
        return await sync_cabinet_stats(
            session, 1, "does-not-exist", settings=_settings(), adapters={"vk_api": adapter}
        )

    assert asyncio.run(_with_db(scenario)) == {}
    assert adapter.stats_calls == []


def test_sync_cabinet_stats_saves_stat_row() -> None:
    """Синк по кабинету реально сохраняет срез, а не только считает summary."""
    adapter = _FakeAdapter(stats={"shows": 100.0, "clicks": 5.0, "spent": 250.0, "goals": 10.0})

    async def scenario(session: AsyncSession) -> list[Stat]:
        session.add(_campaign(1, external_id="ext-1", cabinet_id=1))
        await session.commit()
        await sync_cabinet_stats(
            session, 1, "ext-1", settings=_settings(), adapters={"vk_api": adapter}
        )
        await session.commit()
        return list((await session.execute(select(Stat))).scalars().all())

    stats = asyncio.run(_with_db(scenario))
    assert len(stats) == 1
    assert stats[0].campaign_id == "ext-1"
    assert stats[0].shows == 100.0


def test_sync_cabinet_stats_error_is_reported_not_raised() -> None:
    """Сбой площадки по кампании кабинета — честная сводка `error`, не исключение наружу."""
    broken = _FakeAdapter(broken=True)

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        await session.commit()
        return await sync_cabinet_stats(
            session, 1, "vk-1", settings=_settings(), adapters={"vk_api": broken}
        )

    assert asyncio.run(_with_db(scenario)) == {1: "error"}


# --- A2/A3: три исхода синка кабинета, не два ---------------------------------------
#
# A2 научил роутер не путать пустую сводку с успехом. Но эта же правка перелечила:
# кабинет с кампанией в `prepared`/`stopped` тоже даёт пустую сводку (в неё не
# запущено ничего активного — `list_active_campaigns` её и не отбирает), и это
# НОРМА, а не сбой площадки. Бот не должен пугать оператора «не удалось обновить»
# под каждым неподнятым кабинетом. Нужны три исхода:
# `updated` (что-то реально совпало и синкнулось без ошибок), `nothing_to_update`
# (под кабинетом нет активных кампаний — синкать нечего, это ожидаемо),
# `failed` (были настоящие ошибки площадки/адаптера — вот тут пометка обязана
# остаться).


def test_cabinet_sync_outcome_is_nothing_to_update_when_empty() -> None:
    """Пустая сводка (кампаний под этим кабинетом нет вовсе, либо ни одна не активна)
    — это «нечего обновлять», а не успех и не сбой.
    """
    assert cabinet_sync_outcome({}) == "nothing_to_update"


def test_cabinet_sync_outcome_is_updated_when_matched_campaign_synced() -> None:
    assert cabinet_sync_outcome({1: "ok"}) == "updated"


def test_cabinet_sync_outcome_is_failed_on_any_error_or_skip() -> None:
    assert cabinet_sync_outcome({1: "error"}) == "failed"
    assert cabinet_sync_outcome({1: "skipped"}) == "failed"
    assert cabinet_sync_outcome({1: "ok", 2: "error"}) == "failed"


def test_sync_cabinet_stats_for_unmatched_cabinet_is_nothing_to_update_not_failed() -> None:
    """Синк кабинета, для которого нет активных кампаний, — это «нечего обновлять»
    (не тривиальный успех из старого дефекта, но и не ложный сигнал сбоя площадки).
    """
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1))
        await session.commit()
        return await sync_cabinet_stats(
            session, 1, "does-not-exist", settings=_settings(), adapters={"vk_api": adapter}
        )

    results = asyncio.run(_with_db(scenario))
    assert cabinet_sync_outcome(results) == "nothing_to_update"


def test_sync_cabinet_stats_for_unlaunched_campaign_is_nothing_to_update() -> None:
    """Кампания есть, но она `prepared`/`stopped` — ей нечего синкать, это не сбой
    (сценарий, из-за которого A2 стало перелечивать: боевой кабинет с двумя
    кампаниями, `prepared` и `stopped`, не должен показывать предупреждение).
    """
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> dict[int, str]:
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1, status="prepared"))
        await session.commit()
        return await sync_cabinet_stats(
            session, 1, "vk-1", settings=_settings(), adapters={"vk_api": adapter}
        )

    results = asyncio.run(_with_db(scenario))
    assert results == {}
    assert cabinet_sync_outcome(results) == "nothing_to_update"
    assert adapter.stats_calls == []
