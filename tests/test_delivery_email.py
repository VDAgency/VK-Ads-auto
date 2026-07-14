"""SmtpDelivery — успех и все ошибки из §9 спеки (моки aiosmtplib.send)."""

import asyncio
from unittest.mock import patch

import aiosmtplib
import pytest
from services.contact import Contact, ContactType
from services.delivery.base import DeliveryChannel
from services.delivery.email import SmtpDelivery, human_message


def _adapter() -> SmtpDelivery:
    return SmtpDelivery(
        host="smtp.example.com",
        port=465,
        user="noreply@vk-ads-auto.ru",
        password="secret",
        from_name="VK Ads Auto",
    )


def _contact() -> Contact:
    return Contact(ContactType.EMAIL, "ivan@example.com")


def test_send_ok_returns_delivered() -> None:
    async def scenario() -> None:
        with patch("services.delivery.email.aiosmtplib.send") as smtp_send:
            smtp_send.return_value = None
            result = await _adapter().send(_contact(), "приглашение")
        assert result.ok is True
        assert result.channel is DeliveryChannel.EMAIL
        assert result.error is None
        assert result.fallback_text is None

    asyncio.run(scenario())


def test_send_recipients_refused_is_mapped() -> None:
    async def scenario() -> None:
        with patch("services.delivery.email.aiosmtplib.send") as smtp_send:
            smtp_send.side_effect = aiosmtplib.SMTPRecipientsRefused([])
            result = await _adapter().send(_contact(), "text")
        assert result.error == "smtp_recipient_refused"
        assert result.fallback_text == "text"

    asyncio.run(scenario())


def test_send_auth_error_is_mapped() -> None:
    async def scenario() -> None:
        with patch("services.delivery.email.aiosmtplib.send") as smtp_send:
            smtp_send.side_effect = aiosmtplib.SMTPAuthenticationError(535, "auth failed")
            result = await _adapter().send(_contact(), "text")
        assert result.error == "smtp_auth"

    asyncio.run(scenario())


def test_send_connect_error_becomes_unreachable() -> None:
    async def scenario() -> None:
        with patch("services.delivery.email.aiosmtplib.send") as smtp_send:
            smtp_send.side_effect = aiosmtplib.SMTPConnectError("connect failed")
            result = await _adapter().send(_contact(), "text")
        assert result.error == "smtp_unreachable"

    asyncio.run(scenario())


def test_send_timeout_becomes_unreachable() -> None:
    async def scenario() -> None:
        with patch("services.delivery.email.aiosmtplib.send") as smtp_send:
            smtp_send.side_effect = TimeoutError("timeout")
            result = await _adapter().send(_contact(), "text")
        assert result.error == "smtp_unreachable"

    asyncio.run(scenario())


def test_send_without_host_returns_unreachable_without_network() -> None:
    """Пустой SMTP_HOST — без попытки соединения."""

    async def scenario() -> None:
        adapter = SmtpDelivery(host="", port=465, user="a", password="b", from_name="X")
        result = await adapter.send(_contact(), "text")
        assert result.error == "smtp_unreachable"

    asyncio.run(scenario())


def test_message_headers_include_from_name_and_subject() -> None:
    """Заголовки From/To/Subject собираются корректно."""
    adapter = _adapter()
    message = adapter._build_message("ivan@example.com", "текст-приглашение")
    assert message["From"] == "VK Ads Auto <noreply@vk-ads-auto.ru>"
    assert message["To"] == "ivan@example.com"
    assert message["Subject"] == "Бриф для запуска рекламы"
    assert "текст-приглашение" in message.get_content()


def test_human_message_translates_known_codes() -> None:
    assert human_message("smtp_recipient_refused") != ""
    assert human_message("smtp_auth") != ""
    assert human_message("smtp_unreachable") != ""


@pytest.mark.parametrize("error_code", ["smtp_recipient_refused", "smtp_auth", "smtp_unreachable"])
def test_failed_result_always_has_fallback_text(error_code: str) -> None:
    """Инвариант §9: при любой ошибке fallback_text заполнен."""

    async def scenario() -> None:
        with patch("services.delivery.email.aiosmtplib.send") as smtp_send:
            errors = {
                "smtp_recipient_refused": aiosmtplib.SMTPRecipientsRefused([]),
                "smtp_auth": aiosmtplib.SMTPAuthenticationError(535, "x"),
                "smtp_unreachable": aiosmtplib.SMTPConnectError("x"),
            }
            smtp_send.side_effect = errors[error_code]
            result = await _adapter().send(_contact(), "invite")
        assert result.fallback_text == "invite"
        assert result.error == error_code

    asyncio.run(scenario())
