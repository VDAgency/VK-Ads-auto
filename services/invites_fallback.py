"""Когда предлагать оператору отправить бриф на email вместо Telegram.

Правило одно: предлагаем, когда сорвался КАНАЛ, и не предлагаем, когда неверен сам
КОНТАКТ. Если оператор ошибся в username, правильное действие — ввести контакт заново,
а не подменять способ доставки: письмо уйдёт не тому человеку либо никому.

Отдельный модуль, а не ветка в хендлере: решение чисто логическое и должно
проверяться таблицей, без aiogram и без сети (CLAUDE.md §1.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Отказы канала: сервис лежит, сессия мертва, сеть не пускает, платформа лимитирует.
# Контакт при этом верный, и письмо — осмысленный обходной путь.
CHANNEL_ERRORS: Final[frozenset[str]] = frozenset(
    {
        "userbot_unreachable",
        "session_expired",
        "session_duplicated",
        "sender_not_authorized",
        "account_banned",
        "flood_wait",
        "peer_flood",
        # Клиент закрыт от незнакомцев — контакт верный, Telegram просто не пропускает.
        "privacy_restricted",
    }
)

# Неверный контакт: почта тут не поможет, потому что и адресат под вопросом.
CONTACT_ERRORS: Final[frozenset[str]] = frozenset({"username_not_occupied", "username_invalid"})


@dataclass(frozen=True, slots=True)
class EmailOffer:
    """Предложение отправить бриф письмом.

    `email` — известный адрес клиента; `None` означает «предложить стоит, но адреса
    у нас нет» — оператора нужно попросить его ввести.
    """

    email: str | None


def offer_email(
    status: str, channel: str, error: str | None, client_email: str | None
) -> EmailOffer | None:
    """Предложить ли email-фолбэк. `None` — не предлагать.

    Предлагаем только после неудачной доставки в Telegram: для email-инвайта запасной
    email бессмысленен, а для телефона у оператора и так есть текст для пересылки.
    """
    if status != "failed" or channel != "telegram":
        return None
    if error in CONTACT_ERRORS:
        return None
    if error is not None and error not in CHANNEL_ERRORS:
        # Незнакомый код — не угадываем. Оператор получит обычный текст для пересылки.
        return None
    return EmailOffer(email=client_email or None)
