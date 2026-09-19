"""Внутренний API статистики (`/api/v1/stats`) — ручной триггер синхронизации и
ежедневная сводка оператору (задача 3А).

Тонкий роутер: обход активных кампаний, опрос площадки и запись срезов — в
`services/stats_sync` и `services/daily_digest`. SQL здесь нет (CLAUDE.md §1.3).
Снаружи оба пути закрыты на ingress (`infra/Caddyfile`), как соседние
операторские ресурсы: бот и n8n ходят в ядро внутри compose-сети, минуя Caddy.
"""

from __future__ import annotations

from typing import Annotated

from config.settings import get_settings
from db.session import get_session
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from services.daily_digest import collect_digest, render_digest
from services.notifier import notify_operator
from services.stats_sync import sync_campaign_stats
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/stats", tags=["stats"])

DEFAULT_ACCOUNT_ID = 1


class StatsSyncOut(BaseModel):
    """Итог прогона: сколько кампаний синхронизировано и построчная сводка."""

    synced: int
    failed: int
    results: dict[int, str]


class StatsDigestOut(BaseModel):
    """Итог сборки сводки: отправлена ли, сколько кампаний, текст (только при dry_run)."""

    sent: bool
    campaigns: int
    text: str | None


async def sync_stats_response(session: AsyncSession) -> StatsSyncOut:
    """Снять метрики и статусы активных кампаний, сохранить срезы.

    Общая часть для операторского эндпоинта и веб-админки.
    """
    results = await sync_campaign_stats(session, DEFAULT_ACCOUNT_ID)
    await session.commit()
    failed = sum(1 for outcome in results.values() if outcome != "ok")
    return StatsSyncOut(synced=len(results) - failed, failed=failed, results=results)


@router.post("/sync")
async def sync(session: Annotated[AsyncSession, Depends(get_session)]) -> StatsSyncOut:
    """Снять метрики и статусы активных кампаний, сохранить срезы."""
    return await sync_stats_response(session)


@router.post("/digest")
async def digest(
    session: Annotated[AsyncSession, Depends(get_session)],
    dry_run: bool = False,
) -> StatsDigestOut:
    """Собрать ежедневную сводку и отправить оператору (n8n, 09:00 МСК).

    `dry_run=true` — только собрать текст и вернуть его в ответе, оператору
    ничего не отправлять (ручная проверка воркфлоу без спама в Telegram).
    """
    report = await collect_digest(session, DEFAULT_ACCOUNT_ID, get_settings())
    await session.commit()
    text = render_digest(report)
    if dry_run:
        return StatsDigestOut(sent=False, campaigns=len(report.rows), text=text)
    await notify_operator(text)
    return StatsDigestOut(sent=True, campaigns=len(report.rows), text=None)
