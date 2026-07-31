"""Фоновая проверка сессий: держит состояние свежим и соединение живым.

Зачем это нужно, если Telethon сам пингует соединение раз в минуту. Его пинг
подтверждает, что жив сокет; наша проверка подтверждает, что жива вся цепочка —
включая авторизацию — и наполняет состояние, которое читают `/health` и `/sessions`.
Без неё readiness был бы пустым до первой отправки брифа.

Интервал 300 секунд выбран осознанно: сессия Telegram сама по себе не протухает
(сервер завершает только неактивные месяцами), поэтому чаще смысла нет, а реже —
поломка обнаружится слишком поздно. Недоступные сессии проверяются с нарастающим
откатом, терминальные (`expired`, `banned`) — не проверяются вовсе: ретрай там
бесполезен, а с отозванным ключом ещё и вреден.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from userbot.state import TERMINAL_STATES, SessionState
from userbot.telethon_client import UserbotClient

logger = logging.getLogger(__name__)


async def keepalive_once(
    client: UserbotClient, *, clock: Callable[[], float] = time.time
) -> list[int]:
    """Один прогон по всем известным сессиям. Возвращает проверенные sender_id.

    Сбой одной сессии не должен мешать остальным, поэтому каждая — в своём
    перехвате: иначе один битый оператор остановил бы обновление состояния для всех.
    """
    checked: list[int] = []
    now = clock()
    for sender_id in client.known_senders():
        info = client.states.get(sender_id)
        if info.state in TERMINAL_STATES:
            continue
        if not client.states.due(sender_id, now):
            continue
        try:
            await client.probe(sender_id)
        except Exception:  # noqa: BLE001 — падение проверки не должно валить цикл
            logger.exception("keepalive: проверка сессии %s сорвалась", sender_id)
            continue
        checked.append(sender_id)
    return checked


async def keepalive_loop(client: UserbotClient, interval: float = 300.0) -> None:
    """Бесконечный цикл проверок. Первый прогон — сразу, чтобы состояние не пустовало."""
    while True:
        try:
            await keepalive_once(client)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл обязан пережить любую ошибку
            logger.exception("keepalive: непредвиденный сбой прогона")
        await asyncio.sleep(interval)


def start(client: UserbotClient, interval: float = 300.0) -> asyncio.Task[None]:
    """Запустить фоновую задачу проверок."""
    return asyncio.create_task(keepalive_loop(client, interval), name="userbot-keepalive")


async def stop(task: asyncio.Task[None]) -> None:
    """Остановить фоновую задачу и дождаться её завершения."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


__all__ = ["SessionState", "keepalive_loop", "keepalive_once", "start", "stop"]
