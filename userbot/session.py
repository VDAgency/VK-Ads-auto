"""Шифрование StringSession через Fernet и хранение в файле (spec §6, §10).

`StringSession` Телетона (строка авторизации Анастасии) — чувствительный секрет.
На диске держим только зашифрованный Fernet-ключом из env вариант, файл — 0600.
Ключ никогда не пишем в логи/код.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SessionStore:
    """Хранилище зашифрованной StringSession на диске.

    - `save(session_str)` — шифрует и пишет в файл с правами 0600.
    - `load()` — читает и расшифровывает; `None`, если файла нет.
    - Неверный ключ при `load()` → `InvalidToken` (пробрасываем — это сбой конфигурации).
    """

    def __init__(self, secret_key: str, path: str) -> None:
        # Fernet сам проверит корректность ключа (base64, 32 байта) при создании.
        self._fernet = Fernet(secret_key.encode("ascii"))
        self._path = Path(path)

    def save(self, session_str: str) -> None:
        """Зашифровать строку сессии и записать в файл (0600)."""
        token = self._fernet.encrypt(session_str.encode("utf-8"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Пишем во временный файл и атомарно заменяем, права — только владельцу.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(token)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self._path)

    def load(self) -> str | None:
        """Прочитать и расшифровать строку сессии; `None`, если файла ещё нет."""
        if not self._path.exists():
            return None
        token = self._path.read_bytes()
        return self._fernet.decrypt(token).decode("utf-8")

    def exists(self) -> bool:
        """Есть ли сохранённая сессия на диске."""
        return self._path.exists()


__all__ = ["SessionStore", "InvalidToken"]
