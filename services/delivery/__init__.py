"""Слой доставки ссылки на бриф клиенту (см. spec 2026-07-13 §5).

Три канала — по типу контакта (см. `services.contact.ContactType`):
- `@username` → `TelegramUserbotDelivery` (через сервис `userbot/`)
- email → `SmtpDelivery` (aiosmtplib)
- телефон → `ManualDelivery` (готовый текст для ручной пересылки)

Ядро вызывает `DeliveryRouter.route(contact).send(...)`; конкретика площадок
не течёт в `bot/` и `core/` — паттерн как у `integrations.channels` для VK/kotbot.
"""

from services.delivery.base import (
    DeliveryAdapter,
    DeliveryChannel,
    DeliveryError,
    DeliveryResult,
)
from services.delivery.email import SmtpDelivery
from services.delivery.manual import ManualDelivery
from services.delivery.router import DeliveryRouter
from services.delivery.telegram import TelegramUserbotDelivery

__all__ = [
    "DeliveryAdapter",
    "DeliveryChannel",
    "DeliveryError",
    "DeliveryResult",
    "DeliveryRouter",
    "ManualDelivery",
    "SmtpDelivery",
    "TelegramUserbotDelivery",
]
