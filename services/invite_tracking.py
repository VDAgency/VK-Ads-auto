"""Трекинг брифов (команда №3): кого ждём и кто прислал недавно.

Headless-ядро: выборки и подсчёт дней ожидания здесь, рендер — в боте. Источник
данных — таблица `BriefInvite` (живые статусы отправки/приёма).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from db.repositories import list_pending_invites, list_recent_received_invites
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class InviteView:
    """Строка трекинга для рендера в канале (бот/веб)."""

    contact: str
    contact_name: str | None
    variant: str
    channel: str
    sent_at: datetime | None
    received_at: datetime | None
    waiting_days: int


def _as_utc(value: datetime) -> datetime:
    """Привести к tz-aware UTC. SQLite отдаёт naive-даты (Postgres — aware)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _waiting_days(delivered_at: datetime | None, now: datetime) -> int:
    """Сколько полных суток ждём с момента доставки (0, если даты нет)."""
    if delivered_at is None:
        return 0
    return max((now - _as_utc(delivered_at)).days, 0)


async def list_pending(
    session: AsyncSession,
    account_id: int,
    *,
    now: datetime | None = None,
) -> list[InviteView]:
    """Инвайты, доставленные клиенту, но ещё не вернувшиеся брифом."""
    now = now or datetime.now(UTC)
    invites = await list_pending_invites(session, account_id)
    return [
        InviteView(
            contact=inv.contact_value,
            contact_name=inv.contact_name,
            variant=inv.variant,
            channel=inv.channel,
            sent_at=inv.delivered_at,
            received_at=None,
            waiting_days=_waiting_days(inv.delivered_at, now),
        )
        for inv in invites
    ]


async def list_recent(
    session: AsyncSession,
    account_id: int,
    *,
    within_days: int = 7,
    now: datetime | None = None,
) -> list[InviteView]:
    """Инвайты, по которым бриф пришёл за последние `within_days` суток."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=within_days)
    invites = await list_recent_received_invites(session, account_id, since)
    return [
        InviteView(
            contact=inv.contact_value,
            contact_name=inv.contact_name,
            variant=inv.variant,
            channel=inv.channel,
            sent_at=inv.delivered_at,
            received_at=inv.received_at,
            waiting_days=0,
        )
        for inv in invites
    ]
