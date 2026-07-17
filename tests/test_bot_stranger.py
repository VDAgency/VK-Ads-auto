"""Тесты визитки для не-операторов: фильтр `NonOperator` + текст/кнопка приветствия."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram.types import Message
from bot import access
from bot.handlers import stranger


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


def _run_greet() -> tuple[str, dict[str, Any]]:
    message = _FakeMessage()
    asyncio.run(stranger.greet_stranger(message))
    assert len(message.answers) == 1
    return message.answers[0]


def test_stranger_message_points_to_anastasia() -> None:
    text, _ = _run_greet()
    # Имя может стоять в любом падеже — проверяем по устойчивым частям.
    assert "Анастаси" in text
    assert "Жук" in text
    assert "@zhuknastya" in text
    assert "ВКонтакте" in text


def test_stranger_message_uses_html() -> None:
    _, kwargs = _run_greet()
    assert kwargs.get("parse_mode") == "HTML"


def test_stranger_message_has_contact_button() -> None:
    _, kwargs = _run_greet()
    markup = kwargs.get("reply_markup")
    assert markup is not None
    urls = [button.url for row in markup.inline_keyboard for button in row]
    assert any(url and "zhuknastya" in url for url in urls)


def _event(user_id: int | None) -> Message:
    """Мини-заглушка апдейта: важен только `from_user.id` для фильтра."""
    from_user = SimpleNamespace(id=user_id) if user_id is not None else None
    return cast(Message, SimpleNamespace(from_user=from_user))


def test_nonoperator_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Оператор — только 555; фильтр визитки пропускает всех остальных.
    monkeypatch.setattr(
        access,
        "get_settings",
        lambda: SimpleNamespace(is_operator=lambda uid: uid == 555),
    )
    filt = access.NonOperator()
    assert asyncio.run(filt(_event(999))) is True
    assert asyncio.run(filt(_event(555))) is False
    # Без пользователя (сервисные апдейты) — не реагируем.
    assert asyncio.run(filt(_event(None))) is False


def test_operator_filter_still_blocks_strangers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        access,
        "get_settings",
        lambda: SimpleNamespace(is_operator=lambda uid: uid == 555),
    )
    filt = access.OperatorOnly()
    assert asyncio.run(filt(_event(555))) is True
    assert asyncio.run(filt(_event(999))) is False
