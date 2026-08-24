"""Тесты мини-отчёта клиента (B1): свои кампании и метрики, расход не отдаётся.

ТЗ 4.2 / критерий приёмки Блока 2 — «мини-отчёт по его целям и статистике
(read-only)». Расход клиенту не показываем ни в каком виде — это сознательное
решение проекта (клиент платит за услугу, а не за медиабюджет), поэтому поля
расхода в типах отчёта не существует вовсе — доказываем это `hasattr`, а не
проверкой значения.

Метрики — последний срез по кампании: переиспользуем `aggregate_cabinet_stats`
(уже проверенный приём `ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY
captured_at DESC)`, см. `tests/test_cabinet_stats_service.py`) — VK отдаёт
накопительный итог с начала кампании, суммировать срезы нельзя.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from db.base import Base
from db.models import Account, Brief, Campaign, Client, Stat
from services.client_report import ClientReport, build_client_report
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


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
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


async def _seed_campaign(
    session: AsyncSession,
    *,
    client_id: int,
    brief_id: int,
    external_id: str,
    account_id: int = 1,
    status: str = "launched",
    object_kind: str = "senler",
    create_client: bool = True,
) -> Campaign:
    """Клиент (опционально) + бриф (FK) + кампания-«кабинет».

    `create_client=False` — для сценариев, где клиент с таким `id` уже заведён
    (например, чтобы завести вторую кампанию на него же, но под другим `account_id`
    в самой кампании: `client.id` — глобальный PK, не составной по тенанту).
    """
    if create_client:
        session.add(Client(id=client_id, account_id=account_id, full_name=f"Клиент {client_id}"))
    session.add(
        Brief(
            id=brief_id,
            account_id=account_id,
            client_id=client_id,
            variant="individual",
            payload={},
        )
    )
    campaign = Campaign(
        account_id=account_id,
        brief_id=brief_id,
        client_id=client_id,
        objective="socialengagement",
        status=status,
        external_id=external_id,
        spec_json={"object_kind": object_kind},
    )
    session.add(campaign)
    await session.commit()
    return campaign


def test_report_shows_own_campaigns_without_spend() -> None:
    """Клиент видит свои метрики; расход не отдаётся наружу ни в каком виде."""

    async def scenario(session: AsyncSession) -> ClientReport:
        await _seed_campaign(session, client_id=42, brief_id=1, external_id="777")
        session.add(
            Stat(
                account_id=1,
                campaign_id="777",
                shows=100,
                clicks=8,
                spent=999,
                results=5,
                captured_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        await session.commit()
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    assert [row.external_id for row in report.campaigns] == ["777"]
    row = report.campaigns[0]
    assert row.shows == 100
    assert row.results == 5
    assert not hasattr(row, "spent")


def test_report_never_leaks_another_clients_campaign() -> None:
    """Кампания другого клиента того же тенанта не попадает в отчёт."""

    async def scenario(session: AsyncSession) -> ClientReport:
        await _seed_campaign(session, client_id=42, brief_id=1, external_id="777")
        await _seed_campaign(session, client_id=43, brief_id=2, external_id="888")
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    assert all(row.external_id != "888" for row in report.campaigns)
    assert [row.external_id for row in report.campaigns] == ["777"]


def test_report_scoped_by_tenant_too() -> None:
    """Кампания под чужим `account_id`, даже с тем же `client_id`, в отчёт не попадает.

    Фильтр по `client_id` один не спасает: изоляция должна идти и по `account_id`
    (CLAUDE.md §1.3 — мульти-тенант-готовность, скоуп по тенанту на каждом запросе).
    """

    async def scenario(session: AsyncSession) -> ClientReport:
        session.add(Account(id=2, name="other-tenant"))
        await session.commit()
        await _seed_campaign(session, client_id=42, brief_id=1, external_id="777", account_id=1)
        await _seed_campaign(
            session,
            client_id=42,
            brief_id=2,
            external_id="999",
            account_id=2,
            create_client=False,
        )
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    assert [row.external_id for row in report.campaigns] == ["777"]


def test_report_translates_goal_and_status_to_russian() -> None:
    """Цель и статус приходят человеческим текстом, не сырыми кодами площадки."""

    async def scenario(session: AsyncSession) -> ClientReport:
        await _seed_campaign(
            session,
            client_id=42,
            brief_id=1,
            external_id="777",
            status="launched",
            object_kind="senler",
        )
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    row = report.campaigns[0]
    assert row.status == "запущена"
    assert row.goal == "Заявка через Senler"


def test_report_uses_latest_snapshot_not_sum() -> None:
    """VK отдаёт накопительный итог — берём последний срез, а не сумму истории."""

    async def scenario(session: AsyncSession) -> ClientReport:
        await _seed_campaign(session, client_id=42, brief_id=1, external_id="777")
        session.add(
            Stat(
                account_id=1,
                campaign_id="777",
                shows=100,
                clicks=10,
                spent=50,
                captured_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            )
        )
        session.add(
            Stat(
                account_id=1,
                campaign_id="777",
                shows=200,
                clicks=20,
                spent=90,
                captured_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            )
        )
        await session.commit()
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    assert report.campaigns[0].shows == 200.0


def test_report_ctr_derived() -> None:
    async def scenario(session: AsyncSession) -> ClientReport:
        await _seed_campaign(session, client_id=42, brief_id=1, external_id="777")
        session.add(Stat(account_id=1, campaign_id="777", shows=1000, clicks=50, spent=1))
        await session.commit()
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    assert report.campaigns[0].ctr == 5.0  # 50/1000*100


def test_report_empty_when_client_has_no_campaigns() -> None:
    """Пустой список — нет кампаний, а не ошибка."""

    async def scenario(session: AsyncSession) -> ClientReport:
        session.add(Client(id=42, account_id=1, full_name="Клиент"))
        await session.commit()
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    assert report.campaigns == []


def test_report_unknown_object_kind_gets_honest_placeholder_not_a_crash() -> None:
    """Легаси-кампания без сохранённого object_kind не роняет отчёт."""

    async def scenario(session: AsyncSession) -> ClientReport:
        campaign = await _seed_campaign(
            session, client_id=42, brief_id=1, external_id="777", object_kind=""
        )
        campaign.spec_json = {}
        await session.commit()
        return await build_client_report(session, account_id=1, client_id=42)

    report = asyncio.run(_with_db(scenario))
    assert report.campaigns[0].goal  # непустая строка, отчёт не упал
