"""Тесты хендлера `/surfaces` (справочник площадок подписки в Telegram-боте).

Регрессия: оператор вводил `/surfaces` — бот не отвечал вообще ничем, ни текста,
ни ошибки. Роутер был зарегистрирован и не перехватывался более ранними хендлерами;
`render_surfaces()` сам по себе не падал. Падение случалось в `message.answer(...,
parse_mode="HTML")`: подсказка лид-формы в справочнике площадок несла буквальный
плейсхолдер `<номер>`, Telegram отклонял такое сообщение как невалидный HTML, а
aiogram эту ошибку проглатывал молча (`Dispatcher._process_update` ловит любое
исключение хендлера и только логирует его — см. докстринг `bot.handlers.surfaces`).

Хендлер здесь гоняется на РЕАЛЬНОМ справочнике площадок (`integrations.vk_surfaces`
через `services.goals`), а не на подмене: баг проявлялся именно на живых данных.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from bot.handlers import surfaces as handler

_ALLOWED_TELEGRAM_TAGS = {"b", "/b"}


class _FakeMessage:
    """Минимальный дубль Message: копит ответы вместе с kwargs (как в других тестах бота)."""

    def __init__(self) -> None:
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


def _run() -> dict[str, Any]:
    message = _FakeMessage()
    asyncio.run(handler.show_surfaces(message))
    assert len(message.answers) == 1, "хендлер обязан ответить ровно один раз"
    return message.answers[0]


def test_show_surfaces_answers_with_non_empty_text() -> None:
    sent = _run()
    assert sent["text"].strip()


def test_show_surfaces_uses_html_parse_mode() -> None:
    sent = _run()
    assert sent.get("parse_mode") == "HTML"


def test_show_surfaces_sends_html_telegram_will_accept() -> None:
    """Тот самый крэш: до фикса здесь падало на `<номер>` из подсказки лид-формы."""
    sent = _run()
    stray_tags = [
        tag for tag in re.findall(r"<([^>]*)>", sent["text"]) if tag not in _ALLOWED_TELEGRAM_TAGS
    ]
    assert stray_tags == [], f"Telegram отклонит sendMessage: {stray_tags}"
