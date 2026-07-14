"""Интерфейс доставки ссылки на бриф (spec 2026-07-13 §5).

`DeliveryAdapter` — Protocol, реализуют `Telegram/Smtp/Manual` адаптеры.
`DeliveryResult` — единая форма ответа: успех/ошибка, канал, текст-фолбэк
для ручной пересылки (всегда для manual, при ошибке — для остальных).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from services.contact import Contact


class DeliveryChannel(Enum):
    """Через какой канал попыталась пройти отправка."""

    TELEGRAM = "telegram"
    EMAIL = "email"
    MANUAL = "manual"


@dataclass(frozen=True)
class DeliveryResult:
    """Итог попытки отправки.

    - `ok=True`: доставлено успешно (или `channel=manual` — оператор получит текст).
    - `ok=False`: канал сорвался; `fallback_text` заполнен, оператор пересылает
      вручную, `error` — короткий код из §9 спеки (напр. `username_not_occupied`).
    """

    ok: bool
    channel: DeliveryChannel
    fallback_text: str | None = None
    error: str | None = None


class DeliveryError(Exception):
    """Исключение доставки. Адаптеры ловят и возвращают DeliveryResult(ok=False)."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class DeliveryAdapter(Protocol):
    """Контракт адаптера доставки. Реализации не бросают исключений наружу —
    любые ошибки конвертируются в DeliveryResult(ok=False).
    """

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult: ...
