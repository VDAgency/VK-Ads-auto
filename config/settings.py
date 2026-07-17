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

    # Публичный базовый URL (для ссылок на брифы из бота).
    public_base_url: str = "https://vk-ads-auto.ru"

    # Секрет для подписи magic-link токенов клиентского кабинета (в проде — из env).
    secret_key: SecretStr = SecretStr("dev-insecure-change-me")

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
    # Боевое создание кабинетов/кампаний VK разрешено только после подтверждения
    # агентского статуса ИП (CLAUDE.md §1.4). Пока False — запуск РК идёт заглушкой.
    vk_agency_confirmed: bool = False

    # Каталог хранения загруженных креативов (persistent volume РФ-сервера).
    creatives_dir: str = "/data/creatives"

    # Userbot (Telethon-сервис доставки в Telegram) — см. spec 2026-07-13 §6.
    # Пустой BASE_URL = сервис не сконфигурирован; адаптер вернёт userbot_unreachable.
    # Остальные USERBOT_-переменные читает сам сервис (userbot/config.py), не ядро.
    userbot_base_url: str = ""

    # Внутренний API ядра для тонких клиентов (бот ходит сюда, не в БД, §1.3 CLAUDE.md).
    # Дефолт — имя сервиса ядра в docker-compose; локально задаётся через env.
    core_base_url: str = "http://api:8000"

    # Мок-гейт статистики (§7 spec 2026-07-15). Демо-данные показываем, пока нет
    # реальных метрик, не истёк срок и клиентов меньше порога. Как только любое
    # условие нарушено — гейт закрывается, моки исчезают сами.
    mock_until: str = "2026-12-31"  # дата отключения демо (ISO, env: MOCK_UNTIL)
    mock_max_clients: int = 5  # порог реальных клиентов (env: MOCK_MAX_CLIENTS)

    # SMTP (доставка писем). Хост/порт общие (Beget), отправителей два:
    # info@ — информационные письма (ссылка на бриф клиенту),
    # support@ — технические (вход в кабинет/сброс пароля, задел под кабинет).
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_info_user: str = ""
    smtp_info_password: SecretStr = SecretStr("")
    smtp_info_from_name: str = "VK Ads Auto"
    smtp_support_user: str = ""
    smtp_support_password: SecretStr = SecretStr("")
    smtp_support_from_name: str = "VK Ads Auto"

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
