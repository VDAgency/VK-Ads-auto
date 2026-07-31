"""Тесты навигации справочника `/help`: меню разделов, листание, краевые случаи.

Ключевое требование — карточка помощи одна: переход между страницами редактирует
то же сообщение, а не отправляет новое. Позиция лежит в callback_data (FSM нет),
поэтому кнопки работают и после рестарта бота.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import InlineKeyboardMarkup
from bot import help_content
from bot.access import OperatorOnly
from bot.handlers import help as help_handler
from bot.keyboards import HELP_MENU_CD, help_page_cd
from bot.main import routers as bot_routers

_OPERATOR_ID = 111


def _bad_request(text: str) -> TelegramBadRequest:
    """Ошибка Telegram с заданным текстом (конструируется типобезопасно)."""
    return TelegramBadRequest(
        method=EditMessageText(chat_id=1, message_id=1, text="x"), message=text
    )


class _FakeMessage:
    def __init__(self, *, edit_error: TelegramBadRequest | None = None) -> None:
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.answers: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []
        self._edit_error = edit_error

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        if self._edit_error is not None:
            raise self._edit_error
        self.edits.append((text, kwargs))


class _FakeCallback:
    def __init__(self, data: str, message: _FakeMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.message = message
        self.alerts: list[str] = []
        self.answered = False

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answered = True
        if text:
            self.alerts.append(text)


def _buttons(markup: Any) -> list[tuple[str, str]]:
    """Плоский список кнопок клавиатуры: (текст, callback_data)."""
    assert isinstance(markup, InlineKeyboardMarkup)
    return [
        (button.text, button.callback_data or "")
        for row in markup.inline_keyboard
        for button in row
    ]


def _open(data: str, message: _FakeMessage | None = None) -> _FakeCallback:
    """Нажать кнопку страницы справочника и вернуть callback."""
    callback = _FakeCallback(data, message or _FakeMessage())
    asyncio.run(help_handler.open_page(callback))
    return callback


_FIRST = help_content.SECTIONS[0]
_LAST = help_content.SECTIONS[-1]


# --- Вход в справочник ---------------------------------------------------------


def test_help_command_shows_section_menu() -> None:
    message = _FakeMessage()
    asyncio.run(help_handler.show_help(message))
    assert len(message.answers) == 1
    text, kwargs = message.answers[0]
    assert kwargs["parse_mode"] == "HTML"
    buttons = _buttons(kwargs["reply_markup"])
    assert len(buttons) == len(help_content.SECTIONS)
    # Каждая кнопка меню открывает ПЕРВУЮ страницу своего раздела.
    assert all(cd.endswith(":0") for _, cd in buttons)
    assert "Справочник" in text


def test_open_section_edits_same_message() -> None:
    message = _FakeMessage()
    _open(help_page_cd(_FIRST.key, 0), message)
    assert len(message.edits) == 1, "страница должна перерисовать карточку"
    assert message.answers == [], "новых сообщений быть не должно"


def test_page_header_shows_section_and_position() -> None:
    callback = _open(help_page_cd(_FIRST.key, 0))
    assert callback.message is not None
    text = callback.message.edits[0][0]
    assert _FIRST.title in text
    assert f"1/{len(_FIRST.pages)}" in text


# --- Листание ------------------------------------------------------------------


def test_next_button_points_to_next_page() -> None:
    callback = _open(help_page_cd(_FIRST.key, 0))
    assert callback.message is not None
    buttons = _buttons(callback.message.edits[0][1]["reply_markup"])
    assert (("Далее ▶", help_page_cd(_FIRST.key, 1))) in buttons


def test_back_button_points_to_previous_page() -> None:
    callback = _open(help_page_cd(_FIRST.key, 2))
    assert callback.message is not None
    buttons = _buttons(callback.message.edits[0][1]["reply_markup"])
    assert ("◀ Назад", help_page_cd(_FIRST.key, 1)) in buttons


def test_back_on_first_page_leads_to_menu() -> None:
    callback = _open(help_page_cd(_FIRST.key, 0))
    assert callback.message is not None
    buttons = _buttons(callback.message.edits[0][1]["reply_markup"])
    assert ("◀ Назад", HELP_MENU_CD) in buttons


def test_last_page_opens_next_section() -> None:
    following = help_content.SECTIONS[1]
    callback = _open(help_page_cd(_FIRST.key, len(_FIRST.pages) - 1))
    assert callback.message is not None
    buttons = _buttons(callback.message.edits[0][1]["reply_markup"])
    targets = [cd for _, cd in buttons]
    assert help_page_cd(following.key, 0) in targets
    # Подпись называет пункт назначения, чтобы переход не был неожиданным.
    labels = [label for label, cd in buttons if cd == help_page_cd(following.key, 0)]
    assert following.title[:10] in labels[0]


def test_last_page_of_last_section_has_no_next() -> None:
    callback = _open(help_page_cd(_LAST.key, len(_LAST.pages) - 1))
    assert callback.message is not None
    buttons = _buttons(callback.message.edits[0][1]["reply_markup"])
    assert not any(label.startswith("Далее") or label.startswith("▶") for label, _ in buttons)
    assert ("≡ В меню", HELP_MENU_CD) in buttons


def test_menu_button_returns_to_menu() -> None:
    message = _FakeMessage()
    callback = _FakeCallback(HELP_MENU_CD, message)
    asyncio.run(help_handler.back_to_menu(callback))
    text, kwargs = message.edits[0]
    assert "Выберите раздел" in text
    assert len(_buttons(kwargs["reply_markup"])) == len(help_content.SECTIONS)


# --- Краевые случаи ------------------------------------------------------------


def test_unknown_section_redraws_menu_with_toast() -> None:
    callback = _open("help:s:nosuch:0")
    assert callback.message is not None
    assert "Выберите раздел" in callback.message.edits[0][0]
    assert callback.alerts, "оператор должен понять, почему открылось меню"


@pytest.mark.parametrize(("raw", "expected"), [(99, "last"), (-1, "first")])
def test_page_out_of_range_is_clamped(raw: int, expected: str) -> None:
    parsed = help_handler.parse_page_callback(help_page_cd(_FIRST.key, raw))
    assert parsed is not None
    _, page = parsed
    assert page == (len(_FIRST.pages) - 1 if expected == "last" else 0)


@pytest.mark.parametrize("data", ["help:s:", "help:s::0", f"help:s:{_FIRST.key}:abc", "help:x"])
def test_malformed_callback_does_not_crash(data: str) -> None:
    callback = _open(data)
    assert callback.answered


def test_not_modified_error_is_swallowed() -> None:
    message = _FakeMessage(edit_error=_bad_request("Bad Request: message is not modified"))
    _open(help_page_cd(_FIRST.key, 0), message)
    assert message.answers == [], "повторное нажатие не должно плодить сообщения"


def test_uneditable_message_falls_back_to_new_card() -> None:
    message = _FakeMessage(edit_error=_bad_request("Bad Request: message can't be edited"))
    _open(help_page_cd(_FIRST.key, 0), message)
    assert len(message.answers) == 1, "вместо мёртвой кнопки шлём новую карточку"
    assert _FIRST.title in message.answers[0][0]


def test_inaccessible_message_is_ignored() -> None:
    callback = _FakeCallback(help_page_cd(_FIRST.key, 0), None)
    asyncio.run(help_handler.open_page(callback))
    assert callback.answered, "спиннер надо погасить в любом случае"


@pytest.mark.parametrize(
    "data", [help_page_cd(_FIRST.key, 0), "help:s:nosuch:0", "help:s:", HELP_MENU_CD]
)
def test_callback_is_always_answered(data: str) -> None:
    message = _FakeMessage()
    callback = _FakeCallback(data, message)
    handler = help_handler.back_to_menu if data == HELP_MENU_CD else help_handler.open_page
    asyncio.run(handler(callback))
    assert callback.answered


def test_long_page_is_clipped_below_limit() -> None:
    text = "\n".join(f"строка {i}" * 20 for i in range(200))
    clipped = help_handler._clip(text, 500)
    assert len(clipped) <= 500 + 2  # хвостовое «…» на своей строке
    assert clipped.endswith("…")
    assert clipped[: clipped.rfind("\n")] in text, "обрезка идёт по границе строки"


# --- Доступ --------------------------------------------------------------------


def test_router_guards_operators_on_both_event_types() -> None:
    """Справка приватная: фильтр оператора нужен и на команду, и на кнопки."""
    for observer in (help_handler.router.message, help_handler.router.callback_query):
        guards = observer._handler.filters or []
        assert any(isinstance(item.callback, OperatorOnly) for item in guards)


def test_help_router_registered_before_fsm_routers() -> None:
    """`/help` должен работать и посреди сценария — значит, раньше catch-all роутеров."""
    names = [router.name for router in bot_routers()]
    position = names.index("help")
    for scenario in ("send_brief", "creative", "ad_accounts", "brief_card"):
        assert position < names.index(scenario), f"{scenario} перехватит /help"
