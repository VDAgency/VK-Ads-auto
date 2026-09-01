"""Авторизация оператора в веб-админке: подписанные HMAC-токены, magic-link + пароль.

Вход в админку даёт БОТ: оператор (Telegram-ID из `OPERATOR_TELEGRAM_IDS`) вызывает
`/admin`, бот локально генерирует admin magic-link (`generate_admin_link`) и шлёт ссылку.
С spec 2026-08-31 доступен и возвратный вход паролем (`/admin/login`,
`services/operator_auth.py`) — тем же Telegram-ID, что подписан в magic-link и сессии.
Ядро проверяет токен/пару и выдаёт session-cookie. Подделать токен без `secret_key`
нельзя, поэтому публичного эндпоинта «выдать ссылку» НЕТ — минтить ссылку может лишь
процесс с секретом (бот/ядро).

Метки purpose (`admlink` / `admsess`) в подписи разделяют magic-link и сессию, а также
отделяют их от клиентских токенов (`services/session_token`, `auth_magiclink`).

Срок сессии — 30 суток (как у клиентского кабинета, `services/session_token.py`).
Про отзыв (аудит 2026-09-01, пункт 4): отзыв ВСЕХ сессий оператора есть —
`sessions_valid_from` (`db.repositories.revoke_operator_sessions`), выставляется
кнопкой в админке и автоматически при смене пароля; `verify_admin_session`/
`verify_admin_link` принимают эту границу через `valid_from` и отклоняют токены,
выпущенные до неё. Помимо этого остаются два способа отозвать доступ ЦЕЛИКОМ по
Telegram ID, оба проверяются в `core/api/v1/admin.py` (`require_admin`, `/login`),
а не здесь:
  1. Убрать ID из `OPERATOR_TELEGRAM_IDS` и перезапустить процесс — прицельно, для
     одного оператора: `is_operator` перестаёт пускать его и по старой сессии, и по
     паролю, даже если подпись/хеш в базе ещё действительны (spec 2026-08-31 —
     закрывает риск «пароль пережил увольнение»).
  2. Сменить `SECRET_KEY` — грубо, для ВСЕХ операторов разом: тогда и все
     magic-link, и все сессии перестают проходить проверку подписи.
Чего по-прежнему нет: отзыва ОДНОЙ сессии по устройству (только все разом).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime

LINK_TTL_SECONDS = 15 * 60  # magic-link входа в админку — 15 минут
SESSION_TTL_SECONDS = 30 * 24 * 3600  # admin-сессия — 30 суток (см. докстринг модуля)
_LINK_PURPOSE = "admlink"
_SESSION_PURPOSE = "admsess"


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


def _generate(purpose: str, operator_id: int, secret: str, ttl_seconds: int) -> str:
    now = int(time.time())
    # Отметка выпуска нужна, чтобы отзыв мог отличить «выдан до» от «выдан после».
    payload = f"{purpose}:{operator_id}:{now + ttl_seconds}:{now}"
    raw = f"{payload}:{_sign(payload, secret)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _verify(
    purpose: str, token: str, secret: str, *, valid_from: datetime | None = None
) -> int | None:
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
    if len(parts) not in (4, 5) or parts[0] != purpose:
        return None
    signature = parts[-1]
    payload = ":".join(parts[:-1])
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return None
    operator_id_str, expires_str = parts[1], parts[2]
    issued_at_str = parts[3] if len(parts) == 5 else None
    try:
        if int(expires_str) < int(time.time()):
            return None
        boundary = _boundary_timestamp(valid_from)
        # Токен старого формата доказать свою свежесть не может — значит, отзыв
        # обязан его отклонить, иначе отзыв ничего не отзывает.
        if boundary is not None and (issued_at_str is None or int(issued_at_str) < boundary):
            return None
        return int(operator_id_str)
    except ValueError:
        return None


def generate_admin_link(operator_id: int, secret: str, ttl_seconds: int = LINK_TTL_SECONDS) -> str:
    """Одноразовая (по TTL) ссылка входа в админку для оператора (генерит бот)."""
    return _generate(_LINK_PURPOSE, operator_id, secret, ttl_seconds)


def verify_admin_link(token: str, secret: str, *, valid_from: datetime | None = None) -> int | None:
    """Проверить admin magic-link; вернуть operator_id или None."""
    return _verify(_LINK_PURPOSE, token, secret, valid_from=valid_from)


def generate_admin_session(
    operator_id: int, secret: str, ttl_seconds: int = SESSION_TTL_SECONDS
) -> str:
    """Session-токен админки (в HttpOnly-cookie после authenticate)."""
    return _generate(_SESSION_PURPOSE, operator_id, secret, ttl_seconds)


def verify_admin_session(
    token: str, secret: str, *, valid_from: datetime | None = None
) -> int | None:
    """Проверить admin session-токен; вернуть operator_id или None."""
    return _verify(_SESSION_PURPOSE, token, secret, valid_from=valid_from)
