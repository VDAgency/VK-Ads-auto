"""Маппинг исключений Telethon в короткие коды ошибок отправки (spec §6, §9).

Коды совпадают с теми, что ждёт `services/delivery/telegram.py` на стороне ядра.
Неизвестные исключения сводим к `userbot_unreachable` — предсказуемый UX оператору.
"""

from __future__ import annotations

from telethon import errors

# Telethon-исключение → код ошибки из §9 спеки.
_ERROR_MAP: dict[type[Exception], str] = {
    errors.UsernameNotOccupiedError: "username_not_occupied",
    errors.UsernameInvalidError: "username_invalid",
    errors.UserPrivacyRestrictedError: "privacy_restricted",
    errors.PeerFloodError: "peer_flood",
    errors.AuthKeyUnregisteredError: "session_expired",
}


def map_send_error(exc: Exception) -> str:
    """Вернуть код ошибки §9 для исключения Telethon при отправке.

    Точное совпадение типа, затем проверка по иерархии (подклассы). Всё незнакомое —
    `userbot_unreachable`.
    """
    code = _ERROR_MAP.get(type(exc))
    if code is not None:
        return code
    for exc_type, mapped in _ERROR_MAP.items():
        if isinstance(exc, exc_type):
            return mapped
    return "userbot_unreachable"
