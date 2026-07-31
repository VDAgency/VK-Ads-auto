"""Когда предлагать email вместо Telegram — таблица решений.

Разделение простое: сорвался канал → письмо осмысленно; неверен контакт → письмо
уйдёт не тому, и правильное действие оператора «ввести контакт заново».
"""

from __future__ import annotations

import pytest
from services.invites_fallback import EmailOffer, offer_email


@pytest.mark.parametrize(
    "error",
    [
        "userbot_unreachable",
        "session_expired",
        "session_duplicated",
        "sender_not_authorized",
        "account_banned",
        "flood_wait",
        "peer_flood",
        "privacy_restricted",
    ],
)
def test_channel_failures_offer_email(error: str) -> None:
    assert offer_email("failed", "telegram", error, "ivan@mail.ru") == EmailOffer(
        email="ivan@mail.ru"
    )


@pytest.mark.parametrize("error", ["username_not_occupied", "username_invalid"])
def test_wrong_contact_does_not_offer_email(error: str) -> None:
    """Опечатка в username — письмо тут не поможет, адресат под вопросом."""
    assert offer_email("failed", "telegram", error, "ivan@mail.ru") is None


def test_unknown_error_does_not_offer_email() -> None:
    """Незнакомый код не трактуем на удачу — оператор получит текст для пересылки."""
    assert offer_email("failed", "telegram", "какая-то новая ошибка", "ivan@mail.ru") is None


def test_successful_delivery_does_not_offer_email() -> None:
    assert offer_email("sent", "telegram", None, "ivan@mail.ru") is None


@pytest.mark.parametrize("channel", ["email", "manual"])
def test_only_telegram_failures_offer_email(channel: str) -> None:
    """Для письма запасное письмо бессмысленно, для телефона текст уже выдан."""
    assert offer_email("failed", channel, "smtp_unavailable", "ivan@mail.ru") is None


def test_offer_without_known_email_asks_operator() -> None:
    """Канал сорвался, а адреса нет — предлагаем ввести его руками."""
    assert offer_email("failed", "telegram", "userbot_unreachable", None) == EmailOffer(email=None)


def test_empty_email_is_treated_as_missing() -> None:
    assert offer_email("failed", "telegram", "userbot_unreachable", "") == EmailOffer(email=None)
