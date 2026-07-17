"""Хеширование паролей клиентского кабинета (stdlib PBKDF2, без внешних зависимостей).

Пароль храним ТОЛЬКО как хеш (CLAUDE.md §1.1 / spec кабинета §2). Формат строки:
`pbkdf2_<algo>$<iterations>$<salt_b64>$<hash_b64>`. Выбор PBKDF2 (не argon2/bcrypt) —
решение 2026-07-17: без нативной сборки и без похода в заблокированный pypi.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ALGO = "sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Сгенерировать соль и вернуть строку хеша PBKDF2 для хранения в БД."""
    salt = os.urandom(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, _ITERATIONS)
    return (
        f"pbkdf2_{_ALGO}${_ITERATIONS}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(derived).decode('ascii')}"
    )


def verify_password(password: str, hashed: str) -> bool:
    """Проверить пароль против сохранённого хеша (constant-time сравнение)."""
    try:
        scheme, iterations_str, salt_b64, hash_b64 = hashed.split("$")
        iterations = int(iterations_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    algo = scheme.split("_", 1)[1] if scheme.startswith("pbkdf2_") else _ALGO
    derived = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)
