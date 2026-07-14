"""Роутер доставки: по типу контакта выбирает адаптер (spec 2026-07-13 §5).

Правило детерминированное: email→SMTP, telegram→userbot, phone→manual. Роутер
не знает про Settings — конкретные адаптеры инжектятся в конструктор, чтобы
тесты могли подсовывать моки без обращения к окружению.
"""

from __future__ import annotations

from services.contact import Contact, ContactType
from services.delivery.base import DeliveryAdapter


class DeliveryRouter:
    """Возвращает адаптер по типу контакта. Никакой бизнес-логики поверх."""

    def __init__(
        self,
        telegram: DeliveryAdapter,
        email: DeliveryAdapter,
        manual: DeliveryAdapter,
    ) -> None:
        self._by_type = {
            ContactType.TELEGRAM: telegram,
            ContactType.EMAIL: email,
            ContactType.PHONE: manual,
        }

    def route(self, contact: Contact) -> DeliveryAdapter:
        try:
            return self._by_type[contact.type]
        except KeyError as exc:  # pragma: no cover — enum закрыт
            raise ValueError(f"No delivery adapter for contact type {contact.type}") from exc
