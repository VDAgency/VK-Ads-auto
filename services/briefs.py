"""Приём брифа: валидация → идентификация клиента по 3 контактам → запись в БД.

Бизнес-логика приёма живёт здесь (headless-ядро), а не в роутере/боте. Источник
(`web` — наша форма, `bot` — пересылка из бота) на логику не влияет.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from config.settings import get_settings
from db.models import Brief
from db.repositories import create_client, find_client_by_contacts, get_client
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import BriefVariant, parse_brief
from services.referral import register_referral, resolve_ref_code


async def _maybe_register_referral(
    session: AsyncSession, account_id: int, ref_code: str, referred_client_id: int
) -> None:
    """Если реф-код валиден и реферер существует — зафиксировать реферал + скидку."""
    referrer_id = resolve_ref_code(ref_code, get_settings().secret_key.get_secret_value())
    if referrer_id is None or referrer_id == referred_client_id:
        return
    if await get_client(session, account_id, referrer_id) is None:
        return
    await register_referral(
        session, account_id, referrer_id, referred_client_id, month=datetime.now().strftime("%Y-%m")
    )


async def intake_brief(
    session: AsyncSession,
    account_id: int,
    variant: BriefVariant,
    payload: Mapping[str, str],
    source: str = "web",
    ref_code: str | None = None,
) -> Brief:
    """Принять бриф: разобрать, привязать/создать клиента, сохранить.

    Бросает `BriefValidationError`, если не хватает обязательных полей.
    Клиент ищется по совпадению любого из контактов (email/phone/telegram).
    Если клиент новый и пришёл по реф-коду — фиксируем реферал и скидку рефереру.
    """
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

    if ref_code and is_new_client:
        await _maybe_register_referral(session, account_id, ref_code, client.id)

    brief = Brief(
        account_id=account_id,
        client_id=client.id,
        variant=variant.value,
        status="received",
        source=source,
        payload=dict(payload),
    )
    session.add(brief)
    await session.commit()
    await session.refresh(brief)
    return brief
