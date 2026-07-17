"""Отправка письма со ссылкой для входа в кабинет (технический отправитель support@).

Информационные письма (ссылка на бриф) уходят от info@; технические (вход/сброс
пароля) — от support@ (архитектурное решение 2026-07-17, [[reference-smtp-beget-mailboxes]]).
Ошибки отправки поглощаются (логируем тип), чтобы `request-link` не раскрывал наличие
клиента и не падал (spec §5.3/§7).
"""

from __future__ import annotations

import logging

from config.settings import Settings, get_settings

from services.delivery.email import SmtpDelivery

logger = logging.getLogger(__name__)

_SUBJECT = "Вход в личный кабинет VK Ads Auto"


def _support_delivery(cfg: Settings) -> SmtpDelivery:
    return SmtpDelivery(
        host=cfg.smtp_host,
        port=cfg.smtp_port,
        user=cfg.smtp_support_user,
        password=cfg.smtp_support_password.get_secret_value(),
        from_name=cfg.smtp_support_from_name,
    )


def _body(magic_link: str) -> str:
    return (
        "Здравствуйте!\n\n"
        "Чтобы войти в личный кабинет и задать пароль, перейдите по ссылке:\n"
        f"{magic_link}\n\n"
        "Ссылка действует ограниченное время. Если вы не запрашивали вход — "
        "просто проигнорируйте это письмо.\n\n"
        "При первом входе установите пароль и запишите его — по нему вы будете "
        "входить в кабинет в дальнейшем."
    )


async def send_login_link(email: str, magic_link: str, *, settings: Settings | None = None) -> bool:
    """Отправить письмо со ссылкой входа. True — отправлено; False — не сконфигурирован/ошибка."""
    cfg = settings or get_settings()
    ok = await _support_delivery(cfg).send_email(email, _SUBJECT, _body(magic_link))
    if not ok:
        logger.warning("cabinet login link email not sent (SMTP unconfigured or send error)")
    return ok
