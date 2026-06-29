"""Автоопределение типа контакта клиента из ввода оператора (Сценарий A, шаг 1).

Оператор вводит контакт одной строкой; система сама определяет тип
(email / телефон / Telegram-username) и нормализует значение. Используется для
идентификации клиента (PROJECT.md §6) и отправки ссылки на бриф.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ContactType(Enum):
    """Тип контакта клиента."""

    EMAIL = "email"
    PHONE = "phone"
    TELEGRAM = "telegram"


@dataclass(frozen=True)
class Contact:
    """Распознанный контакт: тип + нормализованное значение."""

    type: ContactType
    value: str


class ContactParseError(ValueError):
    """Ввод не распознан как email/телефон/Telegram."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        super().__init__(f"Cannot detect contact type from: {raw!r}")


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_CHARS_RE = re.compile(r"^[\d\s()+\-]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def _normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return "+" + digits


def _normalize_telegram(raw: str) -> str | None:
    stripped = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw, flags=re.IGNORECASE)
    stripped = stripped.lstrip("@")
    return "@" + stripped if _USERNAME_RE.match(stripped) else None


def detect_contact(raw: str) -> Contact:
    """Определить тип контакта и нормализовать. Бросает `ContactParseError`.

    Порядок: email → телефон → Telegram. Телефон — строка только из цифр и
    `+()-` пробелов с 10–11 значащими цифрами; Telegram — `@user`, `t.me/user`
    или голый username (5–32 символа, начинается с буквы).
    """
    value = raw.strip()
    if not value:
        raise ContactParseError(raw)

    if _EMAIL_RE.match(value):
        return Contact(ContactType.EMAIL, value.lower())

    if _PHONE_CHARS_RE.match(value):
        phone = _normalize_phone(value)
        if phone is not None:
            return Contact(ContactType.PHONE, phone)

    telegram = _normalize_telegram(value)
    if telegram is not None:
        return Contact(ContactType.TELEGRAM, telegram)

    raise ContactParseError(raw)
