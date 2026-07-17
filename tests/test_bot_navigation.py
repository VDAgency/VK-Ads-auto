"""Тесты навигации бота: приветствие, reply-клавиатура, нативное меню (PR-A)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup
from bot.handlers import pending, send_brief, start
from bot.keyboards import BTN_PENDING, BTN_SEND_BRIEF, main_menu_keyboard
from bot.menu import bot_commands


def test_menu_has_five_commands() -> None:
    commands = {c.command for c in bot_commands()}
    assert commands == {"send_brief", "pending", "stats", "admin", "link_userbot", "help"}


def test_menu_descriptions_non_empty() -> None:
    assert all(c.description for c in bot_commands())


def test_main_keyboard_has_two_persistent_buttons() -> None:
    kb = main_menu_keyboard()
    assert isinstance(kb, ReplyKeyboardMarkup)
    assert kb.is_persistent is True
    assert kb.resize_keyboard is True
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert texts == [BTN_SEND_BRIEF, BTN_PENDING]


def test_welcome_covers_key_points() -> None:
    text = start._WELCOME
    assert "VK-Ads-auto" in text
    assert BTN_SEND_BRIEF in text
    assert BTN_PENDING in text
    assert "/help" in text


class _FakeMessage:
    """Минимальный дубль Message: копит ответы вместе с kwargs."""

    def __init__(self) -> None:
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


class _FakeBot:
    def __init__(self) -> None:
        self.commands_set = False

    async def set_my_commands(self, commands: list[Any]) -> None:
        self.commands_set = True


def test_start_sends_welcome_with_keyboard_and_menu() -> None:
    message = _FakeMessage()
    bot = _FakeBot()
    asyncio.run(start.start(message, bot))
    assert bot.commands_set is True
    assert len(message.answers) == 1
    sent = message.answers[0]
    assert "VK-Ads-auto" in sent["text"]
    assert isinstance(sent["reply_markup"], ReplyKeyboardMarkup)


def _entry_targets(router: Any) -> list[Any]:
    """Развернуть `or_f(...)` первого message-хендлера в список фильтров-целей."""
    or_filter = router.message.handlers[0].filters[0].callback
    return [target.callback for target in or_filter.targets]


def _entry_command(targets: list[Any]) -> tuple[Any, ...]:
    for target in targets:
        if isinstance(target, Command):
            return tuple(target.commands)
    return ()


def _entry_button_filter(targets: list[Any]) -> Any:
    return next(t for t in targets if not isinstance(t, Command))


def test_send_brief_entry_matches_command_and_button() -> None:
    targets = _entry_targets(send_brief.router)
    assert _entry_command(targets) == ("send_brief",)
    button = _entry_button_filter(targets)
    assert bool(button(SimpleNamespace(text=BTN_SEND_BRIEF))) is True
    assert bool(button(SimpleNamespace(text="что-то другое"))) is False


def test_pending_entry_matches_command_and_button() -> None:
    targets = _entry_targets(pending.router)
    assert _entry_command(targets) == ("pending",)
    button = _entry_button_filter(targets)
    assert bool(button(SimpleNamespace(text=BTN_PENDING))) is True
    assert bool(button(SimpleNamespace(text="что-то другое"))) is False
