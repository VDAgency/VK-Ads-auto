"""Тесты хендлера карточки брифа: открытие, FSM правок `номер.значение`, клавиатуры."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bot.api_client import BriefCard, BriefFieldItem, BriefNotFound, CoreUnavailable, InviteItem
from bot.handlers import brief_card
from bot.handlers.pending import _recent_buttons
from bot.keyboards import brief_card_keyboard, recent_briefs_keyboard
from bot.states import EditBrief


class _FakeState:
    def __init__(self) -> None:
        self.state: Any = None
        self.data: dict[str, Any] = {}

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def update_data(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def clear(self) -> None:
        self.state = None
        self.data = {}


class _FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.answers: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None, **kwargs: Any) -> None:
        self.answers.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = _FakeMessage()
        self.answered = False

    async def answer(self, *args: Any, **kwargs: Any) -> None:
        self.answered = True


def _card(status: str = "received", has_creative: bool = False) -> BriefCard:
    return BriefCard(
        brief_id=7,
        variant="individual",
        status=status,
        client_name="Вячеслав",
        client_email="v@example.com",
        client_phone="+79990000000",
        client_telegram="@v",
        fields=[
            BriefFieldItem(n=1, label="Как обращаться", value="Вячеслав"),
            BriefFieldItem(n=2, label="География", value=""),
        ],
        has_creative=has_creative,
        campaign_status=None,
    )


# --- Клавиатуры ----------------------------------------------------------------


def test_recent_briefs_keyboard_builds_buttons() -> None:
    keyboard = recent_briefs_keyboard([(7, "1. Вячеслав"), (8, "2. Иван")])
    assert keyboard is not None
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert datas == ["brief:7", "brief:8"]


def test_recent_briefs_keyboard_empty_is_none() -> None:
    assert recent_briefs_keyboard([]) is None


def test_brief_card_keyboard_has_edit() -> None:
    keyboard = brief_card_keyboard(7)
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "edit:7" in datas


def test_recent_buttons_skip_missing_brief_id() -> None:
    items = [
        InviteItem(
            contact="@a",
            variant="individual",
            channel="telegram",
            sent_at=None,
            received_at=None,
            waiting_days=0,
            contact_name="Аня",
            brief_id=7,
        ),
        InviteItem(
            contact="b@e.c",
            variant="individual",
            channel="email",
            sent_at=None,
            received_at=None,
            waiting_days=0,
            contact_name=None,
            brief_id=None,
        ),
    ]
    # Первый — с brief_id (кнопка), второй — без (пропущен); нумерация с 1.
    assert _recent_buttons(items) == [(7, "1. Аня — @a")]


# --- Открытие карточки ---------------------------------------------------------


def test_open_brief_renders_card(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    async def fake_get(brief_id: int) -> BriefCard:
        captured["id"] = brief_id
        return _card()

    monkeypatch.setattr("bot.api_client.get_brief", fake_get)
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)  # пройти isinstance-гард
    callback = _FakeCallback("brief:7")
    asyncio.run(brief_card.open_brief(callback, _FakeState()))

    assert captured["id"] == 7
    text, markup = callback.message.answers[0]
    assert "Бриф №7" in text
    assert "1. Как обращаться: Вячеслав" in text
    # Пустое поле показывается с прочерком.
    assert "2. География: —" in text
    assert "🖼 Креатив: не загружен" in text
    assert markup is not None  # клавиатура карточки
    assert callback.answered


def test_open_brief_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(brief_id: int) -> BriefCard:
        raise BriefNotFound(str(brief_id))

    monkeypatch.setattr("bot.api_client.get_brief", fake_get)
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)
    callback = _FakeCallback("brief:999")
    asyncio.run(brief_card.open_brief(callback, _FakeState()))

    assert any("не найден" in text.lower() for text, _ in callback.message.answers)


# --- FSM правок ----------------------------------------------------------------


def test_start_edit_sets_state_and_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)
    callback = _FakeCallback("edit:7")
    state = _FakeState()
    asyncio.run(brief_card.start_edit(callback, state))

    assert state.state == EditBrief.entering_edits
    assert state.data["brief_id"] == 7
    assert any("правки" in text.lower() for text, _ in callback.message.answers)


def test_apply_edits_updates_and_shows_card(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_update(brief_id: int, edits: dict[int, str]) -> tuple[BriefCard, list[int]]:
        captured["brief_id"] = brief_id
        captured["edits"] = edits
        return _card(), []

    monkeypatch.setattr("bot.api_client.update_brief", fake_update)
    state = _FakeState()
    state.state = EditBrief.entering_edits
    state.data = {"brief_id": 7}
    message = _FakeMessage("1. Иван Петров\n2. Москва")
    asyncio.run(brief_card.apply_edits(message, state))

    assert captured["brief_id"] == 7
    assert captured["edits"] == {1: "Иван Петров", 2: "Москва"}
    assert state.state is None  # правки применены — вышли из состояния
    text, markup = message.answers[-1]
    assert "Бриф №7" in text
    assert markup is not None


def test_apply_edits_empty_stays_in_state() -> None:
    state = _FakeState()
    state.state = EditBrief.entering_edits
    state.data = {"brief_id": 7}
    message = _FakeMessage("просто текст без номеров")
    asyncio.run(brief_card.apply_edits(message, state))

    assert state.state == EditBrief.entering_edits  # остаёмся, просим переввести
    assert any("формат" in text.lower() for text, _ in message.answers)


def test_apply_edits_reports_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_update(brief_id: int, edits: dict[int, str]) -> tuple[BriefCard, list[int]]:
        return _card(), [99]  # ядро сообщило: №99 вне диапазона

    monkeypatch.setattr("bot.api_client.update_brief", fake_update)
    state = _FakeState()
    state.state = EditBrief.entering_edits
    state.data = {"brief_id": 7}
    message = _FakeMessage("1. Иван\nстрока без номера\n99. вне диапазона")
    asyncio.run(brief_card.apply_edits(message, state))

    text = message.answers[-1][0]
    assert "не распознаны" in text.lower()
    assert "строка без номера" in text
    assert "99" in text


def test_apply_edits_core_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(brief_id: int, edits: dict[int, str]) -> tuple[BriefCard, list[int]]:
        raise CoreUnavailable("down")

    monkeypatch.setattr("bot.api_client.update_brief", boom)
    state = _FakeState()
    state.state = EditBrief.entering_edits
    state.data = {"brief_id": 7}
    message = _FakeMessage("1. Иван")
    asyncio.run(brief_card.apply_edits(message, state))

    assert state.state is None
    assert any("недоступен" in text.lower() for text, _ in message.answers)
