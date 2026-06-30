"""Magic-link токены для входа клиента в мини-кабинет (Блок 2).

Подписанный HMAC-токен, привязанный к client_id и сроку жизни. Без внешних
зависимостей. Секрет — `settings.secret_key`. Отправка ссылки на email/в Telegram —
отдельная доставка (email требует SMTP; пока бот/оператор пересылает ссылку).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # неделя


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def generate_token(client_id: int, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Сгенерировать подписанный токен для client_id со сроком жизни."""
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{client_id}:{expires_at}"
    raw = f"{payload}:{_sign(payload, secret)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_token(token: str, secret: str) -> int | None:
    """Проверить токен; вернуть client_id или None (невалиден/просрочен/подделан)."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    parts = raw.split(":")
    if len(parts) != 3:
        return None
    client_id_str, expires_str, signature = parts
    expected = _sign(f"{client_id_str}:{expires_str}", secret)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        if int(expires_str) < int(time.time()):
            return None
        return int(client_id_str)
    except ValueError:
        return None
