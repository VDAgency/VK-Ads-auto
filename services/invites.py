"""Создание инвайта на бриф: токен → запись → доставка → статус (spec §7.1).

Ядро оркестрирует: генерирует токен, пишет `BriefInvite(pending)`, вызывает слой
доставки по типу контакта, по итогу метит `sent`/`failed`. При повторной ошибке —
supersede предыдущего `failed` (не копим мусор). Конкретика каналов — в
`services/delivery`; сюда инжектится готовый `DeliveryRouter`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol

from config.settings import Settings
from db.repositories import (
    create_brief_invite,
    find_last_failed_invite,
    mark_invite_failed,
    mark_invite_sent,
    mark_invite_superseded,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_invite import invite_text_with_token
from services.brief_parser import BriefVariant
from services.contact import Contact
from services.delivery import (
    DeliveryAdapter,
    DeliveryRouter,
    ManualDelivery,
    SmtpDelivery,
    TelegramUserbotDelivery,
)


class Router(Protocol):
    """Контракт роутера доставки — то, что нужно `create_invite`.

    `DeliveryRouter` ему удовлетворяет; в тестах инжектится стаб. Держим Protocol,
    чтобы ядро не зависело от конкретной реализации роутера.
    """

    def route(self, contact: Contact) -> DeliveryAdapter: ...


@dataclass(frozen=True)
class InviteResult:
    """Итог создания инвайта — то, что уходит в ответе API и в бот."""

    invite_id: int
    status: str  # sent | failed
    channel: str  # telegram | email | manual
    fallback_text: str | None = None
    error: str | None = None


def build_delivery_router(settings: Settings) -> DeliveryRouter:
    """Собрать `DeliveryRouter` из настроек (адаптеры трёх каналов)."""
    return DeliveryRouter(
        telegram=TelegramUserbotDelivery(settings.userbot_base_url),
        email=SmtpDelivery(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password.get_secret_value(),
            from_name=settings.smtp_from_name,
        ),
        manual=ManualDelivery(),
    )


async def create_invite(
    session: AsyncSession,
    *,
    account_id: int,
    operator_id: int,
    variant: str,
    contact: Contact,
    router: Router,
    base_url: str,
) -> InviteResult:
    """Создать инвайт, отправить ссылку клиенту, вернуть итог (spec §7.1)."""
    token = secrets.token_urlsafe(16)
    invite = await create_brief_invite(
        session,
        account_id=account_id,
        operator_id=operator_id,
        token=token,
        variant=variant,
        contact_type=contact.type.value,
        contact_value=contact.value,
        channel=_channel_for(contact),
    )

    invite_text = invite_text_with_token(BriefVariant(variant), token, base_url)
    result = await router.route(contact).send(contact, invite_text)
    channel = result.channel.value

    if result.ok:
        await mark_invite_sent(session, invite.id)
        status = "sent"
    else:
        # Ищем прежний failed ДО пометки текущего, иначе find вернёт текущий.
        prev = await find_last_failed_invite(session, account_id, operator_id, contact.value)
        await mark_invite_failed(session, invite.id, result.error or "unknown")
        if prev is not None:
            await mark_invite_superseded(session, prev.id)
        status = "failed"

    await session.commit()
    return InviteResult(
        invite_id=invite.id,
        status=status,
        channel=channel,
        fallback_text=result.fallback_text,
        error=result.error,
    )


def _channel_for(contact: Contact) -> str:
    """Канал доставки по типу контакта (детерминированно, как в роутере)."""
    from services.contact import ContactType

    return {
        ContactType.TELEGRAM: "telegram",
        ContactType.EMAIL: "email",
        ContactType.PHONE: "manual",
    }[contact.type]
