"""Юнит-тесты services/invites.create_invite (spec §7.1): статусы + supersede."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from db.base import Base
from db.models import Account, BriefInvite
from db.repositories import get_or_create_operator
from services.contact import Contact, ContactType
from services.delivery.base import DeliveryChannel, DeliveryResult
from services.invites import create_invite
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")


class _StubAdapter:
    """Адаптер с заранее заданным результатом send()."""

    def __init__(self, result: DeliveryResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        self.calls.append((contact.value, invite_text))
        return self._result


class _StubRouter:
    """Роутер, всегда отдающий один и тот же адаптер (мок DeliveryRouter)."""

    def __init__(self, adapter: _StubAdapter) -> None:
        self._adapter = adapter

    def route(self, contact: Contact) -> _StubAdapter:
        return self._adapter


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


def test_telegram_ok_marks_sent() -> None:
    async def scenario(session: AsyncSession) -> None:
        op = await get_or_create_operator(session, 1, 555)
        adapter = _StubAdapter(DeliveryResult(ok=True, channel=DeliveryChannel.TELEGRAM))
        router = _StubRouter(adapter)
        result = await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="individual",
            contact=Contact(ContactType.TELEGRAM, "@ivanov"),
            router=router,
            base_url="https://vk-ads-auto.ru",
        )
        assert result.status == "sent"
        assert result.channel == "telegram"
        assert result.error is None
        # Ссылка в приглашении содержит токен инвайта (?t=...).
        assert "?t=" in adapter.calls[0][1]

    asyncio.run(_with_db(scenario))


def test_manual_ok_marks_sent() -> None:
    async def scenario(session: AsyncSession) -> None:
        op = await get_or_create_operator(session, 1, 555)
        adapter = _StubAdapter(
            DeliveryResult(ok=True, channel=DeliveryChannel.MANUAL, fallback_text="перешлите")
        )
        result = await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="individual",
            contact=Contact(ContactType.PHONE, "+79991234567"),
            router=_StubRouter(adapter),
            base_url="https://vk-ads-auto.ru",
        )
        assert result.status == "sent"
        assert result.channel == "manual"
        assert result.fallback_text == "перешлите"

    asyncio.run(_with_db(scenario))


def test_failed_marks_failed_with_error() -> None:
    async def scenario(session: AsyncSession) -> None:
        op = await get_or_create_operator(session, 1, 555)
        adapter = _StubAdapter(
            DeliveryResult(
                ok=False,
                channel=DeliveryChannel.TELEGRAM,
                fallback_text="перешлите вручную",
                error="username_not_occupied",
            )
        )
        result = await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="community",
            contact=Contact(ContactType.TELEGRAM, "@nobody"),
            router=_StubRouter(adapter),
            base_url="https://vk-ads-auto.ru",
        )
        assert result.status == "failed"
        assert result.error == "username_not_occupied"
        assert result.fallback_text == "перешлите вручную"

    asyncio.run(_with_db(scenario))


def test_second_failed_supersedes_previous() -> None:
    async def scenario(session: AsyncSession) -> None:
        op = await get_or_create_operator(session, 1, 555)
        contact = Contact(ContactType.TELEGRAM, "@nobody")
        fail = DeliveryResult(
            ok=False,
            channel=DeliveryChannel.TELEGRAM,
            fallback_text="fb",
            error="username_not_occupied",
        )
        first = await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="individual",
            contact=contact,
            router=_StubRouter(_StubAdapter(fail)),
            base_url="https://vk-ads-auto.ru",
        )
        second = await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="individual",
            contact=contact,
            router=_StubRouter(_StubAdapter(fail)),
            base_url="https://vk-ads-auto.ru",
        )
        # Первый failed → superseded, второй остаётся failed.
        prev = (
            await session.execute(select(BriefInvite).where(BriefInvite.id == first.invite_id))
        ).scalar_one()
        cur = (
            await session.execute(select(BriefInvite).where(BriefInvite.id == second.invite_id))
        ).scalar_one()
        assert prev.status == "superseded"
        assert cur.status == "failed"

    asyncio.run(_with_db(scenario))


def test_success_does_not_supersede_previous_failed() -> None:
    async def scenario(session: AsyncSession) -> None:
        op = await get_or_create_operator(session, 1, 555)
        contact = Contact(ContactType.TELEGRAM, "@user")
        fail = DeliveryResult(
            ok=False, channel=DeliveryChannel.TELEGRAM, fallback_text="fb", error="peer_flood"
        )
        ok = DeliveryResult(ok=True, channel=DeliveryChannel.TELEGRAM)
        first = await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="individual",
            contact=contact,
            router=_StubRouter(_StubAdapter(fail)),
            base_url="https://vk-ads-auto.ru",
        )
        await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="individual",
            contact=contact,
            router=_StubRouter(_StubAdapter(ok)),
            base_url="https://vk-ads-auto.ru",
        )
        # Успех НЕ трогает предыдущий failed (supersede только при новой ошибке).
        prev = (
            await session.execute(select(BriefInvite).where(BriefInvite.id == first.invite_id))
        ).scalar_one()
        assert prev.status == "failed"

    asyncio.run(_with_db(scenario))


def test_operator_fk_persisted() -> None:
    async def scenario(session: AsyncSession) -> None:
        op = await get_or_create_operator(session, 1, 777)
        result = await create_invite(
            session,
            account_id=1,
            operator_id=op.id,
            variant="individual",
            contact=Contact(ContactType.EMAIL, "a@b.ru"),
            router=_StubRouter(
                _StubAdapter(DeliveryResult(ok=True, channel=DeliveryChannel.EMAIL))
            ),
            base_url="https://vk-ads-auto.ru",
        )
        invite = (
            await session.execute(select(BriefInvite).where(BriefInvite.id == result.invite_id))
        ).scalar_one()
        assert invite.operator_id == op.id
        assert invite.contact_type == "email"
        assert invite.contact_value == "a@b.ru"

    asyncio.run(_with_db(scenario))
