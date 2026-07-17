"""Тесты хендлера `/link_kotbot`: FSM, удаление секретов, redact, мок-режим (spec §5).

Пароль и код подтверждения вводятся сообщениями и сразу удаляются из чата;
в логах секреты маскируются. Коды kotbot/VK — не телеграмные, поэтому кейпад
не нужен (в отличие от /link_userbot).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import KotbotAuthError, KotbotAuthResult, KotbotUnavailable
from bot.handlers import link_kotbot
from bot.keyboards import kotbot_strategy_keyboard
from bot.states import LinkKotbot

_OPERATOR_ID = 111


class _FakeState:
    """Минимальный FSMContext: хранит state + data в памяти."""

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
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.answers: list[str] = []
        self.answer_kwargs: list[dict[str, Any]] = []
        self.edits: list[str] = []
        self.deleted = False

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)
        self.answer_kwargs.append(kwargs)

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append(text)

    async def delete(self) -> None:
        self.deleted = True


class _FakeCallback:
    """CallbackQuery с экраном сценария (`message`)."""

    def __init__(self, data: str, message: _FakeMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.message = message or _FakeMessage()
        self.alerts: list[str] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        if text:
            self.alerts.append(text)


def _configured(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr("bot.api_client.kotbot_configured", lambda: value)


def _result(status: str, attempt_id: str | None = None, hint: str = "") -> KotbotAuthResult:
    return KotbotAuthResult(status=status, attempt_id=attempt_id, hint=hint)


# --- Клавиатура -----------------------------------------------------------------


def test_strategy_keyboard_has_both_strategies_and_cancel() -> None:
    buttons = [b for row in kotbot_strategy_keyboard().inline_keyboard for b in row]
    datas = {b.callback_data for b in buttons}
    assert datas == {"kotbot:email", "kotbot:vk", "kotbot:cancel"}


# --- Мок-режим (KOTBOT_BASE_URL пуст) ---------------------------------------------


def test_mock_mode_start_shows_notice_and_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, False)
    message = _FakeMessage()
    state = _FakeState()
    asyncio.run(link_kotbot.start_link(message, state))

    assert any("Демо" in a for a in message.answers)
    assert state.state == LinkKotbot.choosing_strategy
    assert message.answer_kwargs[-1].get("reply_markup") is not None


def test_mock_mode_full_flow_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, False)
    called: list[str] = []

    async def spy_start(strategy: str, login: str, password: str) -> KotbotAuthResult:
        called.append("start")
        return _result("ok")

    monkeypatch.setattr("bot.api_client.kotbot_start_auth", spy_start)
    state = _FakeState()

    screen = _FakeMessage()
    asyncio.run(link_kotbot.choose_strategy(_FakeCallback("kotbot:email", screen), state))
    assert state.state == LinkKotbot.entering_login

    login_msg = _FakeMessage("ops@example.com")
    asyncio.run(link_kotbot.enter_login(login_msg, state))
    assert state.state == LinkKotbot.entering_password

    password_msg = _FakeMessage("p@ssw0rd")
    asyncio.run(link_kotbot.enter_password(password_msg, state))

    assert password_msg.deleted  # секрет стёрт даже в демо
    assert any("Демо" in a for a in password_msg.answers)
    assert state.state is None
    assert called == []  # в сеть ничего не уходило


# --- Выбор стратегии и отмена ------------------------------------------------------


def test_choose_vk_strategy_prompts_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)
    state = _FakeState()
    state.state = LinkKotbot.choosing_strategy
    screen = _FakeMessage()

    asyncio.run(link_kotbot.choose_strategy(_FakeCallback("kotbot:vk", screen), state))

    assert state.data["strategy"] == "vk"
    assert state.state == LinkKotbot.entering_login
    assert any("VK" in e for e in screen.edits)


def test_cancel_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)
    state = _FakeState()
    state.state = LinkKotbot.choosing_strategy
    screen = _FakeMessage()

    asyncio.run(link_kotbot.choose_strategy(_FakeCallback("kotbot:cancel", screen), state))

    assert state.state is None
    assert any("Отменено" in e for e in screen.edits)


def test_unknown_strategy_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)
    state = _FakeState()
    state.state = LinkKotbot.choosing_strategy

    asyncio.run(link_kotbot.choose_strategy(_FakeCallback("kotbot:telegram"), state))

    assert state.state == LinkKotbot.choosing_strategy
    assert "strategy" not in state.data


# --- Успешный вход -------------------------------------------------------------------


def _password_state(strategy: str = "email") -> _FakeState:
    state = _FakeState()
    state.state = LinkKotbot.entering_password
    state.data = {"strategy": strategy, "login": "ops@example.com"}
    return state


def test_password_deleted_and_ok_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)
    captured: dict[str, Any] = {}

    async def fake_start(strategy: str, login: str, password: str) -> KotbotAuthResult:
        captured.update(strategy=strategy, login=login, password=password)
        return _result("ok")

    monkeypatch.setattr("bot.api_client.kotbot_start_auth", fake_start)
    state = _password_state()
    message = _FakeMessage("p@ssw0rd")
    asyncio.run(link_kotbot.enter_password(message, state))

    assert message.deleted
    assert captured == {"strategy": "email", "login": "ops@example.com", "password": "p@ssw0rd"}
    assert any("подключён" in a for a in message.answers)
    assert state.state is None


def test_code_required_moves_to_code_step(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)

    async def fake_start(strategy: str, login: str, password: str) -> KotbotAuthResult:
        return _result("code_required", attempt_id="att-1", hint="Код отправлен на почту")

    monkeypatch.setattr("bot.api_client.kotbot_start_auth", fake_start)
    state = _password_state()
    message = _FakeMessage("p@ssw0rd")
    asyncio.run(link_kotbot.enter_password(message, state))

    assert state.state == LinkKotbot.entering_code
    assert state.data["attempt_id"] == "att-1"
    assert any("код" in a.lower() for a in message.answers)
    assert any("удалим" in a for a in message.answers)


def _code_state() -> _FakeState:
    state = _FakeState()
    state.state = LinkKotbot.entering_code
    state.data = {"strategy": "email", "login": "ops@example.com", "attempt_id": "att-1"}
    return state


def test_code_deleted_and_submitted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)
    captured: dict[str, Any] = {}

    async def fake_code(attempt_id: str, code: str) -> None:
        captured.update(attempt_id=attempt_id, code=code)

    monkeypatch.setattr("bot.api_client.kotbot_submit_code", fake_code)
    state = _code_state()
    message = _FakeMessage("123456")
    asyncio.run(link_kotbot.enter_code(message, state))

    assert message.deleted
    assert captured == {"attempt_id": "att-1", "code": "123456"}
    assert any("подключён" in a for a in message.answers)
    assert state.state is None


# --- Redact в логах ------------------------------------------------------------------


def test_password_redacted_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configured(monkeypatch, True)

    async def fake_start(strategy: str, login: str, password: str) -> KotbotAuthResult:
        return _result("ok")

    monkeypatch.setattr("bot.api_client.kotbot_start_auth", fake_start)
    state = _password_state()
    message = _FakeMessage("hunter2-secret")
    with caplog.at_level(logging.DEBUG, logger="bot.handlers.link_kotbot"):
        asyncio.run(link_kotbot.enter_password(message, state))

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "hunter2-secret" not in joined
    assert "redacted" in joined


def test_code_redacted_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configured(monkeypatch, True)

    async def fake_code(attempt_id: str, code: str) -> None:
        return None

    monkeypatch.setattr("bot.api_client.kotbot_submit_code", fake_code)
    state = _code_state()
    message = _FakeMessage("987654")
    with caplog.at_level(logging.DEBUG, logger="bot.handlers.link_kotbot"):
        asyncio.run(link_kotbot.enter_code(message, state))

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "987654" not in joined
    assert "redacted" in joined


def test_redact_helper_hides_value() -> None:
    assert link_kotbot._redact("hunter2") == "<redacted:7>"


# --- Подсказки ошибок -----------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("invalid_credentials", "Неверный логин или пароль"),
        ("captcha_required", "капчу"),
        ("not_configured", "KOTBOT_SECRET_KEY"),
        ("not_implemented", "K-PR3"),
    ],
)
def test_start_error_hints(monkeypatch: pytest.MonkeyPatch, code: str, expected: str) -> None:
    _configured(monkeypatch, True)

    async def fake_start(strategy: str, login: str, password: str) -> KotbotAuthResult:
        raise KotbotAuthError(code)

    monkeypatch.setattr("bot.api_client.kotbot_start_auth", fake_start)
    state = _password_state()
    message = _FakeMessage("p@ss")
    asyncio.run(link_kotbot.enter_password(message, state))

    assert message.deleted
    assert any(expected in a for a in message.answers)
    assert state.state is None


@pytest.mark.parametrize(
    "code,expected",
    [
        ("code_invalid", "Неверный код"),
        ("attempt_expired", "Время вышло"),
    ],
)
def test_code_error_hints(monkeypatch: pytest.MonkeyPatch, code: str, expected: str) -> None:
    _configured(monkeypatch, True)

    async def fake_code(attempt_id: str, code_value: str) -> None:
        raise KotbotAuthError(code)

    monkeypatch.setattr("bot.api_client.kotbot_submit_code", fake_code)
    state = _code_state()
    message = _FakeMessage("000000")
    asyncio.run(link_kotbot.enter_code(message, state))

    assert message.deleted
    assert any(expected in a for a in message.answers)
    assert state.state is None


def test_unknown_error_code_still_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)

    async def fake_start(strategy: str, login: str, password: str) -> KotbotAuthResult:
        raise KotbotAuthError("flow_failed:login")

    monkeypatch.setattr("bot.api_client.kotbot_start_auth", fake_start)
    state = _password_state()
    message = _FakeMessage("p@ss")
    asyncio.run(link_kotbot.enter_password(message, state))

    assert any("flow_failed:login" in a for a in message.answers)


# --- Недоступность сервиса --------------------------------------------------------------


def test_unavailable_on_password_step(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)

    async def boom(strategy: str, login: str, password: str) -> KotbotAuthResult:
        raise KotbotUnavailable("down")

    monkeypatch.setattr("bot.api_client.kotbot_start_auth", boom)
    state = _password_state()
    message = _FakeMessage("p@ss")
    asyncio.run(link_kotbot.enter_password(message, state))

    assert message.deleted
    assert any("недоступен" in a for a in message.answers)
    assert state.state is None


def test_unavailable_on_code_step(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch, True)

    async def boom(attempt_id: str, code: str) -> None:
        raise KotbotUnavailable("down")

    monkeypatch.setattr("bot.api_client.kotbot_submit_code", boom)
    state = _code_state()
    message = _FakeMessage("123")
    asyncio.run(link_kotbot.enter_code(message, state))

    assert message.deleted
    assert any("недоступен" in a for a in message.answers)
    assert state.state is None
