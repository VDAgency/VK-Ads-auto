"""Авторизация оператора в веб-админке (подписанные HMAC-токены, без пароля).

Вход в админку выдаёт БОТ: оператор (Telegram-ID из `OPERATOR_TELEGRAM_IDS`) вызывает
`/admin`, бот локально генерирует admin magic-link (`generate_admin_link`) и шлёт ссылку.
Ядро только проверяет токен (`/admin/authenticate`) и выдаёт session-cookie. Подделать
токен без `secret_key` нельзя, поэтому публичного эндпоинта «выдать ссылку» НЕТ — минтить
ссылку может лишь процесс с секретом (бот/ядро).

Метки purpose (`admlink` / `admsess`) в подписи разделяют magic-link и сессию, а также
отделяют их от клиентских токенов (`services/session_token`, `auth_magiclink`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

LINK_TTL_SECONDS = 15 * 60  # magic-link входа в админку — 15 минут
SESSION_TTL_SECONDS = 12 * 3600  # admin-сессия — 12 часов
_LINK_PURPOSE = "admlink"
_SESSION_PURPOSE = "admsess"


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _generate(purpose: str, operator_id: int, secret: str, ttl_seconds: int) -> str:
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{purpose}:{operator_id}:{expires_at}"
    raw = f"{payload}:{_sign(payload, secret)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _verify(purpose: str, token: str, secret: str) -> int | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    parts = raw.split(":")
    if len(parts) != 4 or parts[0] != purpose:
        return None
    _, operator_id_str, expires_str, signature = parts
    expected = _sign(f"{purpose}:{operator_id_str}:{expires_str}", secret)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        if int(expires_str) < int(time.time()):
            return None
        return int(operator_id_str)
    except ValueError:
        return None


def generate_admin_link(operator_id: int, secret: str, ttl_seconds: int = LINK_TTL_SECONDS) -> str:
    """Одноразовая (по TTL) ссылка входа в админку для оператора (генерит бот)."""
    return _generate(_LINK_PURPOSE, operator_id, secret, ttl_seconds)


def verify_admin_link(token: str, secret: str) -> int | None:
    """Проверить admin magic-link; вернуть operator_id или None."""
    return _verify(_LINK_PURPOSE, token, secret)


def generate_admin_session(
    operator_id: int, secret: str, ttl_seconds: int = SESSION_TTL_SECONDS
) -> str:
    """Session-токен админки (в HttpOnly-cookie после authenticate)."""
    return _generate(_SESSION_PURPOSE, operator_id, secret, ttl_seconds)


def verify_admin_session(token: str, secret: str) -> int | None:
    """Проверить admin session-токен; вернуть operator_id или None."""
    return _verify(_SESSION_PURPOSE, token, secret)
