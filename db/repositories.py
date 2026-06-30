"""Слой доступа к данным (репозитории). Каналы/роутеры ходят сюда, не пишут SQL сами."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Brief, Client, Discount, Referral, Stat


async def create_referral(
    session: AsyncSession,
    account_id: int,
    referrer_client_id: int,
    referred_client_id: int,
) -> Referral:
    """Записать факт «реферер привёл клиента»."""
    referral = Referral(
        account_id=account_id,
        referrer_client_id=referrer_client_id,
        referred_client_id=referred_client_id,
    )
    session.add(referral)
    await session.flush()
    return referral


async def create_discount(
    session: AsyncSession,
    account_id: int,
    client_id: int,
    percent: int,
    month: str,
) -> Discount:
    """Начислить скидку клиенту на месяц."""
    discount = Discount(
        account_id=account_id,
        client_id=client_id,
        percent=percent,
        month=month,
        status="pending",
    )
    session.add(discount)
    await session.flush()
    return discount


async def get_client(session: AsyncSession, account_id: int, client_id: int) -> Client | None:
    """Получить клиента тенанта по id."""
    stmt = select(Client).where(Client.account_id == account_id, Client.id == client_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_client_briefs(session: AsyncSession, account_id: int, client_id: int) -> list[Brief]:
    """Брифы клиента (свежие первыми)."""
    stmt = (
        select(Brief)
        .where(Brief.account_id == account_id, Brief.client_id == client_id)
        .order_by(Brief.id.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def save_stat(
    session: AsyncSession,
    account_id: int,
    campaign_id: str,
    shows: float,
    clicks: float,
    spent: float,
    results: float,
) -> Stat:
    """Сохранить срез метрик кампании."""
    stat = Stat(
        account_id=account_id,
        campaign_id=campaign_id,
        shows=shows,
        clicks=clicks,
        spent=spent,
        results=results,
    )
    session.add(stat)
    await session.flush()
    return stat


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
