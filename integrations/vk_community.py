"""Тонкий клиент классического VK API (`api.vk.com/method`) для сообществ.

Не путать с `integrations/vk_api.py` — тот ходит в VK Ads API (`ads.vk.com/api/v2`)
токеном рекламного кабинета. Здесь — токен доступа САМОГО СООБЩЕСТВА (выпускает
администратор сообщества в его настройках, годится только для одного
сообщества); единственная цель — спросить `groups.getCallbackServers` и по ответу
понять, подключён ли к сообществу чат-бот Senler (`services/senler.py`).

Ключ API Senler для этого не нужен вовсе — разведка 2026-08-24 (см.
docs/superpowers/specs/2026-08-24-senler-and-block2-gaps-design.md §0). Формат
живого ответа:

    {"response": {"count": 1, "items": [
        {"id": 2, "title": "Senler", "creator_id": -228817082,
         "url": "https://callback.senler.ru/webhook/vk/1078808",
         "secret_key": "…", "status": "ok"}
    ]}}

`secret_key` — секрет самого callback-сервера; этот модуль его не трогает и
никуда не передаёт дальше сырого списка `items`.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://api.vk.com/method"

# Версия classic VK API. У Ads API (integrations/vk_api.py) версии нет — другой,
# REST-контракт (ads.vk.com/api/v2); этот параметр нужен только здесь.
API_VERSION = "5.199"

_TIMEOUT = 15.0


class VkCommunityError(Exception):
    """Базовая ошибка обращения к classic VK API за данными сообщества."""


class VkCommunityUnreachable(VkCommunityError):
    """Проверить подключение не удалось: сеть, HTTP-ошибка или `error` в теле VK.

    Сетевую ошибку отдаём наружу как есть — успех проверки Senler не
    имитируем (CLAUDE.md §7). Фиктивный «нет серверов» здесь не возвращаем:
    иначе временный отказ VK выглядел бы как «Senler не подключён», хотя на
    самом деле проверка просто не состоялась — вызывающая сторона
    (`services/launch_service.py`) сама решает, как об этом честно сказать
    оператору.
    """


async def fetch_callback_servers(
    token: str,
    community_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """`groups.getCallbackServers` — список callback-серверов сообщества.

    Токен нигде не логируется и не появляется в тексте исключений.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await http.get(
            f"{BASE_URL}/groups.getCallbackServers",
            params={"group_id": community_id, "access_token": token, "v": API_VERSION},
        )
    except httpx.HTTPError as exc:
        raise VkCommunityUnreachable(f"VK request failed: {type(exc).__name__}") from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code >= 400:
        raise VkCommunityUnreachable(f"VK returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise VkCommunityUnreachable("VK returned a non-JSON body") from exc
    if not isinstance(payload, dict):
        raise VkCommunityUnreachable("VK returned an unexpected body")

    if "error" in payload:
        error = payload["error"]
        message = error.get("error_msg", "unknown error") if isinstance(error, dict) else error
        raise VkCommunityUnreachable(f"VK API error: {message}")

    items = payload.get("response", {}).get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


__all__ = [
    "API_VERSION",
    "BASE_URL",
    "VkCommunityError",
    "VkCommunityUnreachable",
    "fetch_callback_servers",
]
