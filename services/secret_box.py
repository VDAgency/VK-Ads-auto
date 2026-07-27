"""Шифрование секретов, хранимых в колонках БД (spec 2026-07-27 §6).

Паттерн взят у `kotbot/store.py` и `userbot/session.py` — Fernet с ключом из
окружения, — но носитель другой: там шифруются файлы на диске, здесь строка,
которая ляжет в колонку таблицы. Поэтому это отдельный модуль без файловых
операций, а не переиспользование файлового хранилища.

Пустой ключ — допустимое состояние «не сконфигурировано»: приложение обязано
подняться и отвечать на `/health` даже без `VK_ADS_SECRET_KEY`, а операции с
секретами в этом состоянии бросают `NotConfiguredError` (зеркало поведения
kotbot-store).
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["InvalidToken", "NotConfiguredError", "SecretBox", "token_tail"]

# Сколько последних символов токена показываем оператору вместо самого токена.
_TAIL_LEN = 4


class NotConfiguredError(RuntimeError):
    """Ключ шифрования не задан — работать с секретами нельзя."""


class SecretBox:
    """Шифрование/расшифровка строковых секретов одним ключом Fernet."""

    def __init__(self, secret_key: str) -> None:
        # Fernet сам провалидирует непустой ключ (base64, 32 байта).
        self._fernet = Fernet(secret_key.encode("ascii")) if secret_key else None

    @property
    def configured(self) -> bool:
        """Задан ли ключ (иначе операции бросают `NotConfiguredError`)."""
        return self._fernet is not None

    def _require(self) -> Fernet:
        if self._fernet is None:
            raise NotConfiguredError("VK_ADS_SECRET_KEY is empty")
        return self._fernet

    def encrypt(self, value: str) -> str:
        """Зашифровать секрет; результат пригоден для колонки `Text`."""
        return self._require().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        """Расшифровать секрет. Неверный ключ → `InvalidToken` (сбой конфигурации)."""
        return self._require().decrypt(value.encode("ascii")).decode("utf-8")


def token_tail(token: str) -> str:
    """Хвост токена для показа оператору («…4iA6»).

    Показываем ровно столько, чтобы отличить один кабинет от другого; по хвосту
    восстановить токен нельзя. Короткие строки не раскрываем целиком — для них
    хвост пустой, иначе маска выдала бы весь секрет.
    """
    if len(token) <= _TAIL_LEN:
        return ""
    return token[-_TAIL_LEN:]
