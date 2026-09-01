"""Session-токен клиентского кабинета (подписанный HMAC, в HttpOnly-cookie).

Отдельно от magic-link (`services/auth_magiclink`): payload начинается с метки
`sess`, поэтому magic-link нельзя предъявить как сессию и наоборот (подпись
покрывает метку). Секрет — `settings.secret_key`. Срок — дольше magic-link
(логин-сессия). Без внешних зависимостей.

Про отзыв (аудит 2026-09-01, пункт 4): токен несёт отметку выпуска, `verify_session`
принимает границу `valid_from` (`db.repositories.revoke_client_sessions`) и отклоняет
токены, выпущенные до неё — так работает «выйти на всех устройствах» для клиента.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime

DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # месяц
_PURPOSE = "sess"


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _boundary_timestamp(valid_from: datetime | None) -> int | None:
    """Границу привести к unix-времени. Наивную дату считаем UTC.

    SQLite в тестах отдаёт `datetime` без зоны, Postgres — с зоной. Без нормализации
    сравнение упало бы с `can't compare offset-naive and offset-aware datetimes`.
    """
    if valid_from is None:
        return None
    aware = valid_from if valid_from.tzinfo is not None else valid_from.replace(tzinfo=UTC)
    return int(aware.timestamp())


def generate_session(client_id: int, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Выдать session-токен для client_id со сроком жизни."""
    now = int(time.time())
    # Отметка выпуска нужна, чтобы отзыв мог отличить «выдан до» от «выдан после».
    payload = f"{_PURPOSE}:{client_id}:{now + ttl_seconds}:{now}"
    raw = f"{payload}:{_sign(payload, secret)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_session(token: str, secret: str, *, valid_from: datetime | None = None) -> int | None:
    """Проверить session-токен; вернуть client_id или None (невалиден/просрочен/подделан)."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        # Раунд-трип: `urlsafe_b64decode` молча отбрасывает мусор ПОСЛЕ валидного
        # padding (`=`), а не отвергает его — иначе токен + произвольный хвост
        # декодировался бы как оригинал и проходил бы проверку подписи.
        if base64.urlsafe_b64encode(raw.encode()).decode() != token:
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    parts = raw.split(":")
    # 5 частей — текущий формат; 4 — выпущенный до появления отзыва (2026-09-01).
    if len(parts) not in (4, 5) or parts[0] != _PURPOSE:
        return None
    signature = parts[-1]
    payload = ":".join(parts[:-1])
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return None
    client_id_str, expires_str = parts[1], parts[2]
    issued_at_str = parts[3] if len(parts) == 5 else None
    try:
        if int(expires_str) < int(time.time()):
            return None
        boundary = _boundary_timestamp(valid_from)
        # Токен старого формата доказать свою свежесть не может — значит, отзыв
        # обязан его отклонить, иначе отзыв ничего не отзывает.
        if boundary is not None and (issued_at_str is None or int(issued_at_str) < boundary):
            return None
        return int(client_id_str)
    except ValueError:
        return None
