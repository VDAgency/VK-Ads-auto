"""Версионированный внутренний API ядра (`/api/v1`).

Единый контракт для всех тонких клиентов (бот, веб, mini-app). Конкретные ресурсы
добавляются по мере развития; сейчас — только служебный `ping` как шов контракта.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.get("/ping")
def ping() -> dict[str, bool]:
    """Служебная проверка доступности внутреннего API."""
    return {"pong": True}
