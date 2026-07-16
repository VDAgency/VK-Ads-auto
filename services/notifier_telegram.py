"""Транспорт по умолчанию для `services/notifier.py` — Telegram Bot API по HTTP.

Тонкий адаптер к методу `sendMessage` Telegram Bot API на httpx, без aiogram —
ядро остаётся headless (инвариант CLAUDE.md 1.3). Регистрируется в lifespan
ядра (`core/app.py`), потому что `POST /briefs` живёт в процессе `api`, а бот —
отдельный процесс: колбэк, зарегистрированный ботом, ядру недоступен.

Токен бота — секрет: в логи не пишем ни URL запроса, ни текст httpx-исключений
(и то и другое содержит токен) — только тип ошибки и chat_id.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx
from config.settings import Settings, get_settings

from services.notifier import OperatorSender, register_operator_notifier

logger = logging.getLogger(__name__)

# Событие редкое (приём брифа) — клиент создаётся на каждую отправку.
_TIMEOUT = 10.0


def build_operator_sender(bot_token: str, operator_ids: Iterable[int]) -> OperatorSender:
    """Собрать колбэк отправки текста каждому оператору через Bot API.

    Сбой по одному получателю не блокирует остальных; исключения наружу не
    пробрасываются (уведомление не должно ронять приём брифа).
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chat_ids = list(operator_ids)

    async def send(text: str) -> None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for chat_id in chat_ids:
                try:
                    response = await client.post(url, json={"chat_id": chat_id, "text": text})
                except Exception as exc:  # noqa: BLE001 — один сбой не блокирует остальных
                    # str(exc) у httpx-ошибок содержит URL с токеном — логируем
                    # только тип исключения и chat_id.
                    logger.warning("failed to notify operator %s: %s", chat_id, type(exc).__name__)
                    continue
                if not response.is_success:
                    logger.warning(
                        "failed to notify operator %s: HTTP %s", chat_id, response.status_code
                    )

    return send


def register_telegram_notifier(settings: Settings | None = None) -> None:
    """Зарегистрировать Telegram-транспорт уведомлений оператору в ядре.

    Без токена бота или списка операторов — no-op (тестовое/недонастроенное
    окружение): приём брифов работает, уведомления просто не отправляются.
    """
    settings = settings or get_settings()
    token = settings.bot_token.get_secret_value()
    if not token or not settings.operator_telegram_ids:
        logger.info("telegram notifier not configured; operator notifications disabled")
        return
    # sorted() — детерминированный порядок получателей (стабильность тестов).
    sender = build_operator_sender(token, sorted(settings.operator_telegram_ids))
    register_operator_notifier(sender)
