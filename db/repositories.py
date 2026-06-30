"""Слой доступа к данным (репозитории). Каналы/роутеры ходят сюда, не пишут SQL сами."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Client


async def find_client_by_contacts(
    session: AsyncSession,
    account_id: int,
    email: str | None,
    phone: str | None,
    telegram: str | None,
) -> Client | None:
    """Найти клиента по совпадению ЛЮБОГО из 3 контактов (в рамках тенанта)."""
    conditions = []
    if email:
        conditions.append(Client.email == email)
    if phone:
        conditions.append(Client.phone == phone)
    if telegram:
        conditions.append(Client.telegram == telegram)
    if not conditions:
        return None
    stmt = select(Client).where(Client.account_id == account_id, or_(*conditions)).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_client(
    session: AsyncSession,
    account_id: int,
    full_name: str | None,
    email: str | None,
    phone: str | None,
    telegram: str | None,
    is_self: bool = False,
) -> Client:
    """Создать клиента и получить его id (flush без commit)."""
    client = Client(
        account_id=account_id,
        full_name=full_name,
        email=email,
        phone=phone,
        telegram=telegram,
        is_self=is_self,
    )
    session.add(client)
    await session.flush()
    return client
