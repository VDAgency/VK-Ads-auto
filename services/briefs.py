"""Приём брифа: валидация → идентификация клиента по 3 контактам → запись в БД.

Бизнес-логика приёма живёт здесь (headless-ядро), а не в роутере/боте. Источник
(`web` — наша форма, `bot` — пересылка из бота) на логику не влияет.
"""

from __future__ import annotations

from collections.abc import Mapping

from db.models import Brief
from db.repositories import create_client, find_client_by_contacts
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import BriefVariant, parse_brief


async def intake_brief(
    session: AsyncSession,
    account_id: int,
    variant: BriefVariant,
    payload: Mapping[str, str],
    source: str = "web",
) -> Brief:
    """Принять бриф: разобрать, привязать/создать клиента, сохранить.

    Бросает `BriefValidationError`, если не хватает обязательных полей.
    Клиент ищется по совпадению любого из контактов (email/phone/telegram).
    """
    parsed = parse_brief(payload, variant)
    contact = parsed.contact

    client = await find_client_by_contacts(
        session, account_id, contact.email, contact.phone, contact.telegram
    )
    if client is None:
        client = await create_client(
            session,
            account_id,
            full_name=parsed.full_name,
            email=contact.email,
            phone=contact.phone,
            telegram=contact.telegram,
        )

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
