"""services/notifier — формат уведомления §8.3 и маскирование PII в логах."""

import asyncio
import logging
from datetime import UTC, datetime

from services.notifier import (
    format_brief_received,
    mask_contact,
    notify_operator_brief_received,
)


def test_format_matches_spec_8_3() -> None:
    text = format_brief_received(
        contact="@ivanov",
        sent_at=datetime(2026, 7, 13, 9, 5, tzinfo=UTC),
        channel="telegram",
        variant="individual",
        brief_id=42,
    )
    assert text == (
        "🎉 Пришёл бриф!\n"
        "Клиент: @ivanov\n"
        "Отправлен: 13.07 09:05 через telegram\n"
        "Вариант: Физлицо\n"
        "/view_brief_42"
    )


def test_format_community_variant_ru() -> None:
    text = format_brief_received(
        contact="user@mail.ru",
        sent_at=datetime(2026, 1, 2, 18, 30, tzinfo=UTC),
        channel="email",
        variant="community",
        brief_id=7,
    )
    assert "Вариант: Сообщество (ИП/бизнес)" in text


def test_format_without_sent_at_shows_dash() -> None:
    text = format_brief_received(
        contact="+79990001122",
        sent_at=None,
        channel="manual",
        variant="individual",
        brief_id=1,
    )
    assert "Отправлен: — через manual" in text


def test_mask_email() -> None:
    assert mask_contact("ivan.petrov@example.com") == "iv***@example.com"


def test_mask_short_email_local_part() -> None:
    assert mask_contact("a@b.ru") == "***@b.ru"


def test_mask_phone_keeps_last_two() -> None:
    assert mask_contact("+79991234567") == "+7********67"


def test_mask_telegram() -> None:
    assert mask_contact("@ivanov") == "@iv***"


def test_notify_sends_full_text_and_masks_in_log(caplog: object) -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    import pytest

    _caplog = caplog
    assert isinstance(_caplog, pytest.LogCaptureFixture)

    async def scenario() -> None:
        with _caplog.at_level(logging.INFO, logger="services.notifier"):
            await notify_operator_brief_received(
                fake_send,
                operator_telegram_id=555,
                contact="ivan.petrov@example.com",
                sent_at=datetime(2026, 7, 13, 9, 5, tzinfo=UTC),
                channel="email",
                variant="community",
                brief_id=42,
            )

    asyncio.run(scenario())

    # Оператору ушёл ПОЛНЫЙ контакт.
    assert len(sent) == 1
    assert sent[0][0] == 555
    assert "ivan.petrov@example.com" in sent[0][1]
    # В логах — только маскированный контакт, не полный.
    log_text = " ".join(r.getMessage() for r in _caplog.records)
    assert "ivan.petrov@example.com" not in log_text
    assert "iv***@example.com" in log_text
