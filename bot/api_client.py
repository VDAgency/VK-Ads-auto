"""HTTP-клиент бота к внутреннему API ядра.

Бот — тонкий клиент: за данными ходит сюда, не в БД (§1.3 CLAUDE.md). Ошибки сети
и 5xx превращаются в `CoreUnavailable`, чтобы хендлеры показали дружелюбную
заглушку вместо падения.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

import httpx
from config.settings import get_settings

_TIMEOUT = httpx.Timeout(10.0)


class CoreUnavailable(RuntimeError):
    """Ядро недоступно (сеть/таймаут/5xx) — показать заглушку оператору."""


class ContactNotRecognized(RuntimeError):
    """Ядро не распознало контакт (422) — оператору нужен корректный ввод."""


class UserbotUnavailable(RuntimeError):
    """Юзербот-сервис не сконфигурирован или недоступен — работаем в мок-режиме."""


class UserbotAuthError(RuntimeError):
    """Юзербот отверг код/пароль (400) — показать оператору причину, дать повтор."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class InviteItem:
    """Строка трекинга брифа (зеркало `InviteItem` ядра)."""

    contact: str
    variant: str
    channel: str
    sent_at: str | None
    received_at: str | None
    waiting_days: int
    contact_name: str | None = None
    brief_id: int | None = None


@dataclass(frozen=True, slots=True)
class InviteCreated:
    """Итог создания инвайта (зеркало `CreateInviteOut` ядра, сценарии §8.1)."""

    invite_id: int
    status: str  # sent | failed
    channel: str  # telegram | email | manual
    fallback_text: str | None
    error: str | None


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


# Создание инвайта включает доставку (userbot до 15с / SMTP до 20с) — таймаут
# заметно больше обычного _TIMEOUT.
_INVITE_TIMEOUT = httpx.Timeout(30.0)


async def create_invite(variant: str, contact: str, operator_telegram_id: int) -> InviteCreated:
    """`POST /invites`: токен, запись BriefInvite, доставка выбранным каналом.

    422 (нераспознанный контакт) → `ContactNotRecognized`; сеть/5xx → `CoreUnavailable`.
    """
    url = f"{_base_url()}/api/v1/invites"
    body = {
        "variant": variant,
        "contact": contact,
        "operator_telegram_id": operator_telegram_id,
    }
    try:
        async with httpx.AsyncClient(timeout=_INVITE_TIMEOUT) as client:
            response = await client.post(url, json=body)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 422:
        raise ContactNotRecognized("contact not recognized")
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    payload = response.json()
    return InviteCreated(
        invite_id=int(payload["invite_id"]),
        status=str(payload["status"]),
        channel=str(payload["channel"]),
        fallback_text=payload.get("fallback_text"),
        error=payload.get("error"),
    )


# --- Юзербот (Telethon-сервис доставки, spec §9) ----------------------------
#
# Ходим напрямую в userbot-сервис (свой BASE_URL), не через ядро. Пустой
# `USERBOT_BASE_URL` = сервис не сконфигурирован → `UserbotUnavailable`
# (хендлер уходит в мок-режим). 400 от auth-эндпоинтов = неверный код/пароль.
# Сессии — по операторам: во все вызовы передаём sender_id (Telegram ID оператора).


@dataclass(frozen=True, slots=True)
class UserbotHealth:
    """Состояние авторизации сессии оператора (зеркало `/health?sender_id=`)."""

    authorized: bool
    phone: str | None = None


def _userbot_base_url() -> str:
    return get_settings().userbot_base_url.rstrip("/")


def userbot_configured() -> bool:
    """Задан ли `USERBOT_BASE_URL` — иначе хендлер работает в мок-режиме."""
    return bool(_userbot_base_url())


async def _userbot_request(
    method: str, path: str, json: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Запрос к userbot-сервису; сеть/5xx → `UserbotUnavailable`, 400 → `UserbotAuthError`."""
    base = _userbot_base_url()
    if not base:
        raise UserbotUnavailable("userbot_base_url is empty")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method, f"{base}{path}", json=json)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise UserbotUnavailable(str(exc)) from exc
    if response.status_code == 400:
        detail = "unknown"
        with contextlib.suppress(ValueError):
            detail = str(response.json().get("detail", "unknown"))
        raise UserbotAuthError(detail)
    if response.status_code >= 500:
        raise UserbotUnavailable(f"userbot {response.status_code}")
    result: dict[str, Any] = response.json()
    return result


async def userbot_status(sender_id: int) -> UserbotHealth:
    """Опрос `/health?sender_id=`: авторизована ли сессия оператора, номер."""
    payload = await _userbot_request("GET", f"/health?sender_id={sender_id}")
    return UserbotHealth(
        authorized=bool(payload.get("authorized", False)),
        phone=payload.get("phone"),
    )


async def userbot_health_all() -> dict[int, bool]:
    """Состояние всех сессий `{sender_id: authorized}` (для фонового поллера)."""
    payload = await _userbot_request("GET", "/health")
    sessions = payload.get("sessions", [])
    return {int(item["sender_id"]): bool(item.get("authorized", False)) for item in sessions}


async def userbot_start_auth(sender_id: int, phone: str) -> str:
    """`/auth/start` — вернуть `phone_code_hash` для последующего ввода кода."""
    payload = await _userbot_request(
        "POST", "/auth/start", {"sender_id": sender_id, "phone": phone}
    )
    return str(payload["phone_code_hash"])


async def userbot_submit_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
    """`/auth/code` — вернуть `needs_password` (нужен ли ввод пароля 2FA)."""
    payload = await _userbot_request(
        "POST",
        "/auth/code",
        {
            "sender_id": sender_id,
            "phone": phone,
            "code": code,
            "phone_code_hash": phone_code_hash,
        },
    )
    return bool(payload.get("needs_password", False))


async def userbot_submit_password(sender_id: int, password: str) -> None:
    """`/auth/password` — завершить авторизацию при включённом 2FA."""
    await _userbot_request("POST", "/auth/password", {"sender_id": sender_id, "password": password})
