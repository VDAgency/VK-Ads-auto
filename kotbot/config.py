"""Конфигурация сервиса kotbot. Значения — только из окружения (spec §4).

Отдельно от `config.settings` ядра: kotbot — самостоятельный контейнер и не
должен тянуть зависимости ядра. Секрет (`KOTBOT_SECRET_KEY`) — только в env.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class KotbotSettings(BaseSettings):
    """Настройки kotbot-сервиса, читаемые из окружения."""

    model_config = SettingsConfigDict(
        env_prefix="KOTBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ключ Fernet (base64, 32 байта) для шифрования кредов и storage_state.
    # Пустой ключ = сервис не сконфигурирован: поднимается, но auth/actions
    # отвечают 400 `not_configured` (spec §4).
    secret_key: str = ""

    # Каталог зашифрованных файлов: credentials.enc, state.{strategy}.json.enc
    # (persistent volume, файлы 0600).
    secrets_dir: str = "/secrets/kotbot"

    # Базовый URL kotbot.ru (переопределяется в тестовой среде).
    site_url: str = "https://kotbot.ru"

    # Параметры браузера (используются Playwright-бэкендом, K-PR3).
    headless: bool = True
    browser_channel: str = ""  # локально можно "chrome" (VPN/TLS, spec §14)
    nav_timeout_ms: int = 30000

    # Порядок стратегий входа для ensure_logged_in (K-PR3): первая — предпочтительная.
    strategy_order: str = "email,vk"


@lru_cache
def get_settings() -> KotbotSettings:
    """Вернуть закешированный экземпляр настроек kotbot."""
    return KotbotSettings()
