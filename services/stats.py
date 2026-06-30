"""Сбор статистики кампаний и сборка ежедневного дайджеста.

Метрики берём через адаптер (площадка-агностично), считаем производные (CTR/CPC/CPL)
и форматируем отчёт. Хранение срезов — `db.repositories.save_stat`. Расписание
(дайджест в 9:00) подключается планировщиком/n8n отдельно; здесь — сбор и формат.
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


def build_digest(stats: list[CampaignStats]) -> str:
    """Собрать текст ежедневного дайджеста по списку кампаний."""
    if not stats:
        return "Активных кампаний нет."
    lines = ["Ежедневный отчёт:"]
    total_spent = 0.0
    total_results = 0.0
    for item in stats:
        lines.append(
            f"• {item.campaign_id}: показы {int(item.shows)}, расход {item.spent:.0f} ₽, "
            f"подписки {int(item.results)}, CTR {item.ctr}%, CPC {item.cpc} ₽, CPL {item.cpl} ₽"
        )
        total_spent += item.spent
        total_results += item.results
    lines.append(f"Итого: расход {total_spent:.0f} ₽, подписки {int(total_results)}.")
    return "\n".join(lines)
