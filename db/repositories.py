"""Слой доступа к данным (репозитории). Каналы/роутеры ходят сюда, не пишут SQL сами."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Brief,
    BriefInvite,
    Campaign,
    Client,
    Creative,
    Discount,
    Operator,
    Referral,
    Stat,
)


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


async def get_brief(session: AsyncSession, account_id: int, brief_id: int) -> Brief | None:
    """Получить бриф тенанта по id (для операторской карточки)."""
    stmt = select(Brief).where(Brief.account_id == account_id, Brief.id == brief_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_clients(session: AsyncSession, account_id: int) -> list[Client]:
    """Все клиенты тенанта (свежие первыми) — для админ-панели."""
    stmt = select(Client).where(Client.account_id == account_id).order_by(Client.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def count_briefs_by_client(session: AsyncSession, account_id: int) -> dict[int, int]:
    """Число брифов по каждому клиенту (`client_id → count`) — для списка клиентов."""
    stmt = (
        select(Brief.client_id, func.count())
        .where(Brief.account_id == account_id, Brief.client_id.is_not(None))
        .group_by(Brief.client_id)
    )
    return {cid: cnt for cid, cnt in (await session.execute(stmt)).all() if cid is not None}


async def list_campaigns(session: AsyncSession, account_id: int) -> list[Campaign]:
    """Все кампании тенанта (свежие первыми) — для админ-панели."""
    stmt = select(Campaign).where(Campaign.account_id == account_id).order_by(Campaign.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def count_campaigns(session: AsyncSession, account_id: int) -> int:
    """Число кампаний тенанта — для дашборда админки."""
    stmt = select(func.count()).select_from(Campaign).where(Campaign.account_id == account_id)
    return int((await session.execute(stmt)).scalar_one())


async def get_creative_for_brief(
    session: AsyncSession, account_id: int, brief_id: int
) -> Creative | None:
    """Последний загруженный креатив брифа (для статуса карточки)."""
    stmt = (
        select(Creative)
        .where(Creative.account_id == account_id, Creative.brief_id == brief_id)
        .order_by(Creative.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_latest_campaign_for_brief(
    session: AsyncSession, account_id: int, brief_id: int
) -> Campaign | None:
    """Последняя кампания, созданная по брифу (для статуса карточки)."""
    stmt = (
        select(Campaign)
        .where(Campaign.account_id == account_id, Campaign.brief_id == brief_id)
        .order_by(Campaign.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_brief_ids_for_invites(
    session: AsyncSession, account_id: int, invite_ids: Sequence[int]
) -> dict[int, int]:
    """Отображение `invite_id → brief_id` для присланных брифов (для кликабельного списка)."""
    if not invite_ids:
        return {}
    stmt = select(Brief.invite_id, Brief.id).where(
        Brief.account_id == account_id, Brief.invite_id.in_(invite_ids)
    )
    return {
        invite_id: brief_id
        for invite_id, brief_id in (await session.execute(stmt)).all()
        if invite_id is not None
    }


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


async def find_client_by_email(session: AsyncSession, account_id: int, email: str) -> Client | None:
    """Найти клиента тенанта по email (для входа в кабинет по email+паролю)."""
    stmt = select(Client).where(Client.account_id == account_id, Client.email == email).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_client_password(
    session: AsyncSession, account_id: int, client_id: int, password_hash: str
) -> Client | None:
    """Установить хеш пароля клиента и отметить время установки. `None` — нет клиента."""
    client = await get_client(session, account_id, client_id)
    if client is None:
        return None
    client.password_hash = password_hash
    client.password_set_at = datetime.now(UTC)
    await session.flush()
    return client


async def get_or_create_operator(
    session: AsyncSession,
    account_id: int,
    telegram_id: int,
    full_name: str | None = None,
) -> Operator:
    """Найти оператора по telegram_id или создать. Нужен для FK инвайтов/задач.

    Сидинга операторов нет: оператор материализуется при первом обращении из бота.
    Скоуп тенанта в текущем этапе единый (см. briefs.py), telegram_id уникален глобально.
    """
    stmt = select(Operator).where(Operator.telegram_id == telegram_id)
    operator = (await session.execute(stmt)).scalar_one_or_none()
    if operator is not None:
        return operator
    operator = Operator(account_id=account_id, telegram_id=telegram_id, full_name=full_name)
    session.add(operator)
    await session.flush()
    return operator


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


# ============================================================================
# BriefInvite (см. spec docs/superpowers/specs/2026-07-13-brief-userbot-delivery-design.md)
# ============================================================================


async def create_brief_invite(
    session: AsyncSession,
    account_id: int,
    operator_id: int,
    token: str,
    variant: str,
    contact_type: str,
    contact_value: str,
    channel: str,
    client_id: int | None = None,
) -> BriefInvite:
    """Создать инвайт в статусе pending. Токен должен быть уже сгенерирован сервисом."""
    invite = BriefInvite(
        account_id=account_id,
        operator_id=operator_id,
        token=token,
        variant=variant,
        contact_type=contact_type,
        contact_value=contact_value,
        channel=channel,
        client_id=client_id,
        status="pending",
    )
    session.add(invite)
    await session.flush()
    return invite


async def find_brief_invite_by_token(
    session: AsyncSession,
    token: str,
) -> BriefInvite | None:
    """Найти инвайт по токену. Скоуп тенанта не нужен — токен уникален глобально."""
    stmt = select(BriefInvite).where(BriefInvite.token == token)
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_last_failed_invite(
    session: AsyncSession,
    account_id: int,
    operator_id: int,
    contact_value: str,
) -> BriefInvite | None:
    """Найти самый свежий failed-инвайт того же оператора по тому же контакту.

    Нужен для supersede-логики: при повторной ошибочной отправке предыдущий failed
    получит статус `superseded`, чтобы не копить мусор.
    """
    stmt = (
        select(BriefInvite)
        .where(
            BriefInvite.account_id == account_id,
            BriefInvite.operator_id == operator_id,
            BriefInvite.contact_value == contact_value,
            BriefInvite.status == "failed",
        )
        .order_by(BriefInvite.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def mark_invite_sent(
    session: AsyncSession, invite_id: int, contact_name: str | None = None
) -> None:
    """Пометить инвайт доставленным (channel: telegram/email/manual).

    `contact_name` — имя получателя, добытое каналом при отправке (Telegram);
    None для email/phone.
    """
    await session.execute(
        update(BriefInvite)
        .where(BriefInvite.id == invite_id)
        .values(
            status="sent",
            delivered_at=datetime.now(UTC),
            error=None,
            contact_name=contact_name,
        )
    )


async def mark_invite_failed(
    session: AsyncSession,
    invite_id: int,
    error: str,
) -> None:
    """Пометить инвайт failed с кодом ошибки (напр. username_not_occupied)."""
    await session.execute(
        update(BriefInvite).where(BriefInvite.id == invite_id).values(status="failed", error=error)
    )


async def mark_invite_superseded(session: AsyncSession, invite_id: int) -> None:
    """Пометить старый failed-инвайт заменённым — не показываем оператору дальше."""
    await session.execute(
        update(BriefInvite).where(BriefInvite.id == invite_id).values(status="superseded")
    )


async def count_clients(session: AsyncSession, account_id: int) -> int:
    """Сколько клиентов у тенанта — вход мок-гейта (§7)."""
    stmt = select(func.count()).select_from(Client).where(Client.account_id == account_id)
    return int((await session.execute(stmt)).scalar_one())


async def list_stat_campaign_ids(session: AsyncSession, account_id: int) -> list[str]:
    """Уникальные `campaign_id`, по которым есть срезы `Stat` (реальные кабинеты)."""
    stmt = (
        select(Stat.campaign_id)
        .where(Stat.account_id == account_id)
        .group_by(Stat.campaign_id)
        .order_by(Stat.campaign_id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def aggregate_cabinet_stats(
    session: AsyncSession,
    account_id: int,
    campaign_id: str,
    *,
    since: datetime | None = None,
) -> dict[str, float]:
    """Суммарные метрики кабинета (кампании) с момента `since` (или за всё время).

    Возвращает нули, если срезов нет — вызывающий решает, показывать ли мок.
    """
    conditions = [Stat.account_id == account_id, Stat.campaign_id == campaign_id]
    if since is not None:
        conditions.append(Stat.captured_at >= since)
    stmt = select(
        func.coalesce(func.sum(Stat.shows), 0.0),
        func.coalesce(func.sum(Stat.clicks), 0.0),
        func.coalesce(func.sum(Stat.spent), 0.0),
        func.coalesce(func.sum(Stat.results), 0.0),
    ).where(*conditions)
    row = (await session.execute(stmt)).one()
    return {
        "shows": float(row[0]),
        "clicks": float(row[1]),
        "spent": float(row[2]),
        "results": float(row[3]),
    }


async def list_pending_invites(
    session: AsyncSession,
    account_id: int,
) -> list[BriefInvite]:
    """Инвайты, доставленные клиенту, но ещё не вернувшиеся брифом (`sent`).

    Сортировка: самые старые сверху — их дольше всех ждём.
    """
    stmt = (
        select(BriefInvite)
        .where(BriefInvite.account_id == account_id, BriefInvite.status == "sent")
        .order_by(BriefInvite.delivered_at.asc().nullslast(), BriefInvite.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_recent_received_invites(
    session: AsyncSession,
    account_id: int,
    since: datetime,
) -> list[BriefInvite]:
    """Инвайты, по которым бриф пришёл начиная с момента `since` (свежие сверху)."""
    stmt = (
        select(BriefInvite)
        .where(
            BriefInvite.account_id == account_id,
            BriefInvite.status == "received",
            BriefInvite.received_at >= since,
        )
        .order_by(BriefInvite.received_at.desc(), BriefInvite.id.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def mark_invite_received_if_sent(
    session: AsyncSession, invite_id: int, contact_name: str | None = None
) -> bool:
    """Атомарный переход `sent → received` (защита от двойного POST /briefs).

    `contact_name` — имя клиента из присланного брифа (`full_name`): при приёме
    записываем его в инвайт, чтобы список «Пришли за неделю» показывал имя.

    Возвращает True, если ровно эта строка обновилась. False — если инвайт был
    в другом статусе (уже received, failed, superseded) → вызов клиента вернёт 409.
    """
    stmt = (
        update(BriefInvite)
        .where(BriefInvite.id == invite_id, BriefInvite.status == "sent")
        .values(status="received", received_at=datetime.now(UTC))
    )
    if contact_name:
        stmt = stmt.values(contact_name=contact_name)
    # cast: session.execute() возвращает Result[Any], но для DML это CursorResult
    # с полем rowcount — известное ограничение типизации SQLAlchemy async.
    result = cast(CursorResult[tuple[int, ...]], await session.execute(stmt))
    return bool(result.rowcount)
