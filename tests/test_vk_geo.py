"""Резолв гео из брифа в region id VK Ads (справочник подменён фейком, без сети)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from integrations.vk_geo import RUSSIA_REGION_ID, VkGeoResolver

# Фрагмент реального /regions.json: имена только английские (см. docs/VK_API_REFERENCE.md).
_REGIONS: list[dict[str, Any]] = [
    {"id": 188, "name": "Россия", "parent_id": None, "flags": []},
    {"id": 70, "name": "Московская область", "parent_id": 188, "flags": []},
    {"id": 5506, "name": "Москва", "parent_id": 70, "flags": []},
    {"id": 5567, "name": "Тверь", "parent_id": 123, "flags": []},
    {"id": 5580, "name": "Королёв", "parent_id": 70, "flags": []},
    {"id": 5560, "name": "Санкт-Петербург", "parent_id": 72, "flags": []},
]


class _Fetcher:
    """Считает обращения к справочнику — так проверяем кэш в рамках экземпляра."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> list[dict[str, Any]]:
        self.calls += 1
        return [dict(region) for region in _REGIONS]


def _resolve(geo_raw: str, fetcher: _Fetcher | None = None) -> list[int]:
    resolver = VkGeoResolver(fetcher or _Fetcher())
    return asyncio.run(resolver.resolve(geo_raw))


def test_moscow_resolves_to_city_id() -> None:
    assert _resolve("Москва") == [5506]


def test_case_and_extra_spaces_are_ignored() -> None:
    assert _resolve("  сАнкт-ПетербурГ  ") == [5560]


def test_common_alias_resolves() -> None:
    assert _resolve("СПб") == [5560]


def test_city_prefix_is_stripped() -> None:
    assert _resolve("г. Москва") == [5506]


def test_whole_russia_resolves_to_country() -> None:
    assert _resolve("вся Россия") == [RUSSIA_REGION_ID]


def test_multiple_regions_are_split() -> None:
    assert _resolve("Москва, Санкт-Петербург") == [5506, 5560]


def test_conjunction_splits_and_duplicates_collapse() -> None:
    assert _resolve("Москва и Москва") == [5506]


def test_english_name_passes_through() -> None:
    assert _resolve("Москва") == [5506]


def test_unknown_geo_falls_back_to_russia_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="integrations.vk_geo"):
        assert _resolve("Урюпинск-Сити") == [RUSSIA_REGION_ID]
    assert any("Урюпинск-Сити" in record.getMessage() for record in caplog.records)


def test_empty_geo_falls_back_to_russia() -> None:
    assert _resolve("   ") == [RUSSIA_REGION_ID]


def test_partially_unknown_geo_keeps_resolved_regions() -> None:
    assert _resolve("Москва, Урюпинск-Сити") == [5506]


def test_reference_is_fetched_once_per_instance() -> None:
    fetcher = _Fetcher()
    resolver = VkGeoResolver(fetcher)

    async def scenario() -> None:
        await resolver.resolve("Москва")
        await resolver.resolve("Санкт-Петербург")

    asyncio.run(scenario())
    assert fetcher.calls == 1


def test_fetch_failure_falls_back_to_russia(caplog: pytest.LogCaptureFixture) -> None:
    async def boom() -> list[dict[str, Any]]:
        raise RuntimeError("regions unavailable")

    resolver = VkGeoResolver(boom)
    with caplog.at_level(logging.WARNING, logger="integrations.vk_geo"):
        assert asyncio.run(resolver.resolve("Москва")) == [RUSSIA_REGION_ID]
    assert caplog.records
