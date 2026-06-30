"""Конфигурация приложения. Источник значений — переменные окружения / `.env`.

Секреты никогда не хранятся в коде — только в окружении (см. `.env.example`).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Telegram-бот. На время разработки — тестовый токен; перед сдачей заменить.
    # Пустое значение = бот не сконфигурирован.
    bot_token: SecretStr = SecretStr("")
    # Telegram ID операторов (бот принимает команды только от них). В env —
    # список через запятую: OPERATOR_TELEGRAM_IDS=5389520473,5481870843.
    operator_telegram_ids: Annotated[frozenset[int], NoDecode] = frozenset()

    # VK Реклама (VK Ads): OAuth2-токен (Bearer) + refresh. Конфиг per-account
    # (см. PROJECT.md §6) — здесь дефолтные значения для единственного тенанта.
    vk_ads_access_token: SecretStr = SecretStr("")
    vk_ads_refresh_token: SecretStr = SecretStr("")
    vk_ads_token_type: str = "Bearer"

    @field_validator("operator_telegram_ids", mode="before")
    @classmethod
    def _parse_operator_ids(cls, value: object) -> object:
        """Разобрать список ID из строки `a,b,c` (формат env) в набор int."""
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value

    def is_operator(self, telegram_id: int) -> bool:
        """True, если данный Telegram ID входит в список операторов."""
        return telegram_id in self.operator_telegram_ids


@lru_cache
def get_settings() -> Settings:
    """Вернуть закешированный экземпляр настроек (читается один раз за процесс)."""
    return Settings()
