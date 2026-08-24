"""Тонкий клиент классического VK API (`api.vk.com/method`) для сообществ.

Не путать с `integrations/vk_api.py` — тот ходит в VK Ads API (`ads.vk.com/api/v2`)
токеном рекламного кабинета. Здесь — токен доступа САМОГО СООБЩЕСТВА (выпускает
администратор сообщества в его настройках, годится только для одного
сообщества); две цели — опознать само сообщество по токену (`groups.getById`)
при привязке и спросить `groups.getCallbackServers`, чтобы по ответу понять,
подключён ли к сообществу чат-бот Senler (`services/senler.py`).

Ключ API Senler для этого не нужен вовсе — разведка 2026-08-24 (см.
docs/superpowers/specs/2026-08-24-senler-and-block2-gaps-design.md §0). Формат
живого ответа `groups.getCallbackServers`:

    {"response": {"count": 1, "items": [
        {"id": 2, "title": "Senler", "creator_id": -228817082,
         "url": "https://callback.senler.ru/webhook/vk/1078808",
         "secret_key": "…", "status": "ok"}
    ]}}

`secret_key` — секрет самого callback-сервера; этот модуль его не трогает и
никуда не передаёт дальше сырого списка `items`.

`groups.getById`, вызванный БЕЗ параметра `group_id` — с одним лишь токеном
сообщества, — называет само сообщество, которому этот токен принадлежит (живой
запрос 2026-08-24):

    {"response": {"groups": [
        {"id": 228817082, "name": "DJ BEAUTY", "screen_name": "djbeauty",
         "type": "group"}
    ]}}

Отсюда операторский сценарий привязки токена (`core/api/v1/senler.py`) больше
не спрашивает id сообщества руками — его называет сам VK, тем же приёмом, что
`services/vk_identity.py::fetch_identity` для рекламного кабинета.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    оператору. Тот же принцип для привязки токена (`core/api/v1/senler.py`):
    не смогли опознать сообщество — токен не сохраняем, а не имитируем успех.
    """


@dataclass(frozen=True, slots=True)
class CommunityIdentity:
    """Кто это сообщество — по одному лишь токену, без явного `group_id`."""

    id: str
    screen_name: str
    name: str


async def _request(
    method: str,
    params: dict[str, str],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Сходить в classic VK API и вернуть тело `response` как есть.

    Общая часть `fetch_callback_servers`/`fetch_own_community`: HTTP, JSON,
    `error` в теле — везде одна и та же честная `VkCommunityUnreachable`, без
    догадок по содержимому. Токен нигде не логируется и не появляется в
    тексте исключений (он уходит только параметром запроса).
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await http.get(f"{BASE_URL}/{method}", params=params)
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

    result = payload.get("response", {})
    return result if isinstance(result, dict) else {}


async def fetch_callback_servers(
    token: str,
    community_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """`groups.getCallbackServers` — список callback-серверов сообщества."""
    result = await _request(
        "groups.getCallbackServers",
        {"group_id": community_id, "access_token": token, "v": API_VERSION},
        client=client,
    )
    items = result.get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


async def fetch_own_community(
    token: str, *, client: httpx.AsyncClient | None = None
) -> CommunityIdentity:
    """`groups.getById` БЕЗ `group_id` — сообщество опознаёт себя по токену.

    Используется ДО сохранения токена в БД (`core/api/v1/senler.py`), по
    образцу `services/ad_accounts.py::add_account`: так оператор не вводит id
    сообщества руками (источник ошибок «перепутал сообщество») и мы заодно
    подтверждаем, что токен рабочий, прежде чем его хранить.

    Бросает `VkCommunityUnreachable`, если VK не ответил, вернул ошибку, или
    список сообществ в ответе пуст (пустого «сообщество не опознано» здесь не
    молчим — то же правило, что у `fetch_callback_servers`).
    """
    result = await _request(
        "groups.getById", {"access_token": token, "v": API_VERSION}, client=client
    )
    groups = result.get("groups", [])
    if not isinstance(groups, list) or not groups:
        raise VkCommunityUnreachable("VK response has no community for this token")
    group = groups[0]
    if not isinstance(group, dict) or "id" not in group:
        raise VkCommunityUnreachable("VK response has no community id")

    community_id = str(group["id"])
    screen_name = str(group.get("screen_name") or community_id).strip().lower()
    name = str(group.get("name") or "").strip() or f"Сообщество {community_id}"
    return CommunityIdentity(id=community_id, screen_name=screen_name, name=name)


__all__ = [
    "API_VERSION",
    "BASE_URL",
    "CommunityIdentity",
    "VkCommunityError",
    "VkCommunityUnreachable",
    "fetch_callback_servers",
    "fetch_own_community",
]
