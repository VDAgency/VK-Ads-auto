import asyncio

import pytest
from integrations.adapter import PlatformAdapter
from integrations.channels import (
    Channel,
    ChannelConfig,
    ChannelRouter,
    NoHealthyChannelError,
)


class _FakeAdapter(PlatformAdapter):
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    async def health_check(self) -> bool:
        return self._healthy

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        raise NotImplementedError

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        raise NotImplementedError

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        raise NotImplementedError

    async def launch(self, campaign_id: str) -> None:
        raise NotImplementedError

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        raise NotImplementedError


def _select(
    adapters: dict[Channel, PlatformAdapter], config: ChannelConfig
) -> tuple[Channel, PlatformAdapter]:
    return asyncio.run(ChannelRouter(adapters, config).select())


def test_returns_default_when_healthy() -> None:
    channel, _ = _select(
        {Channel.VK_API: _FakeAdapter(True), Channel.KOTBOT: _FakeAdapter(True)},
        ChannelConfig(default=Channel.VK_API),
    )
    assert channel is Channel.VK_API


def test_falls_back_when_default_unhealthy() -> None:
    channel, _ = _select(
        {Channel.VK_API: _FakeAdapter(False), Channel.KOTBOT: _FakeAdapter(True)},
        ChannelConfig(default=Channel.VK_API),
    )
    assert channel is Channel.KOTBOT


def test_forced_overrides_default() -> None:
    channel, _ = _select(
        {Channel.VK_API: _FakeAdapter(True), Channel.KOTBOT: _FakeAdapter(True)},
        ChannelConfig(default=Channel.VK_API, forced=Channel.KOTBOT),
    )
    assert channel is Channel.KOTBOT


def test_raises_when_no_channel_healthy() -> None:
    with pytest.raises(NoHealthyChannelError):
        _select(
            {Channel.VK_API: _FakeAdapter(False), Channel.KOTBOT: _FakeAdapter(False)},
            ChannelConfig(default=Channel.VK_API),
        )
