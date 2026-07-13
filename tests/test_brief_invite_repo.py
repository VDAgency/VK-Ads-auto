"""Тесты репозитория BriefInvite (spec 2026-07-13 §4).

Проверяем переходы статус-машины, supersede-логику для повторной ошибочной
отправки и атомарность `mark_invite_received_if_sent` (защита от гонки при
двойном POST /briefs).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from db.base import Base
from db.models import Account, BriefInvite, Operator
from db.repositories import (
    create_brief_invite,
    find_brief_invite_by_token,
    find_last_failed_invite,
    mark_invite_failed,
    mark_invite_received_if_sent,
    mark_invite_sent,
    mark_invite_superseded,
)
from sqlalchemy import select
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
        session.add(Operator(id=10, account_id=1, telegram_id=555, full_name="Оператор"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_create_invite_starts_in_pending() -> None:
    async def scenario(session: AsyncSession) -> str:
        invite = await create_brief_invite(
            session,
            account_id=1,
            operator_id=10,
            token="tok-abc",
            variant="individual",
            contact_type="telegram",
            contact_value="@ivanov",
            channel="telegram",
        )
        return invite.status

    assert asyncio.run(_with_db(scenario)) == "pending"


def test_find_by_token_returns_invite() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        await create_brief_invite(
            session, 1, 10, "tok-xyz", "individual", "email", "a@b.c", "email"
        )
        found = await find_brief_invite_by_token(session, "tok-xyz")
        return found.contact_value if found else None

    assert asyncio.run(_with_db(scenario)) == "a@b.c"


def test_find_by_token_missing_returns_none() -> None:
    async def scenario(session: AsyncSession) -> BriefInvite | None:
        return await find_brief_invite_by_token(session, "nonexistent")

    assert asyncio.run(_with_db(scenario)) is None


def test_mark_sent_transitions_status_and_delivered_at() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, bool]:
        invite = await create_brief_invite(
            session, 1, 10, "tok-1", "individual", "telegram", "@u", "telegram"
        )
        await mark_invite_sent(session, invite.id)
        await session.refresh(invite)
        return invite.status, invite.delivered_at is not None

    status, has_delivered_at = asyncio.run(_with_db(scenario))
    assert status == "sent"
    assert has_delivered_at


def test_mark_failed_sets_error_code() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, str | None]:
        invite = await create_brief_invite(
            session, 1, 10, "tok-2", "individual", "telegram", "@u", "telegram"
        )
        await mark_invite_failed(session, invite.id, "username_not_occupied")
        await session.refresh(invite)
        return invite.status, invite.error

    status, error = asyncio.run(_with_db(scenario))
    assert status == "failed"
    assert error == "username_not_occupied"


def test_find_last_failed_returns_most_recent() -> None:
    """При двух failed для одного контакта возвращаем самый свежий (по id)."""

    async def scenario(session: AsyncSession) -> tuple[int, int, int | None]:
        first = await create_brief_invite(
            session, 1, 10, "tok-a", "individual", "telegram", "@u", "telegram"
        )
        await mark_invite_failed(session, first.id, "e1")
        second = await create_brief_invite(
            session, 1, 10, "tok-b", "individual", "telegram", "@u", "telegram"
        )
        await mark_invite_failed(session, second.id, "e2")
        found = await find_last_failed_invite(session, 1, 10, "@u")
        return first.id, second.id, (found.id if found else None)

    first_id, second_id, found_id = asyncio.run(_with_db(scenario))
    assert found_id == second_id
    assert found_id != first_id


def test_find_last_failed_ignores_sent_invites() -> None:
    """Успешный инвайт не должен всплывать в поиске «последний failed»."""

    async def scenario(session: AsyncSession) -> BriefInvite | None:
        sent = await create_brief_invite(
            session, 1, 10, "tok-s", "individual", "email", "a@b.c", "email"
        )
        await mark_invite_sent(session, sent.id)
        return await find_last_failed_invite(session, 1, 10, "a@b.c")

    assert asyncio.run(_with_db(scenario)) is None


def test_find_last_failed_scoped_by_operator() -> None:
    """Failed другого оператора для того же контакта не должен подхватываться."""

    async def scenario(session: AsyncSession) -> BriefInvite | None:
        session.add(Operator(id=11, account_id=1, telegram_id=666))
        await session.flush()
        other = await create_brief_invite(
            session, 1, 11, "tok-o", "individual", "email", "a@b.c", "email"
        )
        await mark_invite_failed(session, other.id, "e")
        return await find_last_failed_invite(session, 1, 10, "a@b.c")

    assert asyncio.run(_with_db(scenario)) is None


def test_mark_superseded_transitions_status() -> None:
    async def scenario(session: AsyncSession) -> str:
        invite = await create_brief_invite(
            session, 1, 10, "tok-sup", "individual", "email", "a@b.c", "email"
        )
        await mark_invite_failed(session, invite.id, "e")
        await mark_invite_superseded(session, invite.id)
        await session.refresh(invite)
        return invite.status

    assert asyncio.run(_with_db(scenario)) == "superseded"


def test_mark_received_if_sent_returns_true_and_sets_received_at() -> None:
    async def scenario(session: AsyncSession) -> tuple[bool, str, bool]:
        invite = await create_brief_invite(
            session, 1, 10, "tok-r", "individual", "email", "a@b.c", "email"
        )
        await mark_invite_sent(session, invite.id)
        ok = await mark_invite_received_if_sent(session, invite.id)
        await session.refresh(invite)
        return ok, invite.status, invite.received_at is not None

    ok, status, has_received_at = asyncio.run(_with_db(scenario))
    assert ok is True
    assert status == "received"
    assert has_received_at


def test_mark_received_if_sent_returns_false_on_second_call() -> None:
    """Двойной POST /briefs с одним токеном — второй должен быть отвергнут."""

    async def scenario(session: AsyncSession) -> tuple[bool, bool]:
        invite = await create_brief_invite(
            session, 1, 10, "tok-dup", "individual", "email", "a@b.c", "email"
        )
        await mark_invite_sent(session, invite.id)
        first = await mark_invite_received_if_sent(session, invite.id)
        second = await mark_invite_received_if_sent(session, invite.id)
        return first, second

    first, second = asyncio.run(_with_db(scenario))
    assert first is True
    assert second is False


def test_mark_received_if_sent_rejects_pending_invite() -> None:
    """Инвайт в pending нельзя перевести в received — сначала должен быть sent."""

    async def scenario(session: AsyncSession) -> tuple[bool, str]:
        invite = await create_brief_invite(
            session, 1, 10, "tok-p", "individual", "email", "a@b.c", "email"
        )
        ok = await mark_invite_received_if_sent(session, invite.id)
        await session.refresh(invite)
        return ok, invite.status

    ok, status = asyncio.run(_with_db(scenario))
    assert ok is False
    assert status == "pending"


def test_mark_received_if_sent_rejects_failed_invite() -> None:
    async def scenario(session: AsyncSession) -> bool:
        invite = await create_brief_invite(
            session, 1, 10, "tok-f", "individual", "email", "a@b.c", "email"
        )
        await mark_invite_failed(session, invite.id, "smtp_unreachable")
        return await mark_invite_received_if_sent(session, invite.id)

    assert asyncio.run(_with_db(scenario)) is False


def test_token_uniqueness_is_enforced() -> None:
    """БД должна отвергать вставку двух инвайтов с одинаковым токеном."""

    async def scenario(session: AsyncSession) -> None:
        await create_brief_invite(
            session, 1, 10, "same-token", "individual", "email", "a@b.c", "email"
        )
        await create_brief_invite(
            session, 1, 10, "same-token", "individual", "email", "x@y.z", "email"
        )

    with pytest.raises(Exception):  # noqa: BLE001,B017 — драйверная ошибка sqlite/postgres
        asyncio.run(_with_db(scenario))


def test_brief_invite_has_account_id_column() -> None:
    """Мульти-тенант-инвариант (§1.3 CLAUDE.md) — account_id обязателен."""
    assert "account_id" in BriefInvite.__table__.columns
    assert BriefInvite.__table__.columns["account_id"].nullable is False


def test_brief_has_invite_id_column() -> None:
    """POST /briefs связывает бриф с инвайтом через это поле."""
    from db.models import Brief

    assert "invite_id" in Brief.__table__.columns
    # nullable — бриф может прийти без инвайта (self-serve с рефкода, старые сценарии).
    assert Brief.__table__.columns["invite_id"].nullable is True


def test_invite_can_be_queried_via_orm_select() -> None:
    """Smoke: базовый ORM-select — модель зарегистрирована в metadata корректно."""

    async def scenario(session: AsyncSession) -> int:
        await create_brief_invite(
            session, 1, 10, "tok-smoke", "individual", "email", "a@b.c", "email"
        )
        rows = (await session.execute(select(BriefInvite))).scalars().all()
        return len(list(rows))

    assert asyncio.run(_with_db(scenario)) == 1
