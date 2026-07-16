"""Health юзербота: GET /health → состояние сессий операторов (spec §6).

Без параметров — все сессии: `{sessions: [{sender_id, authorized, phone?}]}`.
С `?sender_id=` — одна: `{sender_id, authorized, phone?}` (бот проверяет сессию
вызвавшего оператора перед /send_brief; поллер раз в 60с берёт полный список).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from userbot.api import get_client
from userbot.telethon_client import UserbotClient

router = APIRouter(tags=["health"])

Client = Annotated[UserbotClient, Depends(get_client)]


@router.get("/health")
async def health(
    client: Client,
    sender_id: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    if sender_id is not None:
        return await client.health_for(sender_id)
    return await client.health()
