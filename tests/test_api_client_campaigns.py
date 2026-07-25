"""Кампании в `bot/api_client`: остановка через ядро и таймаут запуска (respx-моки)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import respx
from bot import api_client
from bot.api_client import CampaignNotFound, CampaignStopFailed, CoreUnavailable

_CORE = "http://api:8000"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.get_settings", lambda: SimpleNamespace(core_base_url=_CORE))


def test_stop_campaign_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/campaigns/7/stop").mock(
                return_value=httpx.Response(
                    200,
                    json={"campaign_id": 7, "status": "stopped", "external_id": "ext-7"},
                )
            )
            result = await api_client.stop_campaign(7)
        assert result.campaign_id == 7
        assert result.status == "stopped"
        assert result.external_id == "ext-7"

    asyncio.run(scenario())


def test_stop_campaign_without_external_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/campaigns/7/stop").mock(
                return_value=httpx.Response(
                    200, json={"campaign_id": 7, "status": "stopped", "external_id": None}
                )
            )
            result = await api_client.stop_campaign(7)
        assert result.external_id is None

    asyncio.run(scenario())


def test_stop_campaign_404_maps_to_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/campaigns/7/stop").mock(
                return_value=httpx.Response(404, json={"detail": "campaign_not_found"})
            )
            with pytest.raises(CampaignNotFound):
                await api_client.stop_campaign(7)

    asyncio.run(scenario())


def test_stop_campaign_502_maps_to_stop_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    # 502 = площадка/канал не приняли остановку — это не «ядро лежит».
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/campaigns/7/stop").mock(
                return_value=httpx.Response(502, json={"detail": "campaign_stop_failed"})
            )
            with pytest.raises(CampaignStopFailed):
                await api_client.stop_campaign(7)

    asyncio.run(scenario())


def test_stop_campaign_500_maps_to_core_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/campaigns/7/stop").mock(return_value=httpx.Response(500))
            with pytest.raises(CoreUnavailable):
                await api_client.stop_campaign(7)

    asyncio.run(scenario())


def test_stop_campaign_transport_error_maps_to_core_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/campaigns/7/stop").mock(
                side_effect=httpx.ConnectError("boom")
            )
            with pytest.raises(CoreUnavailable):
                await api_client.stop_campaign(7)

    asyncio.run(scenario())


def test_launch_timeout_is_six_minutes() -> None:
    # Загрузка креатива тянет за собой создание кампании на площадке (spec §5).
    assert api_client._LAUNCH_TIMEOUT.read == 360.0
    assert api_client._LAUNCH_TIMEOUT.connect == 360.0
