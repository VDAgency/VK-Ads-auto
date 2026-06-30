"""Сборка приглашения на бриф: ссылка на нужную форму + канал доставки.

Чистая логика (для бота). Авто-отправка на email требует SMTP (пока не настроен) —
бот отдаёт оператору готовый текст и канал, дальше отправка/пересылка.
"""

from __future__ import annotations

from enum import Enum

from services.brief_parser import BriefVariant
from services.contact import Contact, ContactType

_BRIEF_PATH = {
    BriefVariant.INDIVIDUAL: "/brief-individual.html",
    BriefVariant.COMMUNITY: "/brief-community.html",
}


class DeliveryChannel(Enum):
    """Как доставить ссылку клиенту."""

    EMAIL = "email"
    TELEGRAM = "telegram"
    MANUAL = "manual"  # телефон — оператор отправляет вручную


def brief_url(variant: BriefVariant, base_url: str) -> str:
    """Ссылка на форму брифа нужного варианта."""
    return base_url.rstrip("/") + _BRIEF_PATH[variant]


def delivery_channel(contact: Contact) -> DeliveryChannel:
    """Определить канал доставки по типу контакта."""
    if contact.type is ContactType.EMAIL:
        return DeliveryChannel.EMAIL
    if contact.type is ContactType.TELEGRAM:
        return DeliveryChannel.TELEGRAM
    return DeliveryChannel.MANUAL


def compose_invite(variant: BriefVariant, base_url: str) -> str:
    """Текст-приглашение для клиента со ссылкой на бриф."""
    url = brief_url(variant, base_url)
    return (
        "Здравствуйте! Для запуска рекламы заполните, пожалуйста, короткий бриф "
        f"(пара минут): {url}"
    )
