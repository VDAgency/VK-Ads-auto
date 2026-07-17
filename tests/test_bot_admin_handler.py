"""Тест бот-команды `/admin`: выдаёт валидную ссылку входа в админку."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from bot.handlers import admin
from config.settings import get_settings
from services.admin_auth import verify_admin_link

_OPERATOR_ID = 555


class _FakeMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


def test_admin_command_sends_valid_link() -> None:
    message = _FakeMessage()
    asyncio.run(admin.admin_link(message))

    assert message.answers
    text = message.answers[0]
    assert "/admin.html?token=" in text
    token = text.split("token=", 1)[1].strip()
    # Токен из ссылки валиден и принадлежит вызвавшему оператору.
    assert verify_admin_link(token, get_settings().secret_key.get_secret_value()) == _OPERATOR_ID
