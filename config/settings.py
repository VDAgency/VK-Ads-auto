"""Конфигурация приложения. Источник значений — переменные окружения / `.env`.

Секреты никогда не хранятся в коде — только в окружении (см. `.env.example`).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки ядра, читаемые из окружения.

    Поля без значений по умолчанию (например, секреты) задаются только через env
    и в репозиторий не попадают.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Окружение приложения: local / staging / production.
    app_env: str = "local"

    # Строки подключения к инфраструктуре (значения — только из env).
    database_url: str = "postgresql+asyncpg://localhost/vk_ads_auto"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    """Вернуть закешированный экземпляр настроек (читается один раз за процесс)."""
    return Settings()
