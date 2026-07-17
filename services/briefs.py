"""Приём брифа: валидация → идентификация клиента по 3 контактам → запись в БД.

Бизнес-логика приёма живёт здесь (headless-ядро), а не в роутере/боте. Источник
(`web` — наша форма, `bot` — пересылка из бота) на логику не влияет.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from config.settings import get_settings
from db.models import Brief, Client
from db.repositories import (
    create_client,
    find_brief_invite_by_token,
    find_client_by_contacts,
    get_client,
    mark_invite_received_if_sent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import BriefVariant, parse_brief
from services.notifier import notify_operator, notify_operator_brief_received
from services.referral import referral_notification, register_referral, resolve_ref_code


class InviteTokenError(Exception):
    """Токен инвайта не найден или инвайт неактивен.

    `code`: `not_found` (нет такого токена) | `inactive` (инвайт не в статусе,
    допускающем приём — например, уже received или superseded).
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def _maybe_register_referral(
    session: AsyncSession, account_id: int, ref_code: str, referred: Client
) -> tuple[str, str, int] | None:
    """Если реф-код валиден и реферер существует — зафиксировать реферал + скидку.

    Возвращает `(имя реферера, имя приведённого, %)` для уведомления оператору
    (шлётся после commit), либо None, если реферал не зарегистрирован.
    """
    referrer_id = resolve_ref_code(ref_code, get_settings().secret_key.get_secret_value())
    if referrer_id is None or referrer_id == referred.id:
        return None
    referrer = await get_client(session, account_id, referrer_id)
    if referrer is None:
        return None
    discount = await register_referral(
        session, account_id, referrer_id, referred.id, month=datetime.now().strftime("%Y-%m")
    )
    if discount is None:
        return None
    return (referrer.full_name or "клиент", referred.full_name or "клиент", discount.percent)


async def intake_brief(
    session: AsyncSession,
    account_id: int,
    variant: BriefVariant,
    payload: Mapping[str, str],
    source: str = "web",
    ref_code: str | None = None,
    token: str | None = None,
) -> Brief:
    """Принять бриф: разобрать, привязать/создать клиента, сохранить.

    Бросает `BriefValidationError`, если не хватает обязательных полей.
    Клиент ищется по совпадению любого из контактов (email/phone/telegram).
    Если клиент новый и пришёл по реф-коду — фиксируем реферал и скидку рефереру.

    Если передан `token` — форма пришла по нашему инвайту: находим инвайт, проверяем
    активность (бросаем `InviteTokenError` при not_found/inactive), связываем бриф с
    инвайтом, атомарно метим инвайт `received` и уведомляем оператора.
    """
    invite = None
    if token is not None:
        invite = await find_brief_invite_by_token(session, token)
        if invite is None:
            raise InviteTokenError("not_found")
        if invite.status not in ("sent", "failed"):
            # received (двойной сабмит) / superseded / pending (не доставлялся) — не принимаем.
            raise InviteTokenError("inactive")

    parsed = parse_brief(payload, variant)
    contact = parsed.contact

    client = await find_client_by_contacts(
        session, account_id, contact.email, contact.phone, contact.telegram
    )
    is_new_client = client is None
    if client is None:
        client = await create_client(
            session,
            account_id,
            full_name=parsed.full_name,
            email=contact.email,
            phone=contact.phone,
            telegram=contact.telegram,
        )

    referral_info: tuple[str, str, int] | None = None
    if ref_code and is_new_client:
        referral_info = await _maybe_register_referral(session, account_id, ref_code, client)

    brief = Brief(
        account_id=account_id,
        client_id=client.id,
        variant=variant.value,
        status="received",
        source=source,
        payload=dict(payload),
        invite_id=invite.id if invite is not None else None,
    )
    session.add(brief)

    if invite is not None:
        # Атомарный переход sent→received защищает от гонки двойного POST.
        # Имя клиента из брифа пишем в инвайт — для списка «Пришли за неделю».
        await mark_invite_received_if_sent(session, invite.id, contact_name=parsed.full_name)

    await session.commit()
    await session.refresh(brief)

    # Уведомления оператору — после commit (внешние side-effects, не в транзакции).
    # Шлём по ЛЮБОМУ брифу: и по инвайту, и самостоятельному по реф-ссылке (Фаза 11).
    await notify_operator_brief_received(
        client_name=parsed.full_name or contact.telegram or "клиент",
        variant=variant.value,
        contact_value=contact.email or contact.phone or contact.telegram,
    )
    # Реферал — отдельное уведомление «X привёл Y, скидка Z%» (Фаза 10).
    if referral_info is not None:
        referrer_name, referred_name, percent = referral_info
        await notify_operator(referral_notification(referrer_name, referred_name, percent))
    return brief
