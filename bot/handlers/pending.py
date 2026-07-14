"""Трекинг брифов: кто ещё не прислал и кто прислал недавно (команда №3).

Тонкий хендлер: данные берём через `api_client` (HTTP к ядру), здесь только рендер.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.types import Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import CoreUnavailable, InviteItem
from bot.keyboards import BTN_PENDING

router = Router(name="pending")
router.message.filter(OperatorOnly())

_VARIANT_HINT = {"individual": "физлицо", "community": "ИП/бизнес"}
_CHANNEL_HINT = {"telegram": "telegram", "email": "email", "manual": "телефон"}


def _waiting_phrase(days: int) -> str:
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "ждём 1 день"
    if 2 <= days <= 4:
        return f"ждём {days} дня"
    return f"ждём {days} дней"


def _received_phrase(days: int) -> str:
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


def _fmt_pending(item: InviteItem) -> str:
    channel = _CHANNEL_HINT.get(item.channel, item.channel)
    return f"• {item.contact}, {channel} — {_waiting_phrase(item.waiting_days)}"


def _fmt_recent(item: InviteItem) -> str:
    channel = _CHANNEL_HINT.get(item.channel, item.channel)
    return f"• {item.contact}, {channel}"


def _render(pending: list[InviteItem], recent: list[InviteItem]) -> str:
    if not pending and not recent:
        return "Пока никого не ждём. Отправьте бриф — кнопка 📨 «Отправить бриф»."

    lines: list[str] = [f"📥 Ждём бриф ({len(pending)})"]
    lines += [_fmt_pending(i) for i in pending] or ["— пусто"]
    lines.append("")
    lines.append(f"✅ Пришли за неделю ({len(recent)})")
    lines += [_fmt_recent(i) for i in recent] or ["— пусто"]
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
    await message.answer(_render(pending, recent))
