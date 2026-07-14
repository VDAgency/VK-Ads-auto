"""Тесты хендлера `/pending` (PR-B): рендер двух секций и заглушка при сбое."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bot.api_client import CoreUnavailable, InviteItem
from bot.handlers import pending


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


def _item(contact: str, channel: str = "email", waiting: int = 0) -> InviteItem:
    return InviteItem(
        contact=contact,
        variant="individual",
        channel=channel,
        sent_at=None,
        received_at=None,
        waiting_days=waiting,
    )


def test_renders_both_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pending() -> list[InviteItem]:
        return [_item("wait@it.c", "email", 3)]

    async def fake_recent() -> list[InviteItem]:
        return [_item("@ivan", "telegram")]

    monkeypatch.setattr("bot.api_client.get_pending", fake_pending)
    monkeypatch.setattr("bot.api_client.get_recent", fake_recent)
    message = _FakeMessage()
    asyncio.run(pending.show_pending(message))

    text = message.answers[0]
    assert "Ждём бриф (1)" in text
    assert "wait@it.c" in text
    assert "ждём 3 дня" in text
    assert "Пришли за неделю (1)" in text
    assert "@ivan" in text


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


def test_waiting_phrase_plurals() -> None:
    assert pending._waiting_phrase(0) == "сегодня"
    assert pending._waiting_phrase(1) == "ждём 1 день"
    assert pending._waiting_phrase(3) == "ждём 3 дня"
    assert pending._waiting_phrase(7) == "ждём 7 дней"
