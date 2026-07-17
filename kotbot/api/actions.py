"""Action API kotbot (spec §4.3) — до K-PR3 все роуты отвечают 501.

Формы запросов/ответов зафиксированы в спеке; здесь — только контуры маршрутов,
чтобы адаптер ядра (K-PR4) имел стабильные URL. Реализация флоу (создание
кабинета/кампании, креатив, запуск, статистика) придёт с Playwright-бэкендом.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["actions"])

_NOT_IMPLEMENTED_DETAIL = "not_implemented"


def _not_implemented() -> HTTPException:
    return HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)


@router.post("/cabinets")
async def create_cabinet() -> dict[str, str]:
    """`{client_ref, ad_object_url, ad_object_name?}` → `{external_ref}` (K-PR3)."""
    raise _not_implemented()


@router.post("/campaigns")
async def create_campaign() -> dict[str, str]:
    """`{cabinet_ref, spec}` → `{external_id, status}` (K-PR3)."""
    raise _not_implemented()


@router.post("/campaigns/{ext}/creative")
async def upload_creative(ext: str) -> dict[str, str]:
    """`{file_path, title?, body?}` → `{creative_ref}` (K-PR3, том creatives RO)."""
    raise _not_implemented()


@router.post("/campaigns/{ext}/launch")
async def launch_campaign(ext: str) -> dict[str, str]:
    """→ `{status: "launched"|"moderation"}` (K-PR3)."""
    raise _not_implemented()


@router.post("/campaigns/{ext}/stop")
async def stop_campaign(ext: str) -> dict[str, str]:
    """→ `{status: "stopped"}` (K-PR3)."""
    raise _not_implemented()


@router.get("/campaigns/{ext}/stats")
async def campaign_stats(ext: str) -> dict[str, float]:
    """→ `{shows, clicks, spent, goals}` (K-PR3)."""
    raise _not_implemented()


@router.get("/campaigns/{ext}/status")
async def campaign_status(ext: str) -> dict[str, str]:
    """→ `{status}` (K-PR3)."""
    raise _not_implemented()


@router.post("/session/validate")
async def validate_session() -> dict[str, str]:
    """Глубокая проверка сессии браузером (spec §4.1, ручная/отладочная; K-PR3)."""
    raise _not_implemented()
