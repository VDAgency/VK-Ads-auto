"""Session-токен клиентского кабинета (подписанный HMAC, в HttpOnly-cookie).

Отдельно от magic-link (`services/auth_magiclink`): payload начинается с метки
`sess`, поэтому magic-link нельзя предъявить как сессию и наоборот (подпись
покрывает метку). Секрет — `settings.secret_key`. Срок — дольше magic-link
(логин-сессия). Без внешних зависимостей.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # месяц
_PURPOSE = "sess"


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def generate_session(client_id: int, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Выдать session-токен для client_id со сроком жизни."""
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{_PURPOSE}:{client_id}:{expires_at}"
    raw = f"{payload}:{_sign(payload, secret)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_session(token: str, secret: str) -> int | None:
    """Проверить session-токен; вернуть client_id или None (невалиден/просрочен/подделан)."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    parts = raw.split(":")
    if len(parts) != 4 or parts[0] != _PURPOSE:
        return None
    _, client_id_str, expires_str, signature = parts
    expected = _sign(f"{_PURPOSE}:{client_id_str}:{expires_str}", secret)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        if int(expires_str) < int(time.time()):
            return None
        return int(client_id_str)
    except ValueError:
        return None
