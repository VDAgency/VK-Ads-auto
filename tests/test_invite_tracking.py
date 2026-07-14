"""Тесты сервиса трекинга брифов (PR-B): pending/recent + подсчёт дней."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from db.base import Base
from db.models import Account, Operator
from db.repositories import (
    create_brief_invite,
    mark_invite_received_if_sent,
    mark_invite_sent,
)
from services.invite_tracking import list_pending, list_recent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


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
        session.add(Operator(id=10, account_id=1, telegram_id=555, full_name="Оператор"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


async def _make_sent(session: AsyncSession, token: str, contact: str, delivered: datetime) -> int:
    invite = await create_brief_invite(
        session, 1, 10, token, "individual", "email", contact, "email"
    )
    await mark_invite_sent(session, invite.id)
    invite.delivered_at = delivered
    await session.flush()
    return invite.id


def test_pending_lists_only_sent_with_waiting_days() -> None:
    async def scenario(session: AsyncSession) -> list[tuple[str, int]]:
        await _make_sent(session, "t1", "a@b.c", NOW - timedelta(days=3))
        await _make_sent(session, "t2", "d@e.f", NOW - timedelta(days=1))
        views = await list_pending(session, 1, now=NOW)
        return [(v.contact, v.waiting_days) for v in views]

    result = asyncio.run(_with_db(scenario))
    # Сортировка: старее сверху.
    assert result == [("a@b.c", 3), ("d@e.f", 1)]


def test_pending_excludes_received() -> None:
    async def scenario(session: AsyncSession) -> list[str]:
        received_id = await _make_sent(session, "t1", "got@it.c", NOW - timedelta(days=2))
        await mark_invite_received_if_sent(session, received_id)
        await _make_sent(session, "t2", "wait@it.c", NOW - timedelta(days=1))
        views = await list_pending(session, 1, now=NOW)
        return [v.contact for v in views]

    assert asyncio.run(_with_db(scenario)) == ["wait@it.c"]


async def _make_received(
    session: AsyncSession, token: str, contact: str, received: datetime
) -> int:
    invite_id = await _make_sent(session, token, contact, received - timedelta(days=1))
    await mark_invite_received_if_sent(session, invite_id)
    # received_at ставится реальным временем — фиксируем явно для детерминизма окна.
    from db.models import BriefInvite

    invite = await session.get(BriefInvite, invite_id)
    assert invite is not None
    invite.received_at = received
    await session.flush()
    return invite_id


def test_recent_lists_received_within_window() -> None:
    async def scenario(session: AsyncSession) -> list[str]:
        await _make_received(session, "t1", "fresh@it.c", NOW - timedelta(days=1))
        await _make_received(session, "t2", "old@it.c", NOW - timedelta(days=30))
        views = await list_recent(session, 1, within_days=7, now=NOW)
        return [v.contact for v in views]

    # Старый (30 дней) за окном — только свежий.
    assert asyncio.run(_with_db(scenario)) == ["fresh@it.c"]


def test_recent_excludes_still_pending() -> None:
    async def scenario(session: AsyncSession) -> int:
        await _make_sent(session, "t1", "wait@it.c", NOW - timedelta(days=1))
        views = await list_recent(session, 1, now=NOW)
        return len(views)

    assert asyncio.run(_with_db(scenario)) == 0
