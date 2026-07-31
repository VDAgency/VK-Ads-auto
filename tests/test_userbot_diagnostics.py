"""Матрица достижимости точек — без единого настоящего сокета."""

from __future__ import annotations

import asyncio

from userbot.diagnostics import EndpointProbe, probe_endpoints
from userbot.endpoints import Endpoint, Transport

_POINTS = [
    Endpoint(dc_id=2, ip="149.154.167.51", port=443),
    Endpoint(dc_id=2, ip="149.154.167.51", port=5222, transport=Transport.OBFUSCATED),
    Endpoint(dc_id=4, ip="149.154.167.91", port=443, via_proxy=True),
]


def _probe_factory(reachable_ports: set[int]) -> object:
    async def probe(host: str, port: int, limit: float) -> float | None:
        return 0.05 if port in reachable_ports else None

    return probe


def test_reachable_and_dead_points_are_distinguished() -> None:
    results = asyncio.run(
        probe_endpoints(_POINTS, probe=_probe_factory({5222}))  # type: ignore[arg-type]
    )
    assert [item.reachable for item in results] == [False, True, False]


def test_latency_is_reported() -> None:
    results = asyncio.run(
        probe_endpoints(_POINTS[:1], probe=_probe_factory({443}))  # type: ignore[arg-type]
    )
    assert results[0].latency == 0.05


def test_order_matches_input() -> None:
    """Порядок — это порядок перебора; перемешать его значит сбить с толку."""
    results = asyncio.run(
        probe_endpoints(_POINTS, probe=_probe_factory(set()))  # type: ignore[arg-type]
    )
    assert [item.endpoint for item in results] == _POINTS


def test_budget_returns_everything_as_unreachable() -> None:
    """Экран должен открыться, даже если проверка не уложилась в бюджет."""

    async def slow(host: str, port: int, limit: float) -> float | None:
        await asyncio.sleep(10)
        return 1.0

    results = asyncio.run(probe_endpoints(_POINTS, probe=slow, budget=0.05))
    assert results == [EndpointProbe(endpoint=point, latency=None) for point in _POINTS]


def test_empty_input_is_fine() -> None:
    assert asyncio.run(probe_endpoints([])) == []
