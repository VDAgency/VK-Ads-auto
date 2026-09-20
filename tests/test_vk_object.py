"""Резолвер числового id объекта ВК по короткому адресу (`integrations/vk_object.py`,
spec 2026-09-19-block1-remaining-gaps §D). Фикстуры HTML — `tests/fixtures/vk_pages/`
— воспроизводят живые маркерные последовательности 2026-09-20: у личной страницы
среди 12 вхождений `owner_id` первое — decoy-ноль, у сообщества есть decoy
`user_id`, не связанный с owner_id.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import httpx
import pytest
import respx
from integrations.vk_object import ResolvedVkObject, resolve_vk_object

T = TypeVar("T")

_FIXTURES = Path(__file__).parent / "fixtures" / "vk_pages"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def test_personal_page_with_decoy_zero_owner_id_resolves_to_the_real_id() -> None:
    """Живой прогон 2026-09-20: 12 вхождений `owner_id`, первое — decoy-ноль,
    остальные 11 — верный 808632468. «Первое совпадение» дало бы id `0` —
    резолвер обязан взять самое частое ненулевое значение."""

    async def scenario() -> ResolvedVkObject | None:
        with respx.mock() as router:
            router.get("https://vk.ru/fin_dolm").mock(
                return_value=httpx.Response(200, text=_fixture("personal_page.html"))
            )
            return await resolve_vk_object("https://vk.ru/fin_dolm")

    result = _run(scenario())
    assert result == ResolvedVkObject(numeric_id=808632468, kind="personal")
    assert result is not None
    assert result.canonical_url == "https://vk.com/id808632468"


def test_community_with_decoy_user_id_resolves_by_owner_id_alone() -> None:
    """Сообщество несёт decoy `"user_id":100`, не связанный с owner_id — резолвер
    его не видит вовсе (не запасной маркер), решает только `owner_id`."""

    async def scenario() -> ResolvedVkObject | None:
        with respx.mock() as router:
            router.get("https://vk.com/djbeauty").mock(
                return_value=httpx.Response(200, text=_fixture("community.html"))
            )
            return await resolve_vk_object("https://vk.com/djbeauty")

    result = _run(scenario())
    assert result == ResolvedVkObject(numeric_id=228817082, kind="community")
    assert result is not None
    assert result.canonical_url == "https://vk.com/club228817082"


def test_only_zero_owner_id_returns_none() -> None:
    """Ни одного ненулевого `owner_id` — деградация в `None`, без угадывания по
    `user_id` (тот на странице сообщества оказался decoy, доверять ему нельзя)."""

    async def scenario() -> ResolvedVkObject | None:
        with respx.mock() as router:
            router.get("https://vk.com/loading").mock(
                return_value=httpx.Response(200, text=_fixture("only_zero_owner_id.html"))
            )
            return await resolve_vk_object("https://vk.com/loading")

    assert _run(scenario()) is None


def test_page_without_marker_returns_none() -> None:
    async def scenario() -> ResolvedVkObject | None:
        with respx.mock() as router:
            router.get("https://vk.com/deleted_page").mock(
                return_value=httpx.Response(200, text=_fixture("no_marker.html"))
            )
            return await resolve_vk_object("https://vk.com/deleted_page")

    assert _run(scenario()) is None


def test_404_returns_none() -> None:
    async def scenario() -> ResolvedVkObject | None:
        with respx.mock() as router:
            router.get("https://vk.com/gone").mock(return_value=httpx.Response(404))
            return await resolve_vk_object("https://vk.com/gone")

    assert _run(scenario()) is None


def test_timeout_returns_none() -> None:
    async def scenario() -> ResolvedVkObject | None:
        with respx.mock() as router:
            router.get("https://vk.com/slow").mock(side_effect=httpx.ConnectTimeout("boom"))
            return await resolve_vk_object("https://vk.com/slow")

    assert _run(scenario()) is None


def test_non_vk_host_returns_none_without_request() -> None:
    async def scenario() -> tuple[ResolvedVkObject | None, bool]:
        with respx.mock(assert_all_called=False) as router:
            route = router.get("https://ok.ru/group/123").mock(
                return_value=httpx.Response(200, text="irrelevant")
            )
            result = await resolve_vk_object("https://ok.ru/group/123")
            return result, route.called

    result, called = _run(scenario())
    assert result is None
    assert called is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://vk.com/club228817082", ResolvedVkObject(228817082, "community")),
        ("https://vk.com/id5", ResolvedVkObject(5, "personal")),
    ],
)
def test_numeric_address_resolves_without_network(url: str, expected: ResolvedVkObject) -> None:
    async def scenario() -> tuple[ResolvedVkObject | None, bool]:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(url).mock(return_value=httpx.Response(200, text="unused"))
            result = await resolve_vk_object(url)
            return result, route.called

    result, called = _run(scenario())
    assert result == expected
    assert called is False
