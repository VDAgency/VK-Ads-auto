import asyncio

import pytest
from integrations.adapter import PlatformAdapter
from services.mapping import CampaignSpec

_SPEC = CampaignSpec(
    objective="socialengagement", name="n", object_url="https://vk.com/club1", geo_raw="Москва"
)


class _StubAdapter(PlatformAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab-1"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        self.calls.append(("create_campaign", goal))
        return "camp-1"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        self.calls.append(("upload_creative", creative_ref))
        return "crt-1"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {"impressions": 0.0}

    async def health_check(self) -> bool:
        return True


def test_cannot_instantiate_abstract_adapter() -> None:
    with pytest.raises(TypeError):
        PlatformAdapter()  # type: ignore[abstract]


def test_concrete_subclass_instantiates() -> None:
    adapter = _StubAdapter()
    assert isinstance(adapter, PlatformAdapter)


def test_get_status_defaults_to_unknown() -> None:
    assert asyncio.run(_StubAdapter().get_status("camp-1")) == "unknown"


def test_stop_is_not_implemented_by_default() -> None:
    with pytest.raises(NotImplementedError):
        asyncio.run(_StubAdapter().stop("camp-1"))


def test_create_campaign_from_spec_defaults_to_plain_create() -> None:
    # Дефолтная реализация — прежнее поведение: одна кампания по цели из спеки.
    adapter = _StubAdapter()
    campaign_id = asyncio.run(adapter.create_campaign_from_spec("cab-1", _SPEC))
    assert campaign_id == "camp-1"
    assert adapter.calls == [("create_campaign", "socialengagement")]


def test_create_campaign_from_spec_uploads_creative_when_given() -> None:
    adapter = _StubAdapter()
    asyncio.run(adapter.create_campaign_from_spec("cab-1", _SPEC, creative_ref="ad.png"))
    assert ("upload_creative", "ad.png") in adapter.calls
