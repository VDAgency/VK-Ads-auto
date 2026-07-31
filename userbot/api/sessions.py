"""Сессии операторов: чтение состояния и проверка по требованию.

Чтение (`GET`) идёт из памяти и стоит доли миллисекунды — его можно звать хоть в
каждом запросе. Проверка (`POST .../probe`) действительно ходит в сеть и потому
вынесена в отдельный метод: оператор жмёт «проверить сейчас», когда хочет знать
состояние прямо сейчас, а не то, что успела увидеть фоновая проверка.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from userbot.api import get_client
from userbot.telethon_client import UserbotClient

router = APIRouter(prefix="/sessions", tags=["sessions"])

Client = Annotated[UserbotClient, Depends(get_client)]


@router.get("")
async def list_sessions(client: Client) -> dict[str, object]:
    """Состояние всех известных сессий (из памяти)."""
    return client.health()


@router.get("/{sender_id}")
async def get_session(sender_id: int, client: Client) -> dict[str, object]:
    """Состояние одной сессии (из памяти)."""
    return client.health_for(sender_id)


@router.post("/{sender_id}/probe")
async def probe_session(sender_id: int, client: Client) -> dict[str, object]:
    """Проверить сессию по сети прямо сейчас и вернуть обновлённое состояние."""
    return await client.probe(sender_id)
