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


def brief_url_with_token(variant: BriefVariant, token: str, base_url: str) -> str:
    """Ссылка на форму брифа с вшитым токеном инвайта (`?t=...`).

    Форма читает `t` из query и присылает его в `POST /api/v1/briefs`, чтобы
    ядро связало бриф с инвайтом и пометило его `received` (spec §4, §7).
    """
    return f"{brief_url(variant, base_url)}?t={token}"


def _invite_text(url: str) -> str:
    return (
        "Здравствуйте! Для запуска рекламы заполните, пожалуйста, короткий бриф "
        f"(пара минут): {url}"
    )


def compose_invite(variant: BriefVariant, base_url: str) -> str:
    """Текст-приглашение для клиента со ссылкой на бриф (без токена)."""
    return _invite_text(brief_url(variant, base_url))


def invite_text_with_token(variant: BriefVariant, token: str, base_url: str) -> str:
    """Текст-приглашение со ссылкой, содержащей токен инвайта."""
    return _invite_text(brief_url_with_token(variant, token, base_url))
