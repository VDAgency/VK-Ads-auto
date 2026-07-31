"""Диагностика точек подключения: `GET /diagnostics/endpoints`.

Отдаёт матрицу достижимости изнутри контейнера — то, что раньше снимали руками по
SSH. Оператор открывает её из бота, когда доставка сломалась и надо понять, где
именно: закрыты адреса или дело в самой сессии.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from userbot.api import get_client
from userbot.diagnostics import probe_endpoints
from userbot.telethon_client import UserbotClient

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

Client = Annotated[UserbotClient, Depends(get_client)]


@router.get("/endpoints")
async def endpoints(client: Client) -> dict[str, object]:
    """Достижимость точек из текущей цепочки перебора."""
    candidates = client.diagnostic_candidates()
    results = await probe_endpoints(candidates)
    return {
        "endpoints": [
            {
                "label": item.endpoint.label(),
                "dc_id": item.endpoint.dc_id,
                "ip": item.endpoint.ip,
                "port": item.endpoint.port,
                "transport": item.endpoint.transport.value,
                "via_proxy": item.endpoint.via_proxy,
                "reachable": item.reachable,
                "latency_ms": round(item.latency * 1000) if item.latency is not None else None,
            }
            for item in results
        ]
    }
