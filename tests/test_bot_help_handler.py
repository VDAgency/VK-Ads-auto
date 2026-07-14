"""Тесты хендлера `/help` (PR-D): экран содержит разбор каждой команды (§10)."""

from __future__ import annotations

import asyncio
from typing import Any

from bot.handlers import help as help_handler


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


def _run_help() -> str:
    message = _FakeMessage()
    asyncio.run(help_handler.show_help(message))
    assert len(message.answers) == 1
    return message.answers[0][0]


def test_help_mentions_every_command() -> None:
    text = _run_help()
    for command in ("/send_brief", "/pending", "/stats", "/link_userbot", "/help"):
        assert command in text, f"в помощи нет разбора {command}"


def test_help_explains_workflow() -> None:
    text = _run_help()
    # Цикл работы: бриф → заполнение → кабинет → статистика.
    assert "бриф" in text.lower()
    assert "кабинет" in text.lower()
    assert "статистик" in text.lower()


def test_help_flags_demo_data() -> None:
    text = _run_help()
    assert "Демо" in text or "демо" in text  # честная пометка про заглушки


def test_help_has_support_contact() -> None:
    text = _run_help()
    assert "@" in text  # плейсхолдер контакта для вопросов


def test_help_uses_html_parse_mode() -> None:
    message = _FakeMessage()
    asyncio.run(help_handler.show_help(message))
    assert message.answers[0][1].get("parse_mode") == "HTML"
