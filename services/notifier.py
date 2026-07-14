"""Уведомление оператора о приходе брифа (spec 2026-07-13 §8.3).

Ядро не импортирует aiogram: реальную отправку выполняет колбэк `send_message`
(его задаёт бот при инициализации, обёртка над `Bot.send_message`). Здесь —
только сборка текста по §8.3 и маскирование PII перед записью в лог.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

# Колбэк отправки сообщения оператору: (telegram_id, text) -> None.
SendMessage = Callable[[int, str], Awaitable[None]]

_VARIANT_RU = {
    "individual": "Физлицо",
    "community": "Сообщество (ИП/бизнес)",
}


def _variant_ru(variant: str) -> str:
    return _VARIANT_RU.get(variant, variant)


def format_brief_received(
    *,
    contact: str,
    sent_at: datetime | None,
    channel: str,
    variant: str,
    brief_id: int,
) -> str:
    """Собрать текст уведомления оператору по §8.3."""
    when = sent_at.strftime("%d.%m %H:%M") if sent_at is not None else "—"
    return (
        "🎉 Пришёл бриф!\n"
        f"Клиент: {contact}\n"
        f"Отправлен: {when} через {channel}\n"
        f"Вариант: {_variant_ru(variant)}\n"
        f"/view_brief_{brief_id}"
    )


def mask_contact(contact: str) -> str:
    """Замаскировать контакт для лога (не светим полные PII).

    email → первые 2 символа локальной части + домен; короткая часть → `***`.
    телефон → префикс `+7` (если есть) + звёзды + последние 2 цифры.
    telegram → `@` + первые 2 символа + `***`.
    """
    if "@" in contact and "." in contact.split("@", 1)[1]:
        local, domain = contact.split("@", 1)
        head = local[:2] if len(local) > 2 else ""
        prefix = head if head else "***"
        return f"{prefix}***@{domain}" if head else f"***@{domain}"
    if contact.startswith("@"):
        body = contact[1:]
        return "@" + (body[:2] + "***" if body else "***")
    digits = [c for c in contact if c.isdigit()]
    if len(digits) >= 4:
        keep = contact[:2] if contact.startswith("+") else ""
        last2 = "".join(digits[-2:])
        stars = "*" * (len(digits) - (1 if keep else 0) - 2)
        return f"{keep}{stars}{last2}"
    return "***"


async def notify_operator_brief_received(
    send_message: SendMessage,
    *,
    operator_telegram_id: int,
    contact: str,
    sent_at: datetime | None,
    channel: str,
    variant: str,
    brief_id: int,
) -> None:
    """Отправить оператору уведомление о приходе брифа.

    Оператору уходит полный контакт (ему он нужен для работы); в лог пишем только
    маскированный вариант.
    """
    text = format_brief_received(
        contact=contact,
        sent_at=sent_at,
        channel=channel,
        variant=variant,
        brief_id=brief_id,
    )
    await send_message(operator_telegram_id, text)
    logger.info(
        "brief received notification sent: operator=%s contact=%s brief_id=%s",
        operator_telegram_id,
        mask_contact(contact),
        brief_id,
    )
