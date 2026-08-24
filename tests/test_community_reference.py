"""Разбор ссылки на сообщество для проверки Senler (`services.launch_service._community_reference`).

Ревью 2026-08-24 (дефект 4): устойчивость проверена ревьюером вручную — домены
`vk.com`/`vk.ru`, префиксы `club`/`public`/`event`/`id`, регистр, слэш на конце,
параметры запроса, мусорная строка — но без юнит-тестов ничто не защищает её от
регресса. Функция приватная — тестируем её напрямую, тем же приёмом, что
`_build_adapters`/`_build_router`/`_channel_config` в `tests/test_launch_service.py`.
"""

from __future__ import annotations

import pytest
from services.launch_service import _community_reference


@pytest.mark.parametrize(
    ("url", "expected_numeric_id", "expected_slug"),
    [
        ("https://vk.com/club228817082", "228817082", "club228817082"),
        ("https://vk.ru/club228817082", "228817082", "club228817082"),
        ("https://vk.com/public228817082", "228817082", "public228817082"),
        ("https://vk.com/event228817082", "228817082", "event228817082"),
        ("https://vk.com/id228817082", "228817082", "id228817082"),
        # Главный сценарий брифа на практике: короткий адрес без числового id.
        ("https://vk.ru/djbeauty", None, "djbeauty"),
    ],
)
def test_known_prefixes_on_both_domains(
    url: str, expected_numeric_id: str | None, expected_slug: str
) -> None:
    assert _community_reference(url) == (expected_numeric_id, expected_slug)


def test_is_case_insensitive() -> None:
    assert _community_reference("https://vk.com/CLUB228817082") == ("228817082", "club228817082")
    assert _community_reference("https://vk.ru/DjBeauty") == (None, "djbeauty")


def test_tolerates_a_trailing_slash() -> None:
    assert _community_reference("https://vk.ru/djbeauty/") == (None, "djbeauty")


def test_ignores_query_parameters() -> None:
    assert _community_reference("https://vk.com/club228817082?w=wall-1_2") == (
        "228817082",
        "club228817082",
    )


def test_accepts_a_bare_address_without_scheme() -> None:
    """Бриф иногда приходит без `https://` вовсе."""
    assert _community_reference("vk.ru/djbeauty") == (None, "djbeauty")


def test_garbage_string_does_not_raise() -> None:
    assert _community_reference("not a url at all") == (None, "")


def test_empty_string_does_not_raise() -> None:
    assert _community_reference("") == (None, "")
