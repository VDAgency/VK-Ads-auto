"""Тесты сборки роутера доставки из настроек (`build_delivery_router`).

Ключевая проверка: email-канал (ссылка на бриф) использует профиль info@,
а не support@. Профиль support@ в фабрику пока не проброшен (задел под кабинет).
"""

import asyncio

from config.settings import Settings
from pydantic import SecretStr
from services.contact import Contact, ContactType
from services.delivery.email import SmtpDelivery
from services.delivery.factory import build_delivery_router


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_email_adapter_uses_info_profile() -> None:
    """Email-канал берёт хост/порт/логин/пароль/имя из профиля info@."""
    settings = _settings(
        smtp_host="smtp.x",
        smtp_port=465,
        smtp_info_user="info@x",
        smtp_info_password=SecretStr("info-pass"),
        smtp_info_from_name="Info Sender",
        smtp_support_user="support@x",
        smtp_support_password=SecretStr("support-pass"),
    )
    adapter = build_delivery_router(settings).route(Contact(ContactType.EMAIL, "a@b.c"))
    assert isinstance(adapter, SmtpDelivery)
    assert adapter._user == "info@x"
    assert adapter._from_name == "Info Sender"


def test_support_profile_is_not_wired_to_email_channel() -> None:
    """Только support@ задан, info@ пуст → email-адаптер не сконфигурирован."""
    settings = _settings(
        smtp_host="smtp.x",
        smtp_support_user="support@x",
        smtp_support_password=SecretStr("support-pass"),
    )
    adapter = build_delivery_router(settings).route(Contact(ContactType.EMAIL, "a@b.c"))
    assert isinstance(adapter, SmtpDelivery)
    assert adapter._user == ""

    async def scenario() -> str | None:
        result = await adapter.send(Contact(ContactType.EMAIL, "a@b.c"), "text")
        return result.error

    assert asyncio.run(scenario()) == "smtp_unreachable"
