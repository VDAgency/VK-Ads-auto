import asyncio

from integrations.adapter import PlatformAdapter
from services.brief_parser import Goal
from services.goals import objective_for
from services.launch import LaunchResult, launch_confirmation, run_campaign
from services.mapping import CampaignSpec


class _RecordingAdapter(PlatformAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        self.calls.append(("create_campaign", cabinet_id, goal))
        return "camp-1"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        self.calls.append(("upload_creative", campaign_id, creative_ref))
        return "crt-1"

    async def launch(self, campaign_id: str) -> None:
        self.calls.append(("launch", campaign_id))

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {}

    async def health_check(self) -> bool:
        return True


SPEC = CampaignSpec(objective="socialengagement", name="n", object_url="u", geo_raw="Москва")


def test_objective_for_subscribers() -> None:
    assert objective_for(Goal.SUBSCRIBERS) == "socialengagement"


def test_run_campaign_creates_and_launches() -> None:
    adapter = _RecordingAdapter()
    result = asyncio.run(run_campaign(adapter, "cab-1", SPEC))
    assert result.campaign_id == "camp-1"
    assert result.launched is True
    assert ("create_campaign", "cab-1", "socialengagement") in adapter.calls
    assert ("launch", "camp-1") in adapter.calls
    assert not any(call[0] == "upload_creative" for call in adapter.calls)


def test_run_campaign_uploads_creative_when_provided() -> None:
    adapter = _RecordingAdapter()
    asyncio.run(run_campaign(adapter, "cab-1", SPEC, creative_ref="ad.png"))
    assert ("upload_creative", "camp-1", "ad.png") in adapter.calls


def test_confirmation_mentions_campaign_id() -> None:
    message = launch_confirmation(LaunchResult(campaign_id="camp-9", launched=True))
    assert "camp-9" in message
