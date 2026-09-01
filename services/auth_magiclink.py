"""Magic-link токены для входа клиента в мини-кабинет (Блок 2).

Подписанный HMAC-токен, привязанный к client_id и сроку жизни. Без внешних
зависимостей. Секрет — `settings.secret_key`. Отправка ссылки на email/в Telegram —
отдельная доставка (email требует SMTP; пока бот/оператор пересылает ссылку).

Про отзыв (аудит 2026-09-01, пункт 4): токен несёт отметку выпуска, `verify_token`
принимает границу `valid_from` (`db.repositories.revoke_client_sessions`) и отклоняет
токены, выпущенные до неё. У этого модуля нет метки purpose в payload (в отличие от
`services/session_token`/`admin_auth`) — так было и раньше, здесь это не меняется.
Формат НЕ менять (уже выданные клиентам ссылки должны продолжать работать): вместо
метки purpose границу с токенами других модулей держит требование в `verify_token` —
первое поле обязано парситься как целое число (см. комментарий у проверки).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime

DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # неделя


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


def generate_token(client_id: int, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Сгенерировать подписанный токен для client_id со сроком жизни."""
    now = int(time.time())
    # Отметка выпуска нужна, чтобы отзыв мог отличить «выдан до» от «выдан после».
    payload = f"{client_id}:{now + ttl_seconds}:{now}"
    raw = f"{payload}:{_sign(payload, secret)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_token(token: str, secret: str, *, valid_from: datetime | None = None) -> int | None:
    """Проверить токен; вернуть client_id или None (невалиден/просрочен/подделан)."""
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
    # 4 части — текущий формат; 3 — выпущенный до появления отзыва (2026-09-01).
    if len(parts) not in (3, 4):
        return None
    # У этого модуля нет метки purpose в payload, поэтому явно требуем: первое поле —
    # целое число (client_id). Без этого барьера легаси-токен сессии кабинета
    # (`sess:client_id:expires:signature`, тоже 4 части) или admin-токена проходит
    # ПРОВЕРКУ ПОДПИСИ здесь же — секрет общий у всех модулей, а подписанная строка
    # совпадает буквально. Раньше он отсеивался только случайно, на ветке "истёк":
    # то, что оказывалось на месте expires, обычно парсится как маленькое число и
    # трактуется как просроченная дата. Эта проверка убирает случайность — отвергаем
    # сразу, не полагаясь на удачное совпадение полей. Убирать её нельзя.
    try:
        int(parts[0])
    except ValueError:
        return None
    signature = parts[-1]
    payload = ":".join(parts[:-1])
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return None
    client_id_str, expires_str = parts[0], parts[1]
    issued_at_str = parts[2] if len(parts) == 4 else None
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
