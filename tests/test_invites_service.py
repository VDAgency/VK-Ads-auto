"""Тесты сервиса создания инвайта `services.invites.create_invite` (PR#4).

Доставка — фейковые адаптеры (ok / fail), чтобы проверить оркестрацию: запись
инвайта, пометка sent/failed, supersede предыдущего failed, токен в ссылке.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from db.base import Base
from db.models import Account, BriefInvite
from db.repositories import find_brief_invite_by_token
from services.brief_parser import BriefVariant
from services.contact import Contact, ContactType
from services.delivery.base import DeliveryChannel, DeliveryResult
from services.delivery.router import DeliveryRouter
from services.invites import create_invite
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

BASE_URL = "https://vk-ads-auto.ru"


class _OkAdapter:
    def __init__(self, channel: DeliveryChannel) -> None:
        self._channel = channel

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        del contact, invite_text
        return DeliveryResult(ok=True, channel=self._channel)


class _NamedOkAdapter:
    """Успешная доставка, вернувшая имя получателя (как Telegram-канал)."""

    def __init__(self, channel: DeliveryChannel, name: str) -> None:
        self._channel = channel
        self._name = name

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        del contact, invite_text
        return DeliveryResult(ok=True, channel=self._channel, recipient_name=self._name)


class _FailAdapter:
    def __init__(self, channel: DeliveryChannel, error: str) -> None:
        self._channel = channel
        self._error = error

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        return DeliveryResult(
            ok=False, channel=self._channel, fallback_text=invite_text, error=self._error
        )


def _ok_router() -> DeliveryRouter:
    return DeliveryRouter(
        telegram=_OkAdapter(DeliveryChannel.TELEGRAM),
        email=_OkAdapter(DeliveryChannel.EMAIL),
        manual=_OkAdapter(DeliveryChannel.MANUAL),
    )


def _fail_telegram_router() -> DeliveryRouter:
    return DeliveryRouter(
        telegram=_FailAdapter(DeliveryChannel.TELEGRAM, "username_not_occupied"),
        email=_OkAdapter(DeliveryChannel.EMAIL),
        manual=_OkAdapter(DeliveryChannel.MANUAL),
    )


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


def test_create_invite_email_ok() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, str, str]:
        contact = Contact(ContactType.EMAIL, "ivan@example.com")
        result = await create_invite(
            session, 1, 555, BriefVariant.INDIVIDUAL, contact, BASE_URL, _ok_router()
        )
        await session.commit()
        invite = await find_brief_invite_by_token(session, result.token)
        assert invite is not None
        return result.status, result.channel, invite.status

    status, channel, invite_status = asyncio.run(_with_db(scenario))
    assert status == "sent"
    assert channel == "email"
    assert invite_status == "sent"


def test_create_invite_token_in_link() -> None:
    async def scenario(session: AsyncSession) -> str:
        contact = Contact(ContactType.EMAIL, "ivan@example.com")
        result = await create_invite(
            session, 1, 555, BriefVariant.INDIVIDUAL, contact, BASE_URL, _ok_router()
        )
        return result.token

    token = asyncio.run(_with_db(scenario))
    assert token  # непустой urlsafe-токен
    assert len(token) <= 32  # влезает в String(32) модели


def test_create_invite_telegram_fail_returns_fallback() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, str | None, str | None]:
        contact = Contact(ContactType.TELEGRAM, "@ivan")
        result = await create_invite(
            session, 1, 555, BriefVariant.COMMUNITY, contact, BASE_URL, _fail_telegram_router()
        )
        return result.status, result.error, result.fallback_text

    status, error, fallback = asyncio.run(_with_db(scenario))
    assert status == "failed"
    assert error == "username_not_occupied"
    assert fallback is not None and fallback.startswith("Здравствуйте")


def test_create_invite_stores_recipient_name() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        contact = Contact(ContactType.TELEGRAM, "@cryptosamara")
        router = DeliveryRouter(
            telegram=_NamedOkAdapter(DeliveryChannel.TELEGRAM, "Вячеслав"),
            email=_OkAdapter(DeliveryChannel.EMAIL),
            manual=_OkAdapter(DeliveryChannel.MANUAL),
        )
        result = await create_invite(
            session, 1, 555, BriefVariant.INDIVIDUAL, contact, BASE_URL, router
        )
        await session.commit()
        invite = await find_brief_invite_by_token(session, result.token)
        assert invite is not None
        return invite.contact_name

    assert asyncio.run(_with_db(scenario)) == "Вячеслав"


def test_create_invite_supersedes_previous_failed() -> None:
    async def scenario(session: AsyncSession) -> list[str]:
        contact = Contact(ContactType.TELEGRAM, "@ivan")
        # Две подряд неудачные отправки на один контакт.
        await create_invite(
            session, 1, 555, BriefVariant.COMMUNITY, contact, BASE_URL, _fail_telegram_router()
        )
        await create_invite(
            session, 1, 555, BriefVariant.COMMUNITY, contact, BASE_URL, _fail_telegram_router()
        )
        await session.commit()
        stmt = (
            select(BriefInvite).where(BriefInvite.contact_value == "@ivan").order_by(BriefInvite.id)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [r.status for r in rows]

    statuses = asyncio.run(_with_db(scenario))
    # Первый стал superseded, второй — failed (актуальный для оператора).
    assert statuses == ["superseded", "failed"]


def test_create_invite_reuses_operator() -> None:
    async def scenario(session: AsyncSession) -> int:
        contact = Contact(ContactType.EMAIL, "a@b.com")
        await create_invite(
            session, 1, 777, BriefVariant.INDIVIDUAL, contact, BASE_URL, _ok_router()
        )
        contact2 = Contact(ContactType.EMAIL, "c@d.com")
        await create_invite(
            session, 1, 777, BriefVariant.INDIVIDUAL, contact2, BASE_URL, _ok_router()
        )
        await session.commit()
        from db.models import Operator

        ops = (await session.execute(select(Operator))).scalars().all()
        return len(ops)

    assert asyncio.run(_with_db(scenario)) == 1
