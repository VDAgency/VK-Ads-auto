"""Конфигурация сервиса userbot. Значения — только из окружения (spec §6, §11).

Отдельно от `config.settings` ядра: userbot — самостоятельный контейнер и не должен
тянуть зависимости ядра. Секреты (`API_HASH`, `SECRET_KEY`) — только в env, не в коде.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class UserbotSettings(BaseSettings):
    """Настройки userbot-сервиса, читаемые из окружения."""

    model_config = SettingsConfigDict(
        env_prefix="USERBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram API-приложение проекта (my.telegram.org); одно на все сессии
    # операторов. Пустой api_id/api_hash = сервис не сконфигурирован.
    api_id: int = 0
    api_hash: SecretStr = SecretStr("")

    # Ключ Fernet (base64, 32 байта) для шифрования StringSession.
    secret_key: SecretStr = SecretStr("")

    # Каталог зашифрованных сессий операторов: {sender_id}.session.enc
    # (persistent volume, файлы 0600). По сессии на оператора.
    sessions_dir: str = "/secrets"


@lru_cache
def get_settings() -> UserbotSettings:
    """Вернуть закешированный экземпляр настроек userbot."""
    return UserbotSettings()
