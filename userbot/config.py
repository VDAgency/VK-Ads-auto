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

    # --- точки подключения к дата-центрам (spec 2026-07-31 §5) -------------------
    # Пины адресов: "dc:ip:port[:transport]" через запятую. Перекрывают встроенные
    # адреса и нужны там, где стандартный порт дата-центра закрыт провайдером.
    # Прод: "2:149.154.167.51:5222,4:149.154.167.91:5222" (443/80 у DC2/DC4 закрыты).
    dc_endpoints: str = ""
    # Порядок перебора портов для дата-центров без пина.
    dc_ports: str = "443,5222,80"
    # Основной транспорт MTProto и что пробовать после него. `obfuscated` даёт
    # рандомизированный поток — иногда проходит там, где `full` режется.
    transport: str = "full"
    transport_fallbacks: str = "obfuscated"
    # Порядок дата-центров для НОВОГО логина: домашний DC назначит сам Telegram.
    auth_dc_order: str = "1,5,3,2,4"
    # Прокси: "socks5://user:pass@host:port" или "mtproxy://host:port?secret=hex".
    # Пусто — работаем напрямую. Требует пакета python-socks.
    proxy: SecretStr = SecretStr("")

    # --- тайминги ----------------------------------------------------------------
    # Таймаут одной попытки подключения. Живой TCP-хендшейк укладывается в секунду,
    # так что 8 — с запасом, но без многосекундного залипания на мёртвом адресе.
    connect_timeout: int = 8
    # Потолок всей цепочки перебора. Должен быть меньше таймаута HTTP-клиента бота,
    # иначе оператор получит таймаут вместо осмысленной ошибки.
    connect_budget: int = 45
    # Сколько раз пройти цепочку точек. Фильтрация вероятностная: на проде один и тот
    # же адрес отвечает примерно в половине попыток, и один проход даёт ложное
    # «недоступно». Ограничитель по времени — connect_budget.
    connect_rounds: int = 3

    # --- как сессия представляется Telegram ---------------------------------------
    # Задаём явно и НЕ меняем между релизами. Дефолты Telethon берутся из версии ядра
    # хоста и версии библиотеки — они меняются при каждом обновлении, из-за чего
    # аккаунт выглядит «переехавшим на другое устройство». Эти же строки оператор
    # видит в списке устройств Telegram, поэтому они узнаваемые.
    device_model: str = "VK Ads Auto"
    system_version: str = "Ubuntu 24.04"
    app_version: str = "1.0"
    lang_code: str = "ru"


@lru_cache
def get_settings() -> UserbotSettings:
    """Вернуть закешированный экземпляр настроек userbot."""
    return UserbotSettings()
