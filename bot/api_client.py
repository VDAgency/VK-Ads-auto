"""HTTP-клиент бота к внутреннему API ядра.

Бот — тонкий клиент: за данными ходит сюда, не в БД (§1.3 CLAUDE.md). Ошибки сети
и 5xx превращаются в `CoreUnavailable`, чтобы хендлеры показали дружелюбную
заглушку вместо падения.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from config.settings import get_settings

_TIMEOUT = httpx.Timeout(10.0)


class CoreUnavailable(RuntimeError):
    """Ядро недоступно (сеть/таймаут/5xx) — показать заглушку оператору."""


@dataclass(frozen=True, slots=True)
class InviteItem:
    """Строка трекинга брифа (зеркало `InviteItem` ядра)."""

    contact: str
    variant: str
    channel: str
    sent_at: str | None
    received_at: str | None
    waiting_days: int


@dataclass(frozen=True, slots=True)
class CabinetItem:
    """Карточка кабинета (зеркало `CabinetItem` ядра)."""

    id: str
    name: str
    status: str
    launched_at: str
    is_mock: bool


@dataclass(frozen=True, slots=True)
class CabinetStats:
    """Метрики кабинета за период (зеркало `StatsOut` ядра)."""

    cabinet_id: str
    period: str
    shows: float
    clicks: float
    spent: float
    results: float
    ctr: float
    cpc: float
    is_mock: bool


def _base_url() -> str:
    return get_settings().core_base_url.rstrip("/")


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET к ядру; сетевые ошибки/5xx → `CoreUnavailable`."""
    url = f"{_base_url()}/api/v1{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    result: dict[str, Any] = response.json()
    return result


def _to_invites(payload: dict[str, Any]) -> list[InviteItem]:
    return [InviteItem(**item) for item in payload.get("items", [])]


async def get_pending() -> list[InviteItem]:
    """Инвайты, доставленные клиенту, но ещё не вернувшиеся брифом."""
    return _to_invites(await _get("/invites", {"status": "pending"}))


async def get_recent() -> list[InviteItem]:
    """Инвайты, по которым бриф пришёл за последнюю неделю."""
    return _to_invites(await _get("/invites", {"status": "recent"}))


async def get_cabinets() -> list[CabinetItem]:
    """Список рекламных кабинетов (реальные или демо)."""
    payload = await _get("/cabinets")
    return [CabinetItem(**item) for item in payload.get("items", [])]


async def get_cabinet_stats(cabinet_id: str, period: str) -> CabinetStats:
    """Метрики кабинета за период (`all`/`month`/`week`)."""
    payload = await _get(f"/cabinets/{cabinet_id}/stats", {"period": period})
    return CabinetStats(**payload)
