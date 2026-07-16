"""Шифрование StringSession через Fernet и хранение в файлах (spec §6, §10).

`StringSession` Телетона (строка авторизации оператора) — чувствительный секрет.
На диске держим только зашифрованный Fernet-ключом из env вариант, файлы — 0600.
Ключ никогда не пишем в логи/код.

Сессий несколько — по одной на оператора (sender_id = Telegram ID): каждый оператор
подключает свой аккаунт через /link_userbot, отправка идёт от имени вызвавшего.
Файлы лежат в одном каталоге: `{sessions_dir}/{sender_id}.session.enc`.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_SUFFIX = ".session.enc"


class SessionStore:
    """Хранилище зашифрованных StringSession на диске, по файлу на оператора.

    - `save(sender_id, session_str)` — шифрует и пишет в файл с правами 0600.
    - `load(sender_id)` — читает и расшифровывает; `None`, если файла нет.
    - `list_senders()` — sender_id всех сохранённых сессий (для /health).
    - Неверный ключ при `load()` → `InvalidToken` (пробрасываем — сбой конфигурации).
    """

    def __init__(self, secret_key: str, sessions_dir: str) -> None:
        # Fernet сам проверит корректность ключа (base64, 32 байта) при создании.
        self._fernet = Fernet(secret_key.encode("ascii"))
        self._dir = Path(sessions_dir)

    def _path(self, sender_id: int) -> Path:
        return self._dir / f"{sender_id}{_SUFFIX}"

    def save(self, sender_id: int, session_str: str) -> None:
        """Зашифровать строку сессии оператора и записать в файл (0600)."""
        token = self._fernet.encrypt(session_str.encode("utf-8"))
        self._dir.mkdir(parents=True, exist_ok=True)
        # Пишем во временный файл и атомарно заменяем, права — только владельцу.
        path = self._path(sender_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(token)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)

    def load(self, sender_id: int) -> str | None:
        """Прочитать и расшифровать сессию оператора; `None`, если файла ещё нет."""
        path = self._path(sender_id)
        if not path.exists():
            return None
        token = path.read_bytes()
        return self._fernet.decrypt(token).decode("utf-8")

    def exists(self, sender_id: int) -> bool:
        """Есть ли сохранённая сессия оператора на диске."""
        return self._path(sender_id).exists()

    def list_senders(self) -> list[int]:
        """Sender_id всех сохранённых сессий (файлы с нечисловым именем пропускаем)."""
        if not self._dir.is_dir():
            return []
        senders: list[int] = []
        for path in self._dir.glob(f"*{_SUFFIX}"):
            name = path.name.removesuffix(_SUFFIX)
            try:
                senders.append(int(name))
            except ValueError:
                continue
        return sorted(senders)


__all__ = ["SessionStore", "InvalidToken"]
