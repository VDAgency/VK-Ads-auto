"""Email-канал: адаптер отправки письма через SMTP (spec 2026-07-13 §5).

aiosmtplib, TLS на 465 (implicit SSL). Одна попытка, таймаут 20с; ретраи — по
инициативе оператора. Ошибки SMTP маппим в короткие коды из §9 спеки.

Пустой `smtp_host` = SMTP не сконфигурирован → возвращаем `smtp_unreachable`
без попытки соединения.
"""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from services.contact import Contact
from services.delivery.base import DeliveryChannel, DeliveryResult

_TIMEOUT = 20.0
_SUBJECT = "Бриф для запуска рекламы"

_ERROR_MESSAGES = {
    "smtp_recipient_refused": "Email: адрес отклонён сервером.",
    "smtp_auth": "SMTP: ошибка авторизации.",
    "smtp_unreachable": "SMTP недоступен, попробуйте позже.",
}


class SmtpDelivery:
    """Отправляет письмо с приглашением через SMTP-провайдера тенанта."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_name: str,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._from_name = from_name

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        if not self._host or not self._user:
            return self._failed("smtp_unreachable", invite_text)

        message = self._build_message(contact.value, invite_text)

        try:
            await aiosmtplib.send(
                message,
                hostname=self._host,
                port=self._port,
                username=self._user,
                password=self._password,
                use_tls=True,
                timeout=_TIMEOUT,
            )
        except aiosmtplib.SMTPRecipientsRefused:
            return self._failed("smtp_recipient_refused", invite_text)
        except aiosmtplib.SMTPAuthenticationError:
            return self._failed("smtp_auth", invite_text)
        except (aiosmtplib.SMTPException, OSError, TimeoutError):
            # Сюда попадают SMTPConnectError, SMTPServerDisconnected и таймауты.
            return self._failed("smtp_unreachable", invite_text)

        return DeliveryResult(ok=True, channel=DeliveryChannel.EMAIL)

    def _build_message(self, recipient: str, invite_text: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = f"{self._from_name} <{self._user}>"
        message["To"] = recipient
        message["Subject"] = _SUBJECT
        message.set_content(invite_text)
        return message

    @staticmethod
    def _failed(error_code: str, invite_text: str) -> DeliveryResult:
        return DeliveryResult(
            ok=False,
            channel=DeliveryChannel.EMAIL,
            fallback_text=invite_text,
            error=error_code,
        )


def human_message(error_code: str) -> str:
    """Русский текст ошибки для показа оператору."""
    return _ERROR_MESSAGES.get(error_code, "Email: неизвестная ошибка.")
