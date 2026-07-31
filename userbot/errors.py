"""Классификация исключений Telethon: код ошибки §9 + что делать с сессией.

Одна точка правды для двух решений сразу: какой код вернуть вызывающей стороне и в
какое состояние перевести сессию. Раньше это расходилось — `send()` возвращал
`session_expired`, а мёртвый клиент оставался в кэше, и все следующие отправки бились
об него же.

Главное различие — что ретраить. Сетевые сбои проходят сами; ошибки авторизации не
пройдут никогда, а настойчивые повторы с отозванным ключом Telegram воспринимает хуже,
чем их отсутствие.
"""

from __future__ import annotations

from enum import StrEnum

from telethon import errors

from userbot.state import SessionState


class ErrorClass(StrEnum):
    """Класс ошибки — он же решение о ретраях."""

    NETWORK = "network"  # ретраим, сессия жива
    AUTH_DEAD = "auth_dead"  # НИКОГДА не ретраим, нужна перепривязка
    ACCOUNT = "account"  # НИКОГДА не ретраим, нужен другой аккаунт
    RECIPIENT = "recipient"  # проблема с получателем, состояние сессии не меняем


# Порядок важен: `AuthKeyDuplicatedError` наследует `AuthKeyError`, а НЕ
# `UnauthorizedError`, поэтому общая проверка по иерархии его не поймает — он должен
# стоять раньше. Проверка идёт сверху вниз до первого совпадения.
_RULES: tuple[tuple[type[BaseException], ErrorClass, str], ...] = (
    (errors.UsernameNotOccupiedError, ErrorClass.RECIPIENT, "username_not_occupied"),
    (errors.UsernameInvalidError, ErrorClass.RECIPIENT, "username_invalid"),
    (errors.UserPrivacyRestrictedError, ErrorClass.RECIPIENT, "privacy_restricted"),
    (errors.PeerFloodError, ErrorClass.RECIPIENT, "peer_flood"),
    (errors.FloodWaitError, ErrorClass.RECIPIENT, "flood_wait"),
    (errors.AuthKeyDuplicatedError, ErrorClass.AUTH_DEAD, "session_duplicated"),
    (errors.UserDeactivatedBanError, ErrorClass.ACCOUNT, "account_banned"),
    (errors.UserDeactivatedError, ErrorClass.ACCOUNT, "account_banned"),
    (errors.PhoneNumberBannedError, ErrorClass.ACCOUNT, "account_banned"),
    (errors.AuthKeyUnregisteredError, ErrorClass.AUTH_DEAD, "session_expired"),
    (errors.SessionExpiredError, ErrorClass.AUTH_DEAD, "session_expired"),
    (errors.SessionRevokedError, ErrorClass.AUTH_DEAD, "session_expired"),
    (errors.UnauthorizedError, ErrorClass.AUTH_DEAD, "session_expired"),
    (errors.ServerError, ErrorClass.NETWORK, "userbot_unreachable"),
    (errors.TimedOutError, ErrorClass.NETWORK, "userbot_unreachable"),
    (ConnectionError, ErrorClass.NETWORK, "userbot_unreachable"),
    (TimeoutError, ErrorClass.NETWORK, "userbot_unreachable"),
    (OSError, ErrorClass.NETWORK, "userbot_unreachable"),
)

# Класс ошибки → состояние сессии. `RECIPIENT` здесь отсутствует намеренно: неверный
# получатель ничего не говорит о состоянии самой сессии.
_STATE_BY_CLASS: dict[ErrorClass, SessionState] = {
    ErrorClass.NETWORK: SessionState.UNREACHABLE,
    ErrorClass.AUTH_DEAD: SessionState.EXPIRED,
    ErrorClass.ACCOUNT: SessionState.BANNED,
}


def classify(exc: BaseException) -> tuple[ErrorClass, str]:
    """Класс ошибки и код §9.

    Незнакомое считаем сетевым: так мы в худшем случае лишний раз попробуем, а не
    похороним живую сессию и не отправим оператора на бессмысленную перепривязку.
    """
    for exc_type, error_class, code in _RULES:
        if isinstance(exc, exc_type):
            return error_class, code
    return ErrorClass.NETWORK, "userbot_unreachable"


def state_for(error_class: ErrorClass) -> SessionState | None:
    """Состояние сессии для класса ошибки; `None` — состояние менять не нужно."""
    return _STATE_BY_CLASS.get(error_class)


def map_send_error(exc: Exception) -> str:
    """Код ошибки §9 для отправки (контракт `services/delivery/telegram.py`)."""
    return classify(exc)[1]
