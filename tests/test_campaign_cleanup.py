"""Тесты очистки тестовых кампаний (`services/campaign_cleanup`) — разовая операция.

Живой VK API и боевая БД не участвуют: адаптер подменяется мокoм, БД — SQLite
in-memory. Покрывает: сухой прогон ничего не меняет, реальный прогон удаляет
только подходящее под критерий, исключённые id не трогаются, `stub-` кампания в
адаптер не уходит, ошибка одной кампании не мешает остальным, критерий
регистронезависимый.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from db.base import Base
from db.models import Account, Brief, Campaign, Client
from integrations.adapter import PlatformAdapter
from services.campaign_cleanup import (
    STUB_EXTERNAL_ID_PREFIX,
    CleanupSummary,
    cleanup_test_campaigns,
    find_test_campaigns,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


class _FakeAdapter(PlatformAdapter):
    """Мок адаптера: запоминает вызовы `delete_campaign`, умеет падать по id."""

    def __init__(self, *, fail_for: frozenset[str] = frozenset()) -> None:
        self.deleted: list[str] = []
        self._fail_for = fail_for

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        raise NotImplementedError

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        raise NotImplementedError

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        raise NotImplementedError

    async def launch(self, campaign_id: str) -> None:
        raise NotImplementedError

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True

    async def delete_campaign(self, campaign_id: str) -> None:
        if campaign_id in self._fail_for:
            raise RuntimeError(f"vk refused to delete {campaign_id}")
        self.deleted.append(campaign_id)


async def _with_db(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Пустая БД с тенантом, клиентом и брифом — кампании заводит сам тест."""
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
        session.add(Brief(id=1, account_id=1, client_id=1, variant="community", payload={}))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def _campaign(
    campaign_id: int,
    *,
    account_id: int = 1,
    status: str = "prepared",
    external_id: str | None,
    name: str | None,
) -> Campaign:
    spec_json = {"name": name} if name is not None else {}
    return Campaign(
        id=campaign_id,
        account_id=account_id,
        brief_id=1,
        status=status,
        objective="socialengagement",
        external_id=external_id,
        spec_json=spec_json,
    )


async def _count_campaigns(session: AsyncSession) -> int:
    rows = (await session.execute(select(Campaign))).scalars().all()
    return len(rows)


# --- find_test_campaigns: критерий и скоуп -----------------------------------------


def test_find_matches_default_criterion_case_insensitively() -> None:
    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, external_id="100", name="Подписчики · ТЕСТ Иванов"))
        session.add(_campaign(2, external_id="200", name="Подписчики · тестовый клиент"))
        session.add(_campaign(3, external_id="300", name="Подписчики · Настоящий Клиент"))
        await session.commit()
        candidates = await find_test_campaigns(session, 1)
        return sorted(c.campaign_id for c in candidates)

    assert asyncio.run(_with_db(scenario)) == [1, 2]


def test_find_scopes_by_tenant() -> None:
    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, account_id=1, external_id="100", name="ТЕСТ у нас"))
        session.add(_campaign(2, account_id=2, external_id="200", name="ТЕСТ у чужих"))
        await session.commit()
        candidates = await find_test_campaigns(session, 1)
        return [c.campaign_id for c in candidates]

    assert asyncio.run(_with_db(scenario)) == [1]


def test_find_respects_custom_criterion() -> None:
    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, external_id="100", name="Подписчики · ТЕСТ Иванов"))
        session.add(_campaign(2, external_id="200", name="Подписчики · Демо Петров"))
        await session.commit()
        candidates = await find_test_campaigns(session, 1, name_contains="демо")
        return [c.campaign_id for c in candidates]

    assert asyncio.run(_with_db(scenario)) == [2]


def test_find_excludes_given_ids() -> None:
    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, external_id="100", name="ТЕСТ 1"))
        session.add(_campaign(2, external_id="200", name="ТЕСТ 2"))
        await session.commit()
        candidates = await find_test_campaigns(session, 1, exclude_ids=[2])
        return [c.campaign_id for c in candidates]

    assert asyncio.run(_with_db(scenario)) == [1]


def test_find_ignores_campaign_without_spec_name() -> None:
    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, external_id="100", name=None))
        await session.commit()
        candidates = await find_test_campaigns(session, 1)
        return [c.campaign_id for c in candidates]

    assert asyncio.run(_with_db(scenario)) == []


# --- find_test_campaigns / cleanup_test_campaigns: защита от негодного критерия -----
# Имя кампании собирается как «<цель> · <ФИО клиента>» — в нём почти всегда есть
# пробел, поэтому пустая строка и строка из пробелов матчат практически любую
# кампанию тенанта. Защита обязана срабатывать и при прямом вызове сервиса, в обход
# CLI-скрипта (см. docstring `validate_name_contains`).


def test_find_rejects_empty_name_contains() -> None:
    async def scenario(session: AsyncSession) -> None:
        await find_test_campaigns(session, 1, name_contains="")

    with pytest.raises(ValueError, match="пуст"):
        asyncio.run(_with_db(scenario))


def test_find_rejects_whitespace_only_name_contains() -> None:
    async def scenario(session: AsyncSession) -> None:
        await find_test_campaigns(session, 1, name_contains="   ")

    with pytest.raises(ValueError, match="пуст"):
        asyncio.run(_with_db(scenario))


def test_find_rejects_too_short_name_contains() -> None:
    async def scenario(session: AsyncSession) -> None:
        await find_test_campaigns(session, 1, name_contains="ab")

    with pytest.raises(ValueError, match="коротк"):
        asyncio.run(_with_db(scenario))


def test_find_accepts_normal_criterion_unchanged() -> None:
    """Обычный критерий («тест») по-прежнему работает как раньше, без регрессий."""

    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, external_id="100", name="Подписчики · ТЕСТ Иванов"))
        session.add(_campaign(2, external_id="200", name="Подписчики · Настоящий Клиент"))
        await session.commit()
        candidates = await find_test_campaigns(session, 1, name_contains="тест")
        return [c.campaign_id for c in candidates]

    assert asyncio.run(_with_db(scenario)) == [1]


def test_cleanup_rejects_bad_criterion_even_without_adapter() -> None:
    """Проверка критерия должна срабатывать и через `cleanup_test_campaigns` напрямую —
    не только через `find_test_campaigns` и не только из CLI-скрипта."""

    async def scenario(session: AsyncSession) -> None:
        await cleanup_test_campaigns(session, 1, name_contains="")

    with pytest.raises(ValueError, match="пуст"):
        asyncio.run(_with_db(scenario))


def test_cleanup_rejects_whitespace_only_criterion_before_touching_db() -> None:
    async def scenario(session: AsyncSession) -> None:
        session.add(_campaign(1, external_id="100", name="Подписчики · Настоящий Клиент"))
        await session.commit()
        await cleanup_test_campaigns(session, 1, name_contains=" ")

    with pytest.raises(ValueError, match="пуст"):
        asyncio.run(_with_db(scenario))


# --- cleanup_test_campaigns: сухой прогон -------------------------------------------


def test_dry_run_changes_nothing() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> tuple[CleanupSummary, int]:
        session.add(_campaign(1, external_id="100", name="ТЕСТ Иванов"))
        session.add(_campaign(2, external_id="200", name="Живая Петров"))
        await session.commit()
        summary = await cleanup_test_campaigns(session, 1, adapter=adapter)
        await session.commit()
        return summary, await _count_campaigns(session)

    summary, remaining = asyncio.run(_with_db(scenario))
    assert summary.dry_run is True
    assert [c.campaign_id for c in summary.candidates] == [1]
    assert summary.outcomes == []
    assert remaining == 2
    assert adapter.deleted == []


def test_dry_run_is_the_default() -> None:
    async def scenario(session: AsyncSession) -> CleanupSummary:
        session.add(_campaign(1, external_id="100", name="ТЕСТ"))
        await session.commit()
        # Ни adapter, ни dry_run не переданы явно — должно остаться сухим прогоном.
        return await cleanup_test_campaigns(session, 1)

    assert asyncio.run(_with_db(scenario)).dry_run is True


def test_real_run_without_adapter_raises() -> None:
    async def scenario(session: AsyncSession) -> None:
        session.add(_campaign(1, external_id="100", name="ТЕСТ"))
        await session.commit()
        await cleanup_test_campaigns(session, 1, dry_run=False)

    with pytest.raises(ValueError, match="adapter"):
        asyncio.run(_with_db(scenario))


# --- cleanup_test_campaigns: реальное удаление --------------------------------------


def test_real_run_deletes_only_matching_campaigns() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> tuple[CleanupSummary, list[int]]:
        session.add(_campaign(1, external_id="100", name="ТЕСТ Иванов"))
        session.add(_campaign(2, external_id="200", name="Живая Петров"))
        await session.commit()
        summary = await cleanup_test_campaigns(session, 1, adapter=adapter, dry_run=False)
        await session.commit()
        remaining = (await session.execute(select(Campaign.id))).scalars().all()
        return summary, sorted(remaining)

    summary, remaining = asyncio.run(_with_db(scenario))
    assert summary.dry_run is False
    assert summary.deleted == 1
    assert summary.skipped == 0
    assert summary.errors == 0
    assert remaining == [2]
    assert adapter.deleted == ["100"]


def test_real_run_never_touches_campaigns_outside_criterion() -> None:
    # Кандидатов всего два: третья кампания вообще не участвует в прогоне.
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> list[int]:
        session.add(_campaign(1, external_id="100", name="ТЕСТ 1"))
        session.add(_campaign(2, external_id="200", name="ТЕСТ 2"))
        session.add(_campaign(3, external_id="300", name="Боевая Сидоров"))
        await session.commit()
        await cleanup_test_campaigns(session, 1, adapter=adapter, dry_run=False)
        await session.commit()
        remaining = (await session.execute(select(Campaign.id))).scalars().all()
        return sorted(remaining)

    assert asyncio.run(_with_db(scenario)) == [3]


def test_excluded_id_is_not_deleted() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> tuple[list[int], list[str]]:
        session.add(_campaign(1, external_id="100", name="ТЕСТ 1"))
        session.add(_campaign(2, external_id="200", name="ТЕСТ 2"))
        await session.commit()
        await cleanup_test_campaigns(session, 1, adapter=adapter, exclude_ids=[2], dry_run=False)
        await session.commit()
        remaining = (await session.execute(select(Campaign.id))).scalars().all()
        return sorted(remaining), adapter.deleted

    remaining, deleted = asyncio.run(_with_db(scenario))
    assert remaining == [2]
    assert deleted == ["100"]


def test_stub_campaign_is_not_sent_to_adapter_but_row_is_deleted() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> tuple[list[int], list[str]]:
        session.add(
            _campaign(
                1, external_id=f"{STUB_EXTERNAL_ID_PREFIX}campaign-1", name="ТЕСТ на заглушке"
            )
        )
        await session.commit()
        await cleanup_test_campaigns(session, 1, adapter=adapter, dry_run=False)
        await session.commit()
        remaining = list((await session.execute(select(Campaign.id))).scalars().all())
        return remaining, adapter.deleted

    remaining, deleted = asyncio.run(_with_db(scenario))
    assert remaining == []
    assert deleted == []  # заглушку в адаптер не звали вовсе


def test_campaign_without_external_id_skips_adapter_but_row_is_deleted() -> None:
    adapter = _FakeAdapter()

    async def scenario(session: AsyncSession) -> tuple[list[int], list[str]]:
        session.add(_campaign(1, external_id=None, name="ТЕСТ без внешнего id"))
        await session.commit()
        await cleanup_test_campaigns(session, 1, adapter=adapter, dry_run=False)
        await session.commit()
        remaining = list((await session.execute(select(Campaign.id))).scalars().all())
        return remaining, adapter.deleted

    remaining, deleted = asyncio.run(_with_db(scenario))
    assert remaining == []
    assert deleted == []


def test_error_on_one_campaign_does_not_stop_the_rest() -> None:
    adapter = _FakeAdapter(fail_for=frozenset({"100"}))

    async def scenario(session: AsyncSession) -> tuple[CleanupSummary, list[int]]:
        session.add(_campaign(1, external_id="100", name="ТЕСТ падает"))
        session.add(_campaign(2, external_id="200", name="ТЕСТ проходит"))
        await session.commit()
        summary = await cleanup_test_campaigns(session, 1, adapter=adapter, dry_run=False)
        await session.commit()
        remaining = (await session.execute(select(Campaign.id))).scalars().all()
        return summary, sorted(remaining)

    summary, remaining = asyncio.run(_with_db(scenario))
    assert summary.deleted == 1
    assert summary.errors == 1
    # Упавшая кампания осталась и в БД, и её внешний id никуда не делся.
    assert remaining == [1]
    error_outcomes = [o for o in summary.outcomes if o.result == "error"]
    assert len(error_outcomes) == 1
    assert error_outcomes[0].campaign_id == 1
    assert "100" in error_outcomes[0].detail or "vk refused" in error_outcomes[0].detail


def test_summary_counts_expose_ok_skipped_errors() -> None:
    adapter = _FakeAdapter(fail_for=frozenset({"100"}))

    async def scenario(session: AsyncSession) -> CleanupSummary:
        session.add(_campaign(1, external_id="100", name="ТЕСТ падает"))
        session.add(_campaign(2, external_id="200", name="ТЕСТ проходит"))
        session.add(_campaign(3, external_id=f"{STUB_EXTERNAL_ID_PREFIX}x", name="ТЕСТ заглушка"))
        await session.commit()
        summary = await cleanup_test_campaigns(session, 1, adapter=adapter, dry_run=False)
        await session.commit()
        return summary

    summary = asyncio.run(_with_db(scenario))
    assert summary.deleted == 2
    assert summary.errors == 1
    assert summary.skipped == 0
