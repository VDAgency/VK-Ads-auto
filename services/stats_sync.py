"""Синхронизация статистики и статусов активных кампаний (spec 2026-07-17 §9).

Ходим по активным кампаниям тенанта (`launched`/`moderation` с внешним id),
спрашиваем площадку через её `PlatformAdapter` и сохраняем результат:

- `get_stats(external_id)` → срез `Stat` (через `services.stats.fetch_campaign_stats`
  и `db.repositories.save_stat`);
- `get_status(external_id)` → актуальный статус кампании (`set_campaign_status`).

Адаптер берём тем же способом, что и остановка кампании
(`launch_service.adapter_for_channel` по каналу кабинета), — ядро по-прежнему не
знает про конкретные площадки (CLAUDE.md §1.3).

Ошибка по одной кампании не роняет синк остальных: она попадает в сводку как
`error` и уходит в лог. Коммит — на вызывающем.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from config.settings import Settings, get_settings
from db.models import Campaign
from db.repositories import list_active_campaigns, save_stat, set_campaign_status
from integrations.adapter import PlatformAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from services.launch_service import (
    MODERATION_MARKERS,
    adapter_for_channel,
    campaign_channel,
)
from services.stats import fetch_campaign_stats

logger = logging.getLogger(__name__)

# Статусы площадки, означающие «кампания крутится».
_ACTIVE_STATUSES = frozenset({"active", "launched", "running", "started"})
# Статусы площадки, означающие «кампания больше не крутится».
_STOPPED_STATUSES = frozenset({"blocked", "stopped", "paused", "deleted", "archived", "completed"})


def _map_platform_status(platform_status: str) -> str | None:
    """Статус площадки → статус кампании в БД. `None` — оставить как есть.

    Незнакомый или неизвестный площадке статус не трогаем: лучше устаревшая
    запись, чем ложная «остановлена» (успех/провал не имитируем, CLAUDE.md §7).
    """
    lowered = platform_status.strip().lower()
    if not lowered or lowered == "unknown":
        return None
    if any(marker in lowered for marker in MODERATION_MARKERS):
        return "moderation"
    if lowered in _ACTIVE_STATUSES:
        return "launched"
    if lowered in _STOPPED_STATUSES:
        return "stopped"
    return None


async def _sync_one(
    session: AsyncSession,
    account_id: int,
    campaign: Campaign,
    adapter: PlatformAdapter,
) -> None:
    """Один цикл «метрики → срез, статус → БД» по кампании."""
    external_id = campaign.external_id or ""
    stats = await fetch_campaign_stats(adapter, external_id)
    await save_stat(
        session,
        account_id,
        external_id,
        stats.shows,
        stats.clicks,
        stats.spent,
        stats.results,
    )
    status = _map_platform_status(await adapter.get_status(external_id))
    if status is not None and status != campaign.status:
        await set_campaign_status(session, account_id, campaign.id, status)


async def sync_campaign_stats(
    session: AsyncSession,
    account_id: int,
    *,
    settings: Settings | None = None,
    adapters: Mapping[str, PlatformAdapter] | None = None,
) -> dict[int, str]:
    """Синхронизировать метрики и статусы активных кампаний тенанта.

    Возвращает сводку `{campaign_id: "ok" | "error"}`. `adapters` — подмена
    «канал → адаптер» (тесты и ручные прогоны); по умолчанию адаптеры собираются
    по настройкам, как при остановке кампании.
    """
    cfg = settings or get_settings()
    overrides = dict(adapters or {})
    cache: dict[str, PlatformAdapter] = {}
    summary: dict[int, str] = {}

    for campaign in await list_active_campaigns(session, account_id):
        try:
            channel_name = await campaign_channel(session, account_id, campaign)
            adapter = overrides.get(channel_name) or cache.get(channel_name)
            if adapter is None:
                adapter = adapter_for_channel(cfg, channel_name)
                cache[channel_name] = adapter
            await _sync_one(session, account_id, campaign, adapter)
            summary[campaign.id] = "ok"
        except Exception:  # noqa: BLE001 — одна кампания не должна ронять синк остальных
            logger.exception("stats sync failed for campaign %s", campaign.id)
            summary[campaign.id] = "error"
    return summary
