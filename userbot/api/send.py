"""Отправка сообщения клиенту: POST /send (spec §6, §9).

Ответ — контракт для `services/delivery/telegram.py`: `{ok, error?}`, где `error` —
код из §9. HTTP-статус подбираем под тип ошибки (для наглядности логов), но ядро
опирается на тело ответа, а не на статус.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from userbot.api import get_client
from userbot.telethon_client import UserbotClient

router = APIRouter(tags=["send"])

Client = Annotated[UserbotClient, Depends(get_client)]

# Код ошибки §9 → HTTP-статус (только для читаемости; ядро смотрит тело).
_STATUS_BY_ERROR = {
    "username_not_occupied": 400,
    "username_invalid": 400,
    "privacy_restricted": 403,
    "peer_flood": 429,
    "session_expired": 401,
    "sender_not_authorized": 401,
}


class SendRequest(BaseModel):
    sender_id: int
    username: str
    text: str


@router.post("/send")
async def send(body: SendRequest, client: Client) -> JSONResponse:
    error, display_name = await client.send(body.sender_id, body.username, body.text)
    if error is None:
        payload: dict[str, object] = {"ok": True}
        if display_name:
            payload["display_name"] = display_name
        return JSONResponse(payload)
    status = _STATUS_BY_ERROR.get(error, 502)
    return JSONResponse({"ok": False, "error": error}, status_code=status)
