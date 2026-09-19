"""Тесты ежедневной сводки оператору (`services.daily_digest`, задача 3А).

`render_digest` — чистое форматирование, без БД. `collect_digest` проверяется
на SQLite-фикстурах по образцу `tests/test_stats_sync.py`; сам синк подменяется
(«замокать синк... так же, как в существующих тестах синка», брифинг задачи) —
здесь это monkeypatch `services.daily_digest.sync_campaign_stats`, чтобы не
тянуть в тест ещё и живой выбор адаптера/канала, уже покрытый test_stats_sync.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, TypeVar

import pytest
import services.daily_digest as daily_digest_module
from config.settings import Settings
from db.base import Base
from db.models import Account, AdAccount, Brief, Campaign, Client, Stat
from services.daily_digest import DigestReport, DigestRow, collect_digest, render_digest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


def _settings() -> Settings:
    return Settings(_env_file=None)


# --- render_digest: чистое форматирование -------------------------------------------


def _row(**over: Any) -> DigestRow:
    base: dict[str, Any] = {
        "title": "Подписчики · Иван Иванов",
        "cabinet": "Кабинет «Основной»",
        "status": "launched",
        "shows": 1000.0,
        "clicks": 50.0,
        "spent": 500.0,
        "results": 10.0,
        "stale": False,
    }
    base.update(over)
    return DigestRow(**base)


def test_render_digest_empty_mentions_no_campaigns_and_date() -> None:
    report = DigestReport(rows=[], day=date(2026, 9, 19))
    text = render_digest(report)
    assert "Активных кампаний нет" in text
    assert "19.09.2026" in text


def test_render_digest_single_campaign_has_all_metrics() -> None:
    report = DigestReport(rows=[_row()], day=date(2026, 9, 19))
    text = render_digest(report)
    assert "Подписчики · Иван Иванов" in text
    assert "показы 1000" in text
    assert "клики 50" in text
    assert "расход 500" in text
    assert "результаты 10" in text
    assert "CTR 5.0%" in text  # 50/1000*100
    assert "CPC 10.0 ₽" in text  # 500/50
    assert "CPL 50.0 ₽" in text  # 500/10


def test_render_digest_groups_by_cabinet_with_totals() -> None:
    rows = [
        _row(title="Кампания A", cabinet="Кабинет 1", spent=100.0, results=5.0),
        _row(title="Кампания B", cabinet="Кабинет 2", spent=200.0, results=7.0),
    ]
    report = DigestReport(rows=rows, day=date(2026, 9, 19))
    text = render_digest(report)

    assert "Кабинет «Кабинет 1»" in text
    assert "Кабинет «Кабинет 2»" in text
    assert text.index("Кабинет «Кабинет 1»") < text.index("Кампания A")
    assert text.index("Кабинет «Кабинет 2»") < text.index("Кампания B")
    assert "Итого: расход 300 ₽, результатов 12." in text


def test_render_digest_marks_stale_row() -> None:
    report = DigestReport(rows=[_row(stale=True)], day=date(2026, 9, 19))
    text = render_digest(report)
    assert "данные не обновились" in text


def test_render_digest_zero_clicks_does_not_raise_and_shows_dash() -> None:
    report = DigestReport(rows=[_row(clicks=0.0, results=0.0)], day=date(2026, 9, 19))
    text = render_digest(report)  # не должно упасть ZeroDivisionError
    assert "CPC —" in text
    assert "CPL —" in text


# --- collect_digest: сборка отчёта по БД --------------------------------------------


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
        session.add(Client(id=1, account_id=1, full_name="Иван Иванов"))
        session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
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
    ad_account_id: int | None = None,
    spec_name: str = "Подписчики · Иван Иванов",
) -> Campaign:
    return Campaign(
        id=campaign_id,
        account_id=account_id,
        brief_id=1,
        client_id=1,
        cabinet_id=None,
        ad_account_id=ad_account_id,
        status=status,
        objective="socialengagement",
        external_id=external_id,
        spec_json={"name": spec_name},
    )


def _mock_sync(outcomes: dict[int, str]) -> Callable[..., Awaitable[dict[int, str]]]:
    async def fake(session: AsyncSession, account_id: int, **_: Any) -> dict[int, str]:
        return dict(outcomes)

    return fake


def test_collect_digest_builds_row_with_title_and_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(
            AdAccount(
                id=1,
                account_id=1,
                title="Кабинет «Ромашка»",
                external_id="100500",
                token_tail="",
            )
        )
        session.add(_campaign(1, external_id="ext-1", ad_account_id=1))
        session.add(
            Stat(account_id=1, campaign_id="ext-1", shows=100, clicks=5, spent=250, results=10)
        )
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.title == "Подписчики · Иван Иванов"
    assert row.cabinet == "Кабинет «Ромашка»"
    assert row.status == "launched"
    assert row.shows == 100
    assert row.clicks == 5
    assert row.spent == 250
    assert row.results == 10
    assert row.stale is False


def test_collect_digest_campaign_without_ad_account_shows_dash_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1", ad_account_id=None))
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert report.rows[0].cabinet == "—"


def test_collect_digest_campaign_without_any_stat_row_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кампания без единого сохранённого среза — честные нули, а не выдумка."""
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1"))
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    row = report.rows[0]
    assert row.shows == 0.0
    assert row.clicks == 0.0
    assert row.spent == 0.0
    assert row.results == 0.0


def test_collect_digest_uses_latest_stat_slice_not_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK отдаёт накопительный итог — берём последний срез, не сумму (как cabinet_stats)."""
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1"))
        session.add(
            Stat(
                account_id=1,
                campaign_id="ext-1",
                shows=100,
                clicks=10,
                spent=50,
                captured_at=datetime(2026, 9, 19, 6, 0, tzinfo=UTC),
            )
        )
        session.add(
            Stat(
                account_id=1,
                campaign_id="ext-1",
                shows=200,
                clicks=20,
                spent=90,
                captured_at=datetime(2026, 9, 19, 7, 0, tzinfo=UTC),
            )
        )
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    row = report.rows[0]
    assert row.shows == 200.0
    assert row.spent == 90.0


def test_collect_digest_marks_failed_sync_as_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "error"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1"))
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert report.rows[0].stale is True


def test_collect_digest_day_is_moscow_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({}))

    async def scenario(session: AsyncSession) -> DigestReport:
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    expected = datetime.now(timezone(timedelta(hours=3))).date()
    assert report.day == expected


def test_collect_digest_empty_when_no_active_campaigns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({}))

    async def scenario(session: AsyncSession) -> DigestReport:
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert report.rows == []
