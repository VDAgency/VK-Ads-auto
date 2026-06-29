import pytest
from integrations.adapter import PlatformAdapter


class _StubAdapter(PlatformAdapter):
    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab-1"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return "camp-1"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
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
