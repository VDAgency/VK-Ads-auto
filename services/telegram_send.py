"""Отправка сообщения оператору через Telegram Bot API (без aiogram).

Ядро и бот — разные процессы, поэтому уведомление о приходе брифа ядро шлёт
само, HTTP-вызовом `sendMessage` через httpx. Инвариант « core не импортирует
aiogram» сохраняется. Пустой токен = бот не сконфигурирован: отправка тихо
пропускается (в dev/тестах уведомление не нужно).
"""

from __future__ import annotations

import logging

import httpx

from services.notifier import SendMessage

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


def build_telegram_sender(bot_token: str) -> SendMessage:
    """Собрать колбэк отправки сообщения оператору через Telegram Bot API."""

    async def send(chat_id: int, text: str) -> None:
        if not bot_token:
            logger.warning("BOT_TOKEN не задан — уведомление оператору пропущено")
            return
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json={"chat_id": chat_id, "text": text})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            # Уведомление — best-effort: приём брифа не должен падать из-за Telegram.
            logger.error("не удалось уведомить оператора %s: %s", chat_id, exc)

    return send
