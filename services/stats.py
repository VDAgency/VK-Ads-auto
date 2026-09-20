"""Снятие метрик кампании с площадки и приведение к производным (CTR/CPC/CPL).

Метрики берём через адаптер (площадка-агностично). Хранение срезов —
`db.repositories.save_stat`. Ежедневная сводка оператору (09:00 МСК, задача 3А)
— отдельный модуль `services.daily_digest`, который переиспользует
`fetch_campaign_stats` через `services.stats_sync`, а не эти строки напрямую.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.adapter import PlatformAdapter


@dataclass(frozen=True)
class CampaignStats:
    """Срез метрик кампании + производные показатели."""

    campaign_id: str
    shows: float
    clicks: float
    spent: float
    results: float  # результат по цели (подписки)

    @property
    def cpc(self) -> float:
        return round(self.spent / self.clicks, 2) if self.clicks else 0.0

    @property
    def ctr(self) -> float:
        return round(self.clicks / self.shows * 100, 2) if self.shows else 0.0

    @property
    def cpl(self) -> float:
        return round(self.spent / self.results, 2) if self.results else 0.0


async def fetch_campaign_stats(adapter: PlatformAdapter, campaign_id: str) -> CampaignStats:
    """Снять метрики кампании через адаптер и привести к `CampaignStats`."""
    raw = await adapter.get_stats(campaign_id)
    return CampaignStats(
        campaign_id=campaign_id,
        shows=raw.get("shows", 0.0),
        clicks=raw.get("clicks", 0.0),
        spent=raw.get("spent", 0.0),
        results=raw.get("goals", 0.0),
    )
