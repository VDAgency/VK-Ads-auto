"""Внутренний API статистики (`/api/v1/stats`) — ручной триггер синхронизации.

Тонкий роутер: обход активных кампаний, опрос площадки и запись срезов — в
`services/stats_sync`. SQL здесь нет (CLAUDE.md §1.3). Снаружи путь закрыт на
ingress (`infra/Caddyfile`), как соседние операторские ресурсы: бот и n8n ходят
в ядро внутри compose-сети, минуя Caddy.
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from services.stats_sync import sync_campaign_stats
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/stats", tags=["stats"])

DEFAULT_ACCOUNT_ID = 1


class StatsSyncOut(BaseModel):
    """Итог прогона: сколько кампаний синхронизировано и построчная сводка."""

    synced: int
    failed: int
    results: dict[int, str]


@router.post("/sync")
async def sync(session: Annotated[AsyncSession, Depends(get_session)]) -> StatsSyncOut:
    """Снять метрики и статусы активных кампаний, сохранить срезы."""
    results = await sync_campaign_stats(session, DEFAULT_ACCOUNT_ID)
    await session.commit()
    failed = sum(1 for outcome in results.values() if outcome != "ok")
    return StatsSyncOut(synced=len(results) - failed, failed=failed, results=results)
