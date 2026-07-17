"""Тесты письма входа в кабинет (`services/cabinet_email`, `SmtpDelivery.send_email`)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import aiosmtplib
from config.settings import Settings
from services.cabinet_email import send_login_link
from services.delivery.email import SmtpDelivery


def _configured() -> Settings:
    return Settings(
        _env_file=None,
        smtp_host="smtp.test",
        smtp_port=465,
        smtp_support_user="support@vk-ads-auto.ru",
        smtp_support_password="pw",
        smtp_support_from_name="VK Ads Auto Support",
    )


def test_send_login_link_uses_support_sender_and_includes_link() -> None:
    with patch("services.delivery.email.aiosmtplib.send") as smtp:
        ok = asyncio.run(
            send_login_link(
                "client@example.com",
                "https://vk-ads-auto.ru/cabinet.html?token=ABC123",
                settings=_configured(),
            )
        )
    assert ok is True
    smtp.assert_called_once()
    message = smtp.call_args.args[0]
    assert "support@vk-ads-auto.ru" in message["From"]
    assert "ABC123" in message.get_content()


def test_send_login_link_unconfigured_returns_false() -> None:
    # Пустой smtp_host → письмо не уходит, возвращаем False (эндпоинт всё равно отвечает ok).
    ok = asyncio.run(send_login_link("c@e.com", "link", settings=Settings(_env_file=None)))
    assert ok is False


def test_send_email_returns_false_on_smtp_error() -> None:
    delivery = SmtpDelivery("smtp.test", 465, "u@e.com", "pw", "Name")
    with patch(
        "services.delivery.email.aiosmtplib.send", side_effect=aiosmtplib.SMTPException("boom")
    ):
        ok = asyncio.run(delivery.send_email("c@e.com", "Subject", "Body"))
    assert ok is False


def test_send_email_subject_and_body() -> None:
    delivery = SmtpDelivery("smtp.test", 465, "u@e.com", "pw", "Name")
    with patch("services.delivery.email.aiosmtplib.send") as smtp:
        ok = asyncio.run(delivery.send_email("c@e.com", "Тема письма", "Тело письма"))
    assert ok is True
    message = smtp.call_args.args[0]
    assert message["Subject"] == "Тема письма"
    assert "Тело письма" in message.get_content()
