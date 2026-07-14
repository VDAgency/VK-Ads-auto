"""Health юзербота: GET /health → {authorized, phone?} (spec §6).

Бот раз в 60с опрашивает этот эндпоинт; `authorized=false` → баннер при /send_brief.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from userbot.api import get_client
from userbot.telethon_client import UserbotClient

router = APIRouter(tags=["health"])

Client = Annotated[UserbotClient, Depends(get_client)]


@router.get("/health")
async def health(client: Client) -> dict[str, object]:
    return await client.health()
