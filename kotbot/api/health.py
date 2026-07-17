"""Health kotbot: GET /health → состояние стратегий входа (spec §4.1).

Дёшево: файлы хранилищ + кеш-флаги needs_reauth, браузер не трогаем.
`healthy` = есть хоть одна стратегия с has_state и без needs_reauth.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from kotbot.api import get_automation
from kotbot.service import KotbotAutomation

router = APIRouter(tags=["health"])

Automation = Annotated[KotbotAutomation, Depends(get_automation)]


@router.get("/health")
async def health(automation: Automation) -> dict[str, object]:
    return automation.health()
