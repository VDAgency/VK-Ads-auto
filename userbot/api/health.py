"""Liveness и состояние сессий (spec 2026-07-31 §4.7).

`GET /live` — только «процесс жив»: ноль обращений к сети и диску. Именно его
опрашивает docker-healthcheck. Раньше эту роль играл `/health`, который подключался
к Telegram по каждой сессии: один опрос занимал десятки секунд, контейнер вечно висел
`unhealthy`, а поллер бота считал упавшим весь сервис.

`GET /health` теперь отдаёт состояние сессий ИЗ ПАМЯТИ — его наполняет фоновая
проверка (`userbot/keepalive.py`). Форсированная проверка по сети — `/sessions/{id}/probe`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from userbot.api import get_client
from userbot.telethon_client import UserbotClient

router = APIRouter(tags=["health"])

Client = Annotated[UserbotClient, Depends(get_client)]


@router.get("/live")
async def live() -> dict[str, str]:
    """Чистый liveness: процесс отвечает. Без I/O и без зависимостей."""
    return {"status": "ok"}


@router.get("/health")
async def health(
    client: Client,
    sender_id: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Состояние сессий из памяти; сеть не трогаем, ответ мгновенный."""
    if sender_id is not None:
        return client.health_for(sender_id)
    return client.health()
