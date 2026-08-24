"""Синхронизация статистики и статусов активных кампаний (spec 2026-07-17 §9).

Ходим по активным кампаниям тенанта (`launched`/`moderation` с внешним id),
спрашиваем площадку через её `PlatformAdapter` и сохраняем результат:

- `get_stats(external_id)` → срез `Stat` (через `services.stats.fetch_campaign_stats`
  и `db.repositories.save_stat`);
- `get_status(external_id)` → актуальный статус кампании (`set_campaign_status`).

Адаптер берём тем же способом, что и остановка кампании
(`launch_service.adapter_for_channel` по каналу кабинета), — ядро по-прежнему не
знает про конкретные площадки (CLAUDE.md §1.3). Токен — кабинета САМОЙ кампании
(`launch_service.campaign_vk_token`), а не из окружения: при нескольких
кабинетах общий токен подставил бы чужой доступ.

Ошибка по одной кампании не роняет синк остальных: она попадает в сводку как
`error` и уходит в лог. Кампания без определённого кабинета (легаси без своего
`ad_account_id` при отсутствии или неоднозначности кабинета по умолчанию)
попадает в сводку как `skipped` — синкать её нечем токеном, но это не повод
ронять остальные. Коммит — на вызывающем.

`sync_cabinet_stats` (задача 2, дефект 1) — та же логика, но по ОДНОМУ кабинету
(`campaign.external_id`): используется входом оператора в кабинет в боте, чтобы
не гонять синк по всем активным кампаниям тенанта ради одного просмотра.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Literal

from config.settings import Settings, get_settings
from db.models import Campaign
from db.repositories import list_active_campaigns, save_stat, set_campaign_status
from integrations.adapter import PlatformAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from services.launch_service import (
    MODERATION_MARKERS,
    adapter_for_channel,
    campaign_channel,
    resolve_channel_vk_token,
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


async def _adapter_for_campaign(
    session: AsyncSession,
    account_id: int,
    campaign: Campaign,
    channel_name: str,
    settings: Settings,
    overrides: Mapping[str, PlatformAdapter],
    cache: dict[tuple[str, int | None], PlatformAdapter],
) -> PlatformAdapter | None:
    """Адаптер для одной кампании: подмена теста → кэш → сборка токеном её кабинета.

    Кэш ключуется парой (канал, `ad_account_id`), а не одним каналом: у одного
    канала (`vk_api`) разные кабинеты живут на разных токенах, общий кэш по
    имени канала подсунул бы чужой доступ соседней кампании.

    `None` — кабинет VK-кампании нельзя определить однозначно (см.
    `resolve_channel_vk_token`): синкать её нечем токеном.
    """
    override = overrides.get(channel_name)
    if override is not None:
        return override

    cache_key = (channel_name, campaign.ad_account_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    vk_token = await resolve_channel_vk_token(session, account_id, campaign, channel_name, settings)
    if vk_token is None:
        return None

    adapter = adapter_for_channel(settings, channel_name, vk_token)
    cache[cache_key] = adapter
    return adapter


async def _sync_campaigns(
    session: AsyncSession,
    account_id: int,
    campaigns: Sequence[Campaign],
    cfg: Settings,
    overrides: Mapping[str, PlatformAdapter],
) -> dict[int, str]:
    """Общий цикл «метрики → срез, статус → БД» по уже отобранному списку кампаний.

    Переиспользуется `sync_campaign_stats` (весь тенант) и `sync_cabinet_stats`
    (один кабинет) — отличается только то, какие кампании в него попадают.
    """
    cache: dict[tuple[str, int | None], PlatformAdapter] = {}
    summary: dict[int, str] = {}

    for campaign in campaigns:
        try:
            channel_name = await campaign_channel(session, account_id, campaign)
            adapter = await _adapter_for_campaign(
                session, account_id, campaign, channel_name, cfg, overrides, cache
            )
            if adapter is None:
                summary[campaign.id] = "skipped"
                continue
            await _sync_one(session, account_id, campaign, adapter)
            summary[campaign.id] = "ok"
        except Exception:  # noqa: BLE001 — одна кампания не должна ронять синк остальных
            logger.exception("stats sync failed for campaign %s", campaign.id)
            summary[campaign.id] = "error"
    return summary


async def sync_campaign_stats(
    session: AsyncSession,
    account_id: int,
    *,
    settings: Settings | None = None,
    adapters: Mapping[str, PlatformAdapter] | None = None,
) -> dict[int, str]:
    """Синхронизировать метрики и статусы активных кампаний тенанта.

    Возвращает сводку `{campaign_id: "ok" | "error" | "skipped"}`. `adapters` —
    подмена «канал → адаптер» по имени канала (тесты и ручные прогоны); по
    умолчанию адаптер собирается токеном кабинета САМОЙ кампании — так же, как
    при остановке (`launch_service.stop_campaign`), а не общим токеном канала.
    """
    cfg = settings or get_settings()
    overrides = dict(adapters or {})
    campaigns = await list_active_campaigns(session, account_id)
    return await _sync_campaigns(session, account_id, campaigns, cfg, overrides)


async def sync_cabinet_stats(
    session: AsyncSession,
    account_id: int,
    cabinet_id: str,
    *,
    settings: Settings | None = None,
    adapters: Mapping[str, PlatformAdapter] | None = None,
) -> dict[int, str]:
    """Синхронизировать метрики и статус кампаний ОДНОГО кабинета (задача 2, дефект 1).

    «Кабинет» здесь — то же, чем оперирует `services.cabinet_stats`: внешний id
    кампании (`Campaign.external_id`, см. `db.repositories.list_cabinet_campaigns`).
    Используется входом оператора в кабинет в боте — обновить метрики ИМЕННО этого
    кабинета перед показом, не трогая остальные активные кампании тенанта.

    Возвращает пустую сводку, если среди активных кампаний нет ни одной с таким
    `external_id` — честно нечего синкать, это не ошибка сама по себе (кампания
    вне `launched`/`moderation`: ещё не запущена — `prepared`/`failed`, — либо
    уже остановлена оператором — `stopped`). Вызывающий определяет итоговый исход
    через `cabinet_sync_outcome` — пустая сводка это `"nothing_to_update"`, а не
    `"updated"` и не `"failed"`.
    """
    cfg = settings or get_settings()
    overrides = dict(adapters or {})
    campaigns = [
        c for c in await list_active_campaigns(session, account_id) if c.external_id == cabinet_id
    ]
    return await _sync_campaigns(session, account_id, campaigns, cfg, overrides)


CabinetSyncOutcome = Literal["updated", "nothing_to_update", "failed"]


def cabinet_sync_outcome(results: Mapping[int, str]) -> CabinetSyncOutcome:
    """Трёхзначная честная оценка синка ОДНОГО кабинета (A2, доработка того же дефекта).

    `cabinet_sync_ok` (первая версия A2) сводила оценку к двум состояниям: пустая
    сводка — под этим `external_id` нет ни одной активной кампании (кабинет не
    найден или ничего не запущено) — раньше на уровне `core/api/v1/cabinets.py`
    ошибочно засчитывалась успехом (`failed == sum(... != "ok")` тривиально равен
    нулю на пустом словаре), и это было честно исправлено на «не успех». Но
    «не успех» бот тогда рисовал как сбой площадки (⚠️ «не удалось обновить») —
    а пустая сводка по кампании в статусе `prepared`/`stopped`/`failed` (см.
    `db.repositories.list_active_campaigns` — в неё отбираются только `launched`/
    `moderation`) получается КАЖДЫЙ раз, когда кампания вне этих двух статусов:
    ещё не запущена (`prepared`), упала при запуске (`failed`) — или уже
    остановлена оператором (`stopped`, см. `launch_service.stop_campaign`) ПОСЛЕ
    того, как реально откручивалась и набрала статистику. Это норма, а не сбой:
    цифры по такой кампании окончательные, обновлять нечего.

    Три исхода вместо двух:
    - `"updated"` — сводка не пуста и по всем кампаниям кабинета `"ok"`: что-то
      реально совпало и синкнулось без ошибок;
    - `"nothing_to_update"` — сводка пуста: под кабинетом нет ни одной активной
      кампании (не запущена или не найдена) — синкать было нечего, это ожидаемо;
    - `"failed"` — сводка не пуста, но есть хотя бы один `"error"`/`"skipped"`:
      были настоящие проблемы синка (площадка не ответила, адаптер не собрался) —
      вот здесь тревожная пометка в боте обязана остаться (не имитируем успех,
      CLAUDE.md §7).
    """
    if not results:
        return "nothing_to_update"
    if all(outcome == "ok" for outcome in results.values()):
        return "updated"
    return "failed"
