"""Создание инвайта на бриф: токен → запись → доставка → результат оператору.

Оркестрация уровня ядра (spec 2026-07-13 §6): генерируем уникальный токен, пишем
инвайт в `pending`, зовём канал доставки, по итогу метим `sent`/`failed`. При
неудаче — supersede предыдущего `failed`-инвайта того же контакта, чтобы не копить
мусор. Бот (тонкий клиент) получает `InviteResult` и рендерит один из сценариев §8.1.

Бизнес-логики в боте/роутере нет — только здесь.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from db.repositories import (
    create_brief_invite,
    find_last_failed_invite,
    get_or_create_operator,
    mark_invite_failed,
    mark_invite_sent,
    mark_invite_superseded,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_invite import invite_text_with_token
from services.brief_parser import BriefVariant
from services.contact import Contact, ContactType
from services.delivery.base import DeliveryChannel, DeliveryResult
from services.delivery.router import DeliveryRouter

_TOKEN_BYTES = 16  # secrets.token_urlsafe(16) → 22-символьный токен (влезает в String(32))


@dataclass(frozen=True)
class InviteResult:
    """Итог создания инвайта для рендера оператору.

    - `status`: `sent` (канал ok) | `failed` (канал сорвался, есть fallback_text).
    - `channel`: telegram | email | manual.
    - `fallback_text`: текст для ручной пересылки (всегда для manual и при ошибке).
    - `error`: короткий код ошибки канала (только при `failed`).
    """

    invite_id: int
    token: str
    status: str
    channel: str
    fallback_text: str | None
    error: str | None


async def create_invite(
    session: AsyncSession,
    account_id: int,
    operator_telegram_id: int,
    variant: BriefVariant,
    contact: Contact,
    base_url: str,
    router: DeliveryRouter,
) -> InviteResult:
    """Создать инвайт и попытаться доставить ссылку на бриф.

    Порядок: оператор → токен → запись `pending` → доставка → пометка sent/failed.
    При `ok=False` предыдущий failed того же контакта помечается `superseded`.
    Коммит делает вызывающая сторона (роутер/сервис-обёртка) — здесь только flush
    через репозитории, чтобы транзакция была атомарной на уровне запроса.
    """
    operator = await get_or_create_operator(session, account_id, operator_telegram_id)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    invite_text = invite_text_with_token(variant, token, base_url)

    invite = await create_brief_invite(
        session,
        account_id=account_id,
        operator_id=operator.id,
        token=token,
        variant=variant.value,
        contact_type=contact.type.value,
        contact_value=contact.value,
        channel=_channel_for(contact).value,
    )

    result: DeliveryResult = await router.route(contact).send(contact, invite_text)

    if result.ok:
        await mark_invite_sent(session, invite.id, contact_name=result.recipient_name)
        status = "sent"
    else:
        # supersede предыдущего failed того же контакта — до пометки текущего failed,
        # чтобы не задеть только что созданную строку.
        previous = await find_last_failed_invite(session, account_id, operator.id, contact.value)
        if previous is not None and previous.id != invite.id:
            await mark_invite_superseded(session, previous.id)
        await mark_invite_failed(session, invite.id, result.error or "unknown")
        status = "failed"

    return InviteResult(
        invite_id=invite.id,
        token=token,
        status=status,
        channel=result.channel.value,
        fallback_text=result.fallback_text,
        error=result.error,
    )


def _channel_for(contact: Contact) -> DeliveryChannel:
    """Канал доставки по типу контакта (для записи в инвайт до отправки).

    Детерминированно совпадает с выбором `DeliveryRouter` (email→SMTP, telegram→
    userbot, phone→manual).
    """
    return {
        ContactType.TELEGRAM: DeliveryChannel.TELEGRAM,
        ContactType.EMAIL: DeliveryChannel.EMAIL,
        ContactType.PHONE: DeliveryChannel.MANUAL,
    }[contact.type]
