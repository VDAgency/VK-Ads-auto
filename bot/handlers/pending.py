"""Трекинг брифов: кто ещё не прислал и кто прислал недавно (команда №3).

Тонкий хендлер: данные берём через `api_client` (HTTP к ядру), здесь только рендер.
Строка списка: `N. Имя — @контакт — дата` (имя подтягивается из Telegram при
отправке; для email/phone и старых записей имени нет — показываем только контакт).
Дата — календарная относительно МСК: сегодня / вчера / ДД.ММ (в этом году) / ДД.ММ.ГГГГ.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.types import Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import CoreUnavailable, InviteItem
from bot.keyboards import BTN_PENDING

router = Router(name="pending")
router.message.filter(OperatorOnly())

# Операторы в РФ — считаем «сегодня/вчера» по МСК (UTC+3, без переходов на DST).
_MSK = timezone(timedelta(hours=3))


def _date_label(iso: str | None, now: datetime) -> str:
    """Календарная метка отправки/приёма: сегодня / вчера / ДД.ММ / ДД.ММ.ГГГГ."""
    if not iso:
        return ""
    moment = datetime.fromisoformat(iso).astimezone(_MSK).date()
    today = now.astimezone(_MSK).date()
    if moment == today:
        return "сегодня"
    if moment == today - timedelta(days=1):
        return "вчера"
    if moment.year == today.year:
        return moment.strftime("%d.%m")
    return moment.strftime("%d.%m.%Y")


def _who(item: InviteItem) -> str:
    """Читаемая часть строки: `Имя — @контакт` или просто контакт, если имени нет."""
    if item.contact_name:
        return f"{item.contact_name} — {item.contact}"
    return item.contact


def _fmt_line(idx: int, who: str, label: str) -> str:
    tail = f" — {label}" if label else ""
    return f"{idx}. {who}{tail}"


def _render(pending: list[InviteItem], recent: list[InviteItem], now: datetime) -> str:
    if not pending and not recent:
        return "Пока никого не ждём. Отправьте бриф — кнопка 📨 «Отправить бриф»."

    lines: list[str] = [f"📥 Ждём бриф ({len(pending)})"]
    lines += [
        _fmt_line(i, _who(item), _date_label(item.sent_at, now))
        for i, item in enumerate(pending, start=1)
    ] or ["— пусто"]
    lines.append("")
    lines.append(f"✅ Пришли за неделю ({len(recent)})")
    lines += [
        _fmt_line(i, _who(item), _date_label(item.received_at, now))
        for i, item in enumerate(recent, start=1)
    ] or ["— пусто"]
    return "\n".join(lines)


@router.message(or_f(Command("pending"), F.text == BTN_PENDING))
async def show_pending(message: Message) -> None:
    """Показать, кого ждём и кто прислал бриф за последнюю неделю."""
    try:
        pending = await api_client.get_pending()
        recent = await api_client.get_recent()
    except CoreUnavailable:
        await message.answer("Сервис временно недоступен, попробуйте позже.")
        return
    await message.answer(_render(pending, recent, datetime.now(_MSK)))
