"""Тесты хендлера `/pending`: рендер `N. Имя — контакт — дата`, метки дат, заглушка."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from bot.api_client import CoreUnavailable, InviteItem
from bot.handlers import pending

_MSK = timezone(timedelta(hours=3))
_NOW = datetime(2026, 7, 16, 22, 0, tzinfo=_MSK)


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


def _item(
    contact: str,
    *,
    name: str | None = None,
    channel: str = "email",
    sent_at: str | None = None,
    received_at: str | None = None,
) -> InviteItem:
    return InviteItem(
        contact=contact,
        contact_name=name,
        variant="individual",
        channel=channel,
        sent_at=sent_at,
        received_at=received_at,
        waiting_days=0,
    )


def test_renders_name_contact_and_numbering(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pending() -> list[InviteItem]:
        return [
            _item("@CryptoSamara", name="Вячеслав", channel="telegram"),
            _item("ivan@mail.ru"),  # без имени — только контакт
        ]

    async def fake_recent() -> list[InviteItem]:
        return [_item("@petrov", name="Пётр", channel="telegram")]

    monkeypatch.setattr("bot.api_client.get_pending", fake_pending)
    monkeypatch.setattr("bot.api_client.get_recent", fake_recent)
    message = _FakeMessage()
    asyncio.run(pending.show_pending(message))

    text = message.answers[0]
    assert "Ждём бриф (2)" in text
    assert "1. Вячеслав — @CryptoSamara" in text
    assert "2. ivan@mail.ru" in text  # без имени — контакт как есть
    assert "Пришли за неделю (1)" in text
    assert "1. Пётр — @petrov" in text


def test_empty_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty() -> list[InviteItem]:
        return []

    monkeypatch.setattr("bot.api_client.get_pending", empty)
    monkeypatch.setattr("bot.api_client.get_recent", empty)
    message = _FakeMessage()
    asyncio.run(pending.show_pending(message))

    assert "Пока никого не ждём" in message.answers[0]


def test_core_unavailable_shows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> list[InviteItem]:
        raise CoreUnavailable("down")

    monkeypatch.setattr("bot.api_client.get_pending", boom)
    message = _FakeMessage()
    asyncio.run(pending.show_pending(message))

    assert "временно недоступен" in message.answers[0]


# --- Метки дат (чистый помощник) ----------------------------------------------


def test_date_label_today() -> None:
    assert pending._date_label("2026-07-16T10:00:00+00:00", _NOW) == "сегодня"


def test_date_label_yesterday() -> None:
    assert pending._date_label("2026-07-15T10:00:00+00:00", _NOW) == "вчера"


def test_date_label_same_year() -> None:
    assert pending._date_label("2026-07-01T10:00:00+00:00", _NOW) == "01.07"


def test_date_label_other_year() -> None:
    assert pending._date_label("2025-06-01T10:00:00+00:00", _NOW) == "01.06.2025"


def test_date_label_empty_for_none() -> None:
    assert pending._date_label(None, _NOW) == ""


def test_who_with_and_without_name() -> None:
    assert pending._who(_item("@cs", name="Вячеслав")) == "Вячеслав — @cs"
    assert pending._who(_item("ivan@mail.ru")) == "ivan@mail.ru"
