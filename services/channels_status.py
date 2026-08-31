"""Состояние каналов доставки для веб-админки (`GET /api/v1/admin/channels`).

Юзербот и kotbot — отдельные внешние сервисы, каждый со своим `USERBOT_BASE_URL`/
`KOTBOT_BASE_URL`, а не ядро: те же источники, что бот опрашивает командами
`/userbot_status` и `/kotbot` (`bot/handlers/userbot_status.py`,
`bot/handlers/link_kotbot.py`), но здесь — код ядра, не бота (CLAUDE.md §1.3:
`bot/api_client.py` — HTTP-клиент бота К ЯДРУ, ядро на него ссылаться не может).

Веб получает только просмотр состояния: подключение аккаунта (код из SMS,
пароль 2FA) сознательно остаётся в боте. Внешний сервис может быть недоступен —
это не ошибка ядра, поэтому таймауты короткие и ни одна сетевая проблема здесь
не бросает исключение: вызывающая сторона (роутер) обязана всегда ответить 200
с честным описанием состояния, а не 500.

Телефон в ответ попадает только замаскированным (`services.userbot_report.mask_phone`).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from config.settings import Settings, get_settings
from integrations.kotbot_http import KotbotAdapter

from services.userbot_report import mask_phone

# Чтение сессий читается из памяти сервиса — дёшево, но сервис может не отвечать
# вовсе (упал/перезапускается): таймаут короткий, чтобы экран оператора не висел.
_USERBOT_STATUS_TIMEOUT = httpx.Timeout(5.0)


@dataclass(frozen=True, slots=True)
class UserbotSessionStatus:
    """Одна сессия юзербота для показа оператору — без полного номера."""

    sender_id: int
    authorized: bool
    unreachable: bool
    phone_masked: str | None


@dataclass(frozen=True, slots=True)
class UserbotChannelStatus:
    """Состояние юзербот-сервиса целиком."""

    configured: bool
    available: bool
    sessions: tuple[UserbotSessionStatus, ...]


@dataclass(frozen=True, slots=True)
class KotbotChannelStatus:
    """Состояние kotbot-сервиса: настроен ли и отвечает ли health-check."""

    configured: bool
    healthy: bool


@dataclass(frozen=True, slots=True)
class ChannelsStatus:
    """Состояние обоих каналов доставки — веб-зеркало `/userbot_status` + `/kotbot`."""

    userbot: UserbotChannelStatus
    kotbot: KotbotChannelStatus


async def _fetch_userbot_sessions(base_url: str) -> tuple[UserbotSessionStatus, ...] | None:
    """`GET /sessions` юзербот-сервиса. `None` — сервис не ответил (недоступен)."""
    try:
        async with httpx.AsyncClient(timeout=_USERBOT_STATUS_TIMEOUT) as client:
            response = await client.get(f"{base_url}/sessions")
    except (httpx.HTTPError, httpx.TransportError):
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    raw_items = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return ()

    sessions: list[UserbotSessionStatus] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            sender_id = int(item["sender_id"])
        except (KeyError, TypeError, ValueError):
            continue
        state = str(item.get("state", ""))
        phone = item.get("phone")
        sessions.append(
            UserbotSessionStatus(
                sender_id=sender_id,
                authorized=bool(item.get("authorized", False)),
                unreachable=state == "unreachable" or item.get("error") == "unreachable",
                phone_masked=mask_phone(phone) if isinstance(phone, str) and phone else None,
            )
        )
    return tuple(sessions)


async def _userbot_status(base_url: str) -> UserbotChannelStatus:
    if not base_url:
        return UserbotChannelStatus(configured=False, available=False, sessions=())
    sessions = await _fetch_userbot_sessions(base_url)
    return UserbotChannelStatus(
        configured=True,
        available=sessions is not None,
        sessions=sessions or (),
    )


async def _kotbot_status(base_url: str) -> KotbotChannelStatus:
    if not base_url:
        return KotbotChannelStatus(configured=False, healthy=False)
    # `KotbotAdapter.health_check` сама не бросает исключений — любая проблема
    # (сеть, не-2xx, битое тело) сводится к `False`, ровно то, что нужно здесь.
    healthy = await KotbotAdapter(base_url).health_check()
    return KotbotChannelStatus(configured=True, healthy=healthy)


async def channels_status(settings: Settings | None = None) -> ChannelsStatus:
    """Состояние обоих каналов доставки; никогда не бросает исключений."""
    cfg = settings or get_settings()
    userbot = await _userbot_status(cfg.userbot_base_url.rstrip("/"))
    kotbot = await _kotbot_status(cfg.kotbot_base_url.rstrip("/"))
    return ChannelsStatus(userbot=userbot, kotbot=kotbot)


__all__ = [
    "ChannelsStatus",
    "KotbotChannelStatus",
    "UserbotChannelStatus",
    "UserbotSessionStatus",
    "channels_status",
]
