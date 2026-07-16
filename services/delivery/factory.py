"""Сборка `DeliveryRouter` из настроек окружения.

Роутер и адаптеры не знают про `Settings` (чтобы тесты подсовывали моки). Эта
фабрика — единственное место, где адаптеры получают конфиг из `get_settings()`.
Пустые `userbot_base_url` / `smtp_host` = канал не сконфигурирован → адаптер
вернёт `ok=False` с fallback-текстом, оператор перешлёт вручную.
"""

from __future__ import annotations

from config.settings import Settings, get_settings

from services.delivery.email import SmtpDelivery
from services.delivery.manual import ManualDelivery
from services.delivery.router import DeliveryRouter
from services.delivery.telegram import TelegramUserbotDelivery


def build_delivery_router(
    settings: Settings | None = None, *, sender_id: int | None = None
) -> DeliveryRouter:
    """Собрать роутер доставки с адаптерами, сконфигурированными из настроек.

    `sender_id` — Telegram ID оператора-инициатора: сообщение в Telegram уходит
    от его аккаунта (сессии в userbot-сервисе ключуются по операторам).
    """
    cfg = settings or get_settings()
    return DeliveryRouter(
        telegram=TelegramUserbotDelivery(cfg.userbot_base_url, sender_id=sender_id),
        email=SmtpDelivery(
            host=cfg.smtp_host,
            port=cfg.smtp_port,
            user=cfg.smtp_user,
            password=cfg.smtp_password.get_secret_value(),
            from_name=cfg.smtp_from_name,
        ),
        manual=ManualDelivery(),
    )
