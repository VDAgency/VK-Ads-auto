"""Уведомления оператору из ядра — без прямой зависимости от Telegram.

Ядро не импортирует aiogram (headless-инвариант). Канал доставки регистрирует
колбэк `register_operator_notifier(send_message)`; ядро зовёт его через
`notify_operator_brief_received`. Если колбэк не зарегистрирован (например, бот
не поднят) — тихо логируем и выходим, приём брифа от этого не падает.

ПДн клиента (контакт, имя) в логах маскируются (CLAUDE.md 1.1).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Колбэк отправки сообщения оператору. Тип: (text) -> awaitable. Регистрируется
# каналом (bot/notifier.py) при старте, чтобы ядро не импортировало aiogram.
OperatorSender = Callable[[str], Awaitable[None]]

_sender: OperatorSender | None = None


def register_operator_notifier(sender: OperatorSender) -> None:
    """Зарегистрировать колбэк отправки сообщений оператору (зовёт канал при старте)."""
    global _sender
    _sender = sender


def reset_operator_notifier() -> None:
    """Сбросить колбэк (используется в тестах)."""
    global _sender
    _sender = None


async def notify_operator(text: str) -> None:
    """Отправить оператору произвольное сообщение (напр. о реферале). No-op без колбэка."""
    if _sender is None:
        logger.info("operator notifier not registered; skip notice")
        return
    try:
        await _sender(text)
    except Exception:  # noqa: BLE001 — уведомление не должно ронять вызывающую операцию
        logger.exception("failed to send operator notice")


def mask_pii(value: str) -> str:
    """Замаскировать ПДн для логов: оставить края, середину скрыть.

    `ivan@mail.ru` → `iv***@mail.ru`-подобно; короткие значения → `***`.
    Точная форма не важна — важно не писать контакт целиком в лог.
    """
    if "@" in value:
        local, _, domain = value.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return f"{head}***@{domain}"
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


async def notify_operator_brief_received(
    *,
    client_name: str,
    variant: str,
    contact_value: str | None = None,
) -> None:
    """Сообщить оператору, что клиент прислал бриф.

    Формат по §8.3 спеки. Контакт в логах маскируется; в самом сообщении оператору
    имя клиента показываем (это его рабочая информация), но в лог пишем маску.
    """
    variant_label = "физлицо" if variant == "individual" else "сообщество"
    text = f"📥 Клиент «{client_name}» прислал бриф ({variant_label}). Можно раскладывать кампанию."

    if _sender is None:
        logger.info(
            "operator notifier not registered; skip brief-received notice for %s",
            mask_pii(contact_value or client_name),
        )
        return

    try:
        await _sender(text)
    except Exception:  # noqa: BLE001 — уведомление не должно ронять приём брифа
        logger.exception(
            "failed to notify operator about brief from %s",
            mask_pii(contact_value or client_name),
        )
