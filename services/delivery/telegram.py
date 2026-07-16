"""Telegram-канал: адаптер, ходящий в userbot-сервис (Telethon) по HTTP.

`userbot/` живёт отдельным контейнером в docker-compose и не смотрит наружу —
только по внутренней compose-сети. Здесь мы только клиентская сторона: POST /send
с телом `{sender_id, username, text}`; маппинг ошибок Telethon в коды §9 спеки.
`sender_id` — Telegram ID оператора, от чьего аккаунта уходит сообщение (сессия
каждого оператора подключается отдельно через /link_userbot).

При любом сбое возвращаем `DeliveryResult(ok=False, fallback_text=<оригинал>)`,
чтобы бот сразу выдал оператору готовый текст для ручной пересылки.
"""

from __future__ import annotations

import httpx

from services.contact import Contact
from services.delivery.base import DeliveryChannel, DeliveryResult

# Отдельный клиент внутри compose-сети — 15с достаточно, ретраев нет
# (см. §4.2 спеки: оператор увидит обратную связь быстро).
_TIMEOUT = httpx.Timeout(15.0)

# Маппинг ответов userbot-сервиса в короткие коды ошибок (§9 спеки).
_ERROR_MESSAGES = {
    "username_not_occupied": "Telegram: пользователь не найден.",
    "username_invalid": "Telegram: неверный формат username.",
    "privacy_restricted": "Telegram: клиент запретил сообщения от незнакомцев.",
    "peer_flood": "Telegram: юзербот временно ограничен (флуд-лимит).",
    "session_expired": "Юзербот разлогинен — перепривяжите через /link_userbot.",
    "sender_not_authorized": "Ваш юзербот не подключён — выполните /link_userbot.",
    "userbot_unreachable": "Сервис отправки недоступен.",
}


class TelegramUserbotDelivery:
    """Отправляет DM клиенту через сервис userbot (Telethon)."""

    def __init__(self, base_url: str, sender_id: int | None = None) -> None:
        # Пустой base_url = userbot не сконфигурирован → любой send падает
        # с userbot_unreachable; sender_id=None = неизвестен отправитель →
        # sender_not_authorized (обрабатывается в send()).
        self._base_url = base_url.rstrip("/")
        self._sender_id = sender_id

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        if not self._base_url:
            return self._failed("userbot_unreachable", invite_text)
        if self._sender_id is None:
            return self._failed("sender_not_authorized", invite_text)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    f"{self._base_url}/send",
                    json={
                        "sender_id": self._sender_id,
                        "username": contact.value,
                        "text": invite_text,
                    },
                )
        except httpx.HTTPError:
            return self._failed("userbot_unreachable", invite_text)

        # userbot возвращает {ok: bool, error?: str}; error — код из §9 спеки.
        try:
            payload = response.json()
        except ValueError:
            return self._failed("userbot_unreachable", invite_text)

        if response.is_success and payload.get("ok") is True:
            return DeliveryResult(ok=True, channel=DeliveryChannel.TELEGRAM)

        error_code = payload.get("error") or "userbot_unreachable"
        # Неизвестные коды сводим к userbot_unreachable — предсказуемый UX оператору.
        if error_code not in _ERROR_MESSAGES:
            error_code = "userbot_unreachable"
        return self._failed(error_code, invite_text)

    @staticmethod
    def _failed(error_code: str, invite_text: str) -> DeliveryResult:
        return DeliveryResult(
            ok=False,
            channel=DeliveryChannel.TELEGRAM,
            fallback_text=invite_text,
            error=error_code,
        )


def human_message(error_code: str) -> str:
    """Русский текст ошибки для показа оператору (используется хендлером бота)."""
    return _ERROR_MESSAGES.get(error_code, "Telegram: неизвестная ошибка.")
