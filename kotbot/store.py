"""Шифрованное хранилище кредов и storage_state kotbot (spec §3, §4).

Паттерн `userbot/session.py`: Fernet-шифрование ключом из env, файлы 0600,
атомарная запись через `os.replace`. На диске в `secrets_dir` лежат:

- `credentials.enc` — JSON `{"email": {"login", "password"}, "vk": {...}}`
  (ключи стратегий опциональны — сохраняется то, чем реально входили);
- `state.email.json.enc`, `state.vk.json.enc` — Playwright storage_state
  (сериализованная SSO-цепочка; наполняется бэкендом в K-PR3).

Пустой `secret_key` = сервис не сконфигурирован: конструктор не падает
(сервис должен подняться и отвечать на /health), но операции чтения/записи
бросают `NotConfiguredError` → API отвечает 400 `not_configured`.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_CREDENTIALS_FILE = "credentials.enc"


class NotConfiguredError(RuntimeError):
    """Пустой `KOTBOT_SECRET_KEY` — операции с хранилищем невозможны."""


class _EncryptedFileStore:
    """Общая механика двух хранилищ: Fernet + атомарная запись файлов 0600."""

    def __init__(self, secret_key: str, secrets_dir: str) -> None:
        # Fernet сам проверит корректность непустого ключа (base64, 32 байта).
        # Пустой ключ — допустимое состояние «не сконфигурирован» (spec §4).
        self._fernet = Fernet(secret_key.encode("ascii")) if secret_key else None
        self._dir = Path(secrets_dir)

    @property
    def configured(self) -> bool:
        """Задан ли ключ шифрования (иначе операции бросают NotConfiguredError)."""
        return self._fernet is not None

    def _require_fernet(self) -> Fernet:
        if self._fernet is None:
            raise NotConfiguredError("KOTBOT_SECRET_KEY is empty")
        return self._fernet

    def _write(self, path: Path, data: bytes) -> None:
        """Зашифровать и записать файл: tmp → chmod 0600 → атомарный replace."""
        token = self._require_fernet().encrypt(data)
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(token)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)

    def _read(self, path: Path) -> bytes | None:
        """Прочитать и расшифровать файл; `None`, если файла ещё нет.

        Пустой ключ → `NotConfiguredError` даже без файла (контракт «операции
        без ключа невозможны»). Неверный ключ → `InvalidToken` (пробрасываем —
        сбой конфигурации).
        """
        fernet = self._require_fernet()
        if not path.exists():
            return None
        return fernet.decrypt(path.read_bytes())


class CredentialStore(_EncryptedFileStore):
    """Креды входа на kotbot.ru по стратегиям (`email` / `vk`), один файл."""

    def _path(self) -> Path:
        return self._dir / _CREDENTIALS_FILE

    def _load_all(self) -> dict[str, dict[str, str]]:
        raw = self._read(self._path())
        if raw is None:
            return {}
        parsed: dict[str, dict[str, str]] = json.loads(raw.decode("utf-8"))
        return parsed

    def save(self, strategy: str, login: str, password: str) -> None:
        """Сохранить креды стратегии, не затирая креды другой стратегии."""
        data = self._load_all()
        data[strategy] = {"login": login, "password": password}
        self._write(self._path(), json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def load(self, strategy: str) -> tuple[str, str] | None:
        """Вернуть `(login, password)` стратегии; `None`, если не сохраняли."""
        entry = self._load_all().get(strategy)
        if entry is None:
            return None
        return entry["login"], entry["password"]

    def has(self, strategy: str) -> bool:
        """Есть ли креды стратегии. Дёшево и безопасно для /health:

        не сконфигурировано или файл нечитаем → `False`, а не исключение.
        """
        if not self.configured:
            return False
        try:
            return self.load(strategy) is not None
        except InvalidToken:
            return False


class StateStore(_EncryptedFileStore):
    """Playwright storage_state по стратегиям: `state.{strategy}.json.enc`."""

    def _path(self, strategy: str) -> Path:
        return self._dir / f"state.{strategy}.json.enc"

    def save_raw(self, strategy: str, data: bytes) -> None:
        """Сохранить сырой storage_state стратегии (шифрованно)."""
        self._write(self._path(strategy), data)

    def load_raw(self, strategy: str) -> bytes | None:
        """Прочитать storage_state стратегии; `None`, если ещё не сохраняли."""
        return self._read(self._path(strategy))

    def has(self, strategy: str) -> bool:
        """Есть ли сохранённый storage_state (для /health — только наличие файла)."""
        return self.configured and self._path(strategy).exists()


__all__ = ["CredentialStore", "InvalidToken", "NotConfiguredError", "StateStore"]
