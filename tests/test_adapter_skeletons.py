import asyncio

import pytest
from integrations.adapter import PlatformAdapter
from integrations.kotbot_playwright import KotbotAdapter
from integrations.vk_api import VkApiAdapter
from pydantic import SecretStr


def test_vk_adapter_is_platform_adapter_and_pending() -> None:
    adapter = VkApiAdapter(SecretStr("token"))
    assert isinstance(adapter, PlatformAdapter)
    assert asyncio.run(adapter.health_check()) is False
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.create_campaign("cab-1", "subscribers"))


def test_kotbot_adapter_is_platform_adapter_and_pending() -> None:
    adapter = KotbotAdapter()
    assert isinstance(adapter, PlatformAdapter)
    assert asyncio.run(adapter.health_check()) is False
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.launch("camp-1"))
