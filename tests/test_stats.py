import asyncio

from db.repositories import save_stat
from integrations.adapter import PlatformAdapter
from services.stats import CampaignStats, build_digest, fetch_campaign_stats
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_brief_intake import _with_db


class _StatsAdapter(PlatformAdapter):
    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "c"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return "c"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return "c"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {"shows": 100.0, "clicks": 5.0, "spent": 250.0, "goals": 10.0}

    async def health_check(self) -> bool:
        return True


def test_fetch_and_derived_metrics() -> None:
    stats = asyncio.run(fetch_campaign_stats(_StatsAdapter(), "camp-1"))
    assert stats.shows == 100.0
    assert stats.results == 10.0
    assert stats.ctr == 5.0
    assert stats.cpc == 50.0
    assert stats.cpl == 25.0


def test_zero_safe_derived() -> None:
    stats = CampaignStats("c", shows=0, clicks=0, spent=0, results=0)
    assert stats.ctr == 0.0
    assert stats.cpc == 0.0
    assert stats.cpl == 0.0


def test_build_digest_with_totals() -> None:
    text = build_digest([CampaignStats("camp-1", 100, 5, 250, 10)])
    assert "camp-1" in text
    assert "Итого" in text


def test_build_digest_empty() -> None:
    assert build_digest([]) == "Активных кампаний нет."


def test_save_stat_persists() -> None:
    async def scenario(session: AsyncSession) -> int:
        stat = await save_stat(session, 1, "camp-1", 100, 5, 250, 10)
        return stat.id

    assert asyncio.run(_with_db(scenario)) >= 1
