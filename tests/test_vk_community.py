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
from integrations.vk_community import BASE_URL, VkCommunityUnreachable, fetch_callback_servers

URL = f"{BASE_URL}/groups.getCallbackServers"

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
