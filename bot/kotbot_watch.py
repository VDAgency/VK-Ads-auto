"""Фоновый поллер здоровья kotbot-сервиса (spec 2026-07-17 §5: раз в 60с).

Кеширует агрегированный `healthy` из `GET /health`. РОВНО на переходе
healthy(True) → unhealthy(False) шлёт всем операторам уведомление «пройдите
/link_kotbot заново». Недоступность самого сервиса — состояние неизвестно
(`None`): уведомление НЕ шлём, потому что правды не знаем.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from config.settings import get_settings

from bot import api_client
from bot.api_client import KotbotUnavailable

logger = logging.getLogger(__name__)

REAUTH_MESSAGE = "🔐 kotbot требует повторной авторизации — выполните /link_kotbot"

# Кеш агрегата: True/False — знаем состояние, None — неизвестно (недоступен/не опрошен).
_healthy: bool | None = None


async def refresh_once() -> None:
    """Один опрос `/health`; при недоступности сервиса кеш помечается неизвестным."""
    global _healthy
    try:
        health = await api_client.kotbot_status()
    except KotbotUnavailable:
        # Не притворяемся, что знаем состояние: is_healthy() вернёт None.
        _healthy = None
        logger.warning("kotbot health poll failed: service unavailable")
        return
    _healthy = health.healthy


def is_healthy() -> bool | None:
    """Здоров ли kotbot-канал; `None` — состояние неизвестно."""
    return _healthy


def reset() -> None:
    """Сбросить кеш (используется в тестах)."""
    global _healthy
    _healthy = None


async def _notify_operators(bot: Bot) -> None:
    """Разослать уведомление всем операторам; сбой одному — не валит остальных."""
    for operator_id in sorted(get_settings().operator_telegram_ids):
        try:
            await bot.send_message(operator_id, REAUTH_MESSAGE)
        except Exception:  # noqa: BLE001 — недоставка одному оператору не блокер поллера
            logger.warning("kotbot_watch: failed to notify operator %s", operator_id)


async def check_once(bot: Bot) -> None:
    """Один цикл поллера: опрос + уведомление РОВНО на переходе True→False."""
    previous = _healthy
    await refresh_once()
    if previous is True and _healthy is False:
        logger.info("kotbot became unhealthy: notifying operators")
        await _notify_operators(bot)


async def poll_forever(bot: Bot, interval: float = 60.0) -> None:
    """Бесконечный цикл опроса (фоновая задача в bot/main.py)."""
    while True:
        await check_once(bot)
        await asyncio.sleep(interval)
