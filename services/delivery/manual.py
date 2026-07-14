"""Manual-канал: адаптер, который «доставку» делает силами оператора.

Для телефона автоматическая отправка невозможна — просто отдаём готовый текст,
оператор пересылает по SMS/мессенджеру. Считаем это успешной доставкой в наших
терминах (инвайт в БД помечается `sent`), потому что оператор получил инструмент.
"""

from __future__ import annotations

from services.contact import Contact
from services.delivery.base import DeliveryChannel, DeliveryResult


class ManualDelivery:
    """Всегда возвращает ok=True с fallback_text — оператор пересылает вручную."""

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        del contact  # адрес не нужен — сообщение получит оператор, не клиент напрямую
        return DeliveryResult(
            ok=True,
            channel=DeliveryChannel.MANUAL,
            fallback_text=invite_text,
        )
