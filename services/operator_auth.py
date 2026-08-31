"""Вход оператора в веб-админку по паролю (в дополнение к magic-link из бота).

Схема — зеркало клиентского кабинета (`services/password.py`, C2 spec
2026-07-17): пароль хранится только как PBKDF2-хеш (`Operator.password_hash`).
Идентификатор оператора здесь — Telegram ID, а не `Operator.id` из БД: именно
Telegram ID подписан в admin-сессии (`core/api/v1/admin.py::require_admin`,
`bot/handlers/admin.py`), значит и пароль привязывается к нему же.

Установка пароля материализует строку оператора лениво через
`get_or_create_operator` — оператор мог до этого входить только по ссылке из
бота и ещё не иметь строки в БД. Проверка пароля, наоборот, ничего не создаёт
(`find_operator_by_telegram_id`): иначе перебор произвольных Telegram ID на
`/admin/login` заводил бы в базе мусорные записи операторов.
"""

from __future__ import annotations

from db.repositories import (
    find_operator_by_telegram_id,
    get_or_create_operator,
    set_operator_password_hash,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.password import hash_password, verify_password

_MIN_PASSWORD_LEN = 10


class WeakPasswordError(ValueError):
    """Пароль короче минимальной длины."""


async def set_operator_password(
    session: AsyncSession, account_id: int, telegram_id: int, password: str
) -> None:
    """Поставить (или сменить) пароль оператора. Короткий пароль — `WeakPasswordError`."""
    if len(password) < _MIN_PASSWORD_LEN:
        raise WeakPasswordError("password_too_short")
    operator = await get_or_create_operator(session, account_id, telegram_id)
    await set_operator_password_hash(session, operator, hash_password(password))


async def authenticate_operator(
    session: AsyncSession, account_id: int, telegram_id: int, password: str
) -> bool:
    """Проверить пару telegram_id + пароль.

    `False` — и для неизвестного оператора, и для оператора без установленного
    пароля: ответ одинаков, чтобы не раскрывать, какой из вариантов имел место.
    """
    operator = await find_operator_by_telegram_id(session, account_id, telegram_id)
    if operator is None or operator.password_hash is None:
        return False
    return verify_password(password, operator.password_hash)
