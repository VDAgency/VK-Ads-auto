"""Фоновый поллер состояния сессий юзербота (spec §9: health-check раз в 60с).

Кеширует `{sender_id: authorized}` по данным `GET /health` userbot-сервиса.
Хендлер /send_brief показывает баннер, если сессия вызвавшего оператора не
авторизована. `None` = состояние неизвестно (сервис недоступен или ещё не
опрошен) — баннер не показываем, чтобы не пугать оператора зря.
"""

from __future__ import annotations

import asyncio
import logging

from bot import api_client
from bot.api_client import UserbotUnavailable

logger = logging.getLogger(__name__)

_status: dict[int, bool] = {}
_known = False


async def refresh_once() -> None:
    """Один опрос `/health`; при недоступности сервиса кеш помечается неизвестным."""
    global _known
    try:
        statuses = await api_client.userbot_health_all()
    except UserbotUnavailable:
        # Не притворяемся, что знаем состояние: is_authorized() вернёт None.
        _known = False
        _status.clear()
        logger.warning("userbot health poll failed: service unavailable")
        return
    _status.clear()
    _status.update(statuses)
    _known = True


async def poll_forever(interval: float = 60.0) -> None:
    """Бесконечный цикл опроса (фоновая задача в bot/main.py)."""
    while True:
        await refresh_once()
        await asyncio.sleep(interval)


def is_authorized(sender_id: int) -> bool | None:
    """Авторизована ли сессия оператора; `None` — состояние неизвестно."""
    if not _known:
        return None
    return _status.get(sender_id, False)


def reset() -> None:
    """Сбросить кеш (используется в тестах)."""
    global _known
    _known = False
    _status.clear()
