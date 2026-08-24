"""`bot.api_client.sync_cabinet_stats`: три исхода синка кабинета (A3, respx-моки).

Клиент — тонкая обёртка над `POST /cabinets/{id}/stats/sync`: разбирает поле
`outcome` из ответа ядра в один из трёх литералов, которые дальше читает
`bot/handlers/stats.py`, решая, какую пометку показать оператору (или не
показывать вовсе). Сетевой сбой самого запроса и незнакомое/отсутствующее поле
`outcome` честно превращаются в `"failed"` — бот не должен молчать о том, что
не смог даже спросить ядро, обновились ли данные.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import respx
from bot import api_client

_CORE = "http://api:8000"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.get_settings", lambda: SimpleNamespace(core_base_url=_CORE))


def test_sync_cabinet_stats_returns_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/cabinets/camp-1/stats/sync").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "outcome": "updated",
                        "synced": 1,
                        "failed": 0,
                        "results": {"1": "ok"},
                    },
                )
            )
            return await api_client.sync_cabinet_stats("camp-1")

    assert asyncio.run(scenario()) == "updated"


def test_sync_cabinet_stats_returns_nothing_to_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кампания не запущена — ядро отвечает `ok=True`, `outcome="nothing_to_update"`."""
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/cabinets/camp-1/stats/sync").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "outcome": "nothing_to_update",
                        "synced": 0,
                        "failed": 0,
                        "results": {},
                    },
                )
            )
            return await api_client.sync_cabinet_stats("camp-1")

    assert asyncio.run(scenario()) == "nothing_to_update"


def test_sync_cabinet_stats_returns_failed_on_platform_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/cabinets/camp-1/stats/sync").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": False,
                        "outcome": "failed",
                        "synced": 0,
                        "failed": 1,
                        "results": {"1": "error"},
                    },
                )
            )
            return await api_client.sync_cabinet_stats("camp-1")

    assert asyncio.run(scenario()) == "failed"


def test_sync_cabinet_stats_5xx_maps_to_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ядро недоступно (5xx) — синк честно `"failed"`, а не тихий тривиальный успех."""
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/cabinets/camp-1/stats/sync").mock(
                return_value=httpx.Response(500)
            )
            return await api_client.sync_cabinet_stats("camp-1")

    assert asyncio.run(scenario()) == "failed"


def test_sync_cabinet_stats_transport_error_maps_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сеть недоступна — тоже честный `"failed"` (не бросаем `CoreUnavailable`: синк
    необязательный шаг перед чтением, дефект 1).
    """
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/cabinets/camp-1/stats/sync").mock(
                side_effect=httpx.ConnectError("boom")
            )
            return await api_client.sync_cabinet_stats("camp-1")

    assert asyncio.run(scenario()) == "failed"


def test_sync_cabinet_stats_unknown_outcome_maps_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Незнакомое/отсутствующее поле `outcome` в ответе ядра — не имитируем успех,
    честный `"failed"` (CLAUDE.md §7).
    """
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/cabinets/camp-1/stats/sync").mock(
                return_value=httpx.Response(200, json={"ok": True, "synced": 0, "failed": 0})
            )
            return await api_client.sync_cabinet_stats("camp-1")

    assert asyncio.run(scenario()) == "failed"
