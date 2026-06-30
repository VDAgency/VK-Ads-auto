"""VkApiAdapter — прямой VK Ads API (myTarget v2) через httpx.

Эндпоинты и поля — по docs/VK_API_REFERENCE.md (`ad_plans`→`ad_groups`→`banners`,
цель `socialengagement`, статистика). Часть полей помечена в справке «verify» —
сверять в песочнице перед боевыми мутациями (привязка сообщества, package_id).
Адаптер мутаций НЕ вызывается автоматически; запуск идёт через оркестрацию, которую
мы контролируем. Токен берётся из per-account конфигурации.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from integrations.adapter import PlatformAdapter

BASE_URL = "https://ads.vk.com/api/v2"


class VkApiAdapter(PlatformAdapter):
    """Адаптер прямого VK Ads API. `client` можно подменить (тесты/моки)."""

    def __init__(self, access_token: SecretStr, *, client: httpx.AsyncClient | None = None) -> None:
        self._token = access_token
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{BASE_URL}{path}"
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, **kwargs)
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def health_check(self) -> bool:
        """Read-only проверка: GET /user.json (scope read_user_info)."""
        try:
            response = await self._request("GET", "/user.json")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        """Создать клиентский кабинет (агентство). Тело — см. справку (verify)."""
        response = await self._request("POST", "/agency/clients.json", json={"name": client_ref})
        response.raise_for_status()
        return str(response.json()["id"])

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        """Создать кампанию (ad_plan) с целью (например, socialengagement)."""
        body = {"name": f"plan-{cabinet_id}", "objective": goal}
        response = await self._request("POST", "/ad_plans.json", json=body)
        response.raise_for_status()
        return str(response.json()["id"])

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        """Загрузить статичный креатив (multipart) и вернуть content id."""
        content = await asyncio.to_thread(Path(creative_ref).read_bytes)
        files = {"file": ("creative", content)}
        response = await self._request("POST", "/content/static.json", files=files)
        response.raise_for_status()
        return str(response.json()["id"])

    async def launch(self, campaign_id: str) -> None:
        """Перевести кампанию в активное состояние."""
        response = await self._request(
            "POST", f"/ad_plans/{campaign_id}.json", json={"status": "active"}
        )
        response.raise_for_status()

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        """Снять сводную статистику кампании (base-метрики)."""
        response = await self._request(
            "GET", "/statistics/ad_plans/summary.json", params={"id": campaign_id}
        )
        response.raise_for_status()
        return _parse_summary(response.json())


def _parse_summary(payload: dict[str, Any]) -> dict[str, float]:
    """Достать base-метрики из ответа статистики VK (показы/клики/расход/CTR)."""
    items = payload.get("items") or []
    if not items:
        return {}
    base = (items[0].get("total") or {}).get("base") or {}
    metrics = ("shows", "clicks", "spent", "ctr", "cpc", "cpm", "goals")
    return {key: float(base[key]) for key in metrics if key in base}
