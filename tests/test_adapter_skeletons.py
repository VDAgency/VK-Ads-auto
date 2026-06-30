import asyncio

import pytest
from integrations.adapter import PlatformAdapter
from integrations.kotbot_playwright import KotbotAdapter


def test_kotbot_adapter_is_platform_adapter_and_pending() -> None:
    adapter = KotbotAdapter()
    assert isinstance(adapter, PlatformAdapter)
    assert asyncio.run(adapter.health_check()) is False
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.launch("camp-1"))
