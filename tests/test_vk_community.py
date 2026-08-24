"""Клиент classic VK API для сообществ (`integrations/vk_community.py`).

Формы ответов — из разведки 2026-08-24 (`groups.getCallbackServers`). Сетевую
ошибку и `error` в теле VK отдаём наружу явно: успех проверки Senler не
имитируем (CLAUDE.md §7).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from integrations.vk_community import (
    BASE_URL,
    VkCommunityUnreachable,
    fetch_callback_servers,
    fetch_own_community,
)

URL = f"{BASE_URL}/groups.getCallbackServers"
IDENTITY_URL = f"{BASE_URL}/groups.getById"

LIVE_RESPONSE = {
    "response": {
        "count": 1,
        "items": [
            {
                "id": 2,
                "title": "Senler",
                "creator_id": -228817082,
                "url": "https://callback.senler.ru/webhook/vk/1078808",
                "secret_key": "not-a-real-secret",
                "status": "ok",
            }
        ],
    }
}


@respx.mock
def test_returns_the_list_of_callback_servers() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=LIVE_RESPONSE))
    servers = asyncio.run(fetch_callback_servers("token", "228817082"))
    assert servers[0]["url"] == "https://callback.senler.ru/webhook/vk/1078808"
    assert servers[0]["status"] == "ok"


@respx.mock
def test_sends_group_id_and_access_token() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=LIVE_RESPONSE))
    asyncio.run(fetch_callback_servers("secret-token", "228817082"))
    params = route.calls.last.request.url.params
    assert params["group_id"] == "228817082"
    assert params["access_token"] == "secret-token"


@respx.mock
def test_empty_response_returns_empty_list() -> None:
    empty = {"response": {"count": 0, "items": []}}
    respx.get(URL).mock(return_value=httpx.Response(200, json=empty))
    assert asyncio.run(fetch_callback_servers("token", "1")) == []


@respx.mock
def test_vk_error_body_is_not_silently_treated_as_success() -> None:
    """VK ответил `error` (например, неверный токен) — не имитируем пустой успех."""
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, json={"error": {"error_code": 5, "error_msg": "User authorization failed"}}
        )
    )
    with pytest.raises(VkCommunityUnreachable):
        asyncio.run(fetch_callback_servers("bad-token", "1"))


@respx.mock
def test_http_error_status_is_not_silently_treated_as_success() -> None:
    respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(VkCommunityUnreachable):
        asyncio.run(fetch_callback_servers("token", "1"))


@respx.mock
def test_network_failure_propagates() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(VkCommunityUnreachable):
        asyncio.run(fetch_callback_servers("token", "1"))


# --- groups.getById: сообщество опознаёт себя по токену, без group_id ------------

IDENTITY_RESPONSE = {
    "response": {
        "groups": [
            {"id": 228817082, "name": "DJ BEAUTY", "screen_name": "djbeauty", "type": "group"}
        ]
    }
}


@respx.mock
def test_fetch_own_community_reads_id_screen_name_and_title() -> None:
    respx.get(IDENTITY_URL).mock(return_value=httpx.Response(200, json=IDENTITY_RESPONSE))
    identity = asyncio.run(fetch_own_community("community-token"))
    assert identity.id == "228817082"
    assert identity.screen_name == "djbeauty"
    assert identity.name == "DJ BEAUTY"


@respx.mock
def test_fetch_own_community_sends_no_group_id() -> None:
    """Ключевой момент решения: `group_id` НЕ передаём — VK опознаёт сообщество
    по одному лишь токену."""
    route = respx.get(IDENTITY_URL).mock(return_value=httpx.Response(200, json=IDENTITY_RESPONSE))
    asyncio.run(fetch_own_community("secret-token"))
    params = route.calls.last.request.url.params
    assert "group_id" not in params
    assert params["access_token"] == "secret-token"


@respx.mock
def test_fetch_own_community_lowercases_the_screen_name() -> None:
    payload = {
        "response": {
            "groups": [{"id": 1, "name": "Mixed Case", "screen_name": "DjBeauty", "type": "group"}]
        }
    }
    respx.get(IDENTITY_URL).mock(return_value=httpx.Response(200, json=payload))
    identity = asyncio.run(fetch_own_community("token"))
    assert identity.screen_name == "djbeauty"


@respx.mock
def test_fetch_own_community_falls_back_to_id_when_screen_name_is_missing() -> None:
    payload = {"response": {"groups": [{"id": 42, "name": "Без короткого адреса"}]}}
    respx.get(IDENTITY_URL).mock(return_value=httpx.Response(200, json=payload))
    identity = asyncio.run(fetch_own_community("token"))
    assert identity.screen_name == "42"


@respx.mock
def test_fetch_own_community_raises_when_no_groups_in_response() -> None:
    """Пустой список групп — токен не привязан ни к одному сообществу, не имитируем успех."""
    respx.get(IDENTITY_URL).mock(
        return_value=httpx.Response(200, json={"response": {"groups": []}})
    )
    with pytest.raises(VkCommunityUnreachable):
        asyncio.run(fetch_own_community("token"))


@respx.mock
def test_fetch_own_community_vk_error_is_not_silently_treated_as_success() -> None:
    respx.get(IDENTITY_URL).mock(
        return_value=httpx.Response(
            200, json={"error": {"error_code": 5, "error_msg": "User authorization failed"}}
        )
    )
    with pytest.raises(VkCommunityUnreachable):
        asyncio.run(fetch_own_community("bad-token"))


@respx.mock
def test_fetch_own_community_network_failure_propagates() -> None:
    respx.get(IDENTITY_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(VkCommunityUnreachable):
        asyncio.run(fetch_own_community("token"))
