"""Тесты хендлера `/link_userbot`: код кнопками (анти-фишинг Telegram), FSM, redact.

Код входа нельзя отправлять сообщением — Telegram блокирует вход, если код был
в исходящем сообщении аккаунта. Поэтому набор кода — только инлайн-кнопками
(`code:*` callbacks), а текст на шаге кода удаляется с предупреждением.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import UserbotAuthError, UserbotHealth, UserbotUnavailable
from bot.handlers import link_userbot
from bot.keyboards import code_keypad
from bot.states import LinkUserbot

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
    """CallbackQuery с экраном набора (`message`) и тостами (`alerts`)."""

    def __init__(self, data: str, message: _FakeMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.message = message or _FakeMessage()
        self.alerts: list[str] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        if text:
            self.alerts.append(text)


def _press(state: _FakeState, screen: _FakeMessage, *buttons: str) -> _FakeCallback:
    """Нажать последовательность кнопок на одном экране; вернуть последний callback."""
    callback = _FakeCallback("", screen)
    for data in buttons:
        callback = _FakeCallback(data, screen)
        asyncio.run(link_userbot.code_button(callback, state))
    return callback


# --- Клавиатура ----------------------------------------------------------------


def test_code_keypad_has_all_digits_and_actions() -> None:
    buttons = [b for row in code_keypad().inline_keyboard for b in row]
    datas = {b.callback_data for b in buttons}
    assert {f"code:d:{d}" for d in "0123456789"} <= datas
    assert {"code:del", "code:ok", "code:cancel"} <= datas


# --- Мок-режим (USERBOT_BASE_URL пуст) --------------------------------------


def test_mock_mode_start_shows_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: False)
    message = _FakeMessage()
    state = _FakeState()
    asyncio.run(link_userbot.start_link(message, state))

    assert any("Демо" in a for a in message.answers)
    assert state.state == LinkUserbot.entering_phone


def test_mock_mode_full_flow_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: False)
    state = _FakeState()

    phone_msg = _FakeMessage("+79990001122")
    asyncio.run(link_userbot.enter_phone(phone_msg, state))
    assert state.state == LinkUserbot.entering_code
    # Клавиатура показана вместе с приглашением.
    assert phone_msg.answer_kwargs[-1].get("reply_markup") is not None

    screen = _FakeMessage()
    _press(state, screen, "code:d:1", "code:d:2", "code:ok")
    assert any("Демо" in e for e in screen.edits)
    assert state.state is None  # завершили демо на коде


# --- Набор кода кнопками -------------------------------------------------------


def _configure_real(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)
    captured: dict[str, Any] = {}

    async def fake_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        captured.update(
            sender_id=sender_id, phone=phone, code=code, phone_code_hash=phone_code_hash
        )
        return False

    monkeypatch.setattr("bot.api_client.userbot_submit_code", fake_code)
    return captured


def _code_state() -> _FakeState:
    state = _FakeState()
    state.state = LinkUserbot.entering_code
    state.data = {"phone": "+7999", "phone_code_hash": "hash", "code": ""}
    return state


def test_keypad_assembles_code_and_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _configure_real(monkeypatch)
    state = _code_state()
    screen = _FakeMessage()

    _press(state, screen, "code:d:5", "code:d:4", "code:d:3", "code:d:2", "code:d:1", "code:ok")

    assert captured["code"] == "54321"
    assert captured["sender_id"] == _OPERATOR_ID
    assert captured["phone"] == "+7999"
    assert any("подключён" in e for e in screen.edits)
    assert state.state is None
    # Экран с цифрами перезаписан — код не остался в чате.
    assert "5 4 3 2 1" not in screen.edits[-1]


def test_keypad_del_removes_last_digit(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_real(monkeypatch)
    state = _code_state()
    screen = _FakeMessage()

    _press(state, screen, "code:d:1", "code:d:2", "code:del")
    assert state.data["code"] == "1"


def test_keypad_cancel_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_real(monkeypatch)
    state = _code_state()
    screen = _FakeMessage()

    _press(state, screen, "code:d:9", "code:cancel")
    assert state.state is None
    assert any("Отменено" in e for e in screen.edits)


def test_keypad_ok_with_empty_code_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_real(monkeypatch)
    state = _code_state()
    screen = _FakeMessage()

    callback = _press(state, screen, "code:ok")
    assert any("наберите код" in a.lower() for a in callback.alerts)
    assert state.state == LinkUserbot.entering_code  # остаёмся на шаге


def test_keypad_code_triggers_password_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)

    async def fake_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        return True  # 2FA включена

    monkeypatch.setattr("bot.api_client.userbot_submit_code", fake_code)
    state = _code_state()
    screen = _FakeMessage()

    _press(state, screen, "code:d:1", "code:ok")
    assert state.state == LinkUserbot.entering_password
    assert any("2FA" in e for e in screen.edits)


def test_keypad_invalid_code_shows_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)

    async def fake_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        raise UserbotAuthError("phone_code_invalid")

    monkeypatch.setattr("bot.api_client.userbot_submit_code", fake_code)
    state = _code_state()
    screen = _FakeMessage()

    _press(state, screen, "code:d:0", "code:ok")
    assert any("Неверный код" in e for e in screen.edits)
    assert state.state is None


def test_keypad_length_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_real(monkeypatch)
    state = _code_state()
    screen = _FakeMessage()

    callback = _press(state, screen, *(["code:d:7"] * 9))
    assert len(state.data["code"]) == 8  # девятая цифра не добавилась
    assert callback.alerts  # оператор получил подсказку


# --- Текст на шаге кода: удаляем и предупреждаем --------------------------------


def test_typed_code_deleted_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)
    state = _code_state()
    message = _FakeMessage("54321")
    asyncio.run(link_userbot.code_typed_text(message, state))

    assert message.deleted
    assert any("кнопками" in a for a in message.answers)
    assert state.state == LinkUserbot.entering_code  # FSM не сломан


def test_typed_code_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)
    state = _code_state()
    message = _FakeMessage("999888")
    with caplog.at_level(logging.INFO, logger="bot.handlers.link_userbot"):
        asyncio.run(link_userbot.code_typed_text(message, state))

    assert "999888" not in " ".join(r.getMessage() for r in caplog.records)


def test_submitted_code_redacted_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure_real(monkeypatch)
    state = _code_state()
    screen = _FakeMessage()
    with caplog.at_level(logging.INFO, logger="bot.handlers.link_userbot"):
        _press(state, screen, "code:d:9", "code:d:8", "code:ok")

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "98" not in joined
    assert "redacted" in joined


def test_redact_helper_hides_value() -> None:
    assert link_userbot._redact("hunter2") == "<redacted:7>"


# --- Статус авторизации / недоступность --------------------------------------


def test_already_authorized_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)
    captured: dict[str, Any] = {}

    async def fake_status(sender_id: int) -> UserbotHealth:
        captured["sender_id"] = sender_id
        return UserbotHealth(authorized=True, phone="+79990001122")

    monkeypatch.setattr("bot.api_client.userbot_status", fake_status)
    message = _FakeMessage()
    state = _FakeState()
    asyncio.run(link_userbot.start_link(message, state))

    assert any("уже подключён" in a for a in message.answers)
    assert state.state is None  # FSM не запускаем
    # Проверяется сессия именно вызвавшего оператора.
    assert captured["sender_id"] == _OPERATOR_ID


def test_start_auth_unavailable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)

    async def fake_status(sender_id: int) -> UserbotHealth:
        return UserbotHealth(authorized=False)

    monkeypatch.setattr("bot.api_client.userbot_status", fake_status)
    message = _FakeMessage()
    state = _FakeState()
    asyncio.run(link_userbot.start_link(message, state))
    assert state.state == LinkUserbot.entering_phone

    # На вводе телефона сервис отвалился:
    async def boom(sender_id: int, phone: str) -> str:
        raise UserbotUnavailable("down")

    monkeypatch.setattr("bot.api_client.userbot_start_auth", boom)
    phone_msg = _FakeMessage("+7999")
    asyncio.run(link_userbot.enter_phone(phone_msg, state))

    assert any("недоступен" in a for a in phone_msg.answers)
    assert state.state is None


def test_code_unavailable_during_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)

    async def boom(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        raise UserbotUnavailable("down")

    monkeypatch.setattr("bot.api_client.userbot_submit_code", boom)
    state = _code_state()
    screen = _FakeMessage()
    _press(state, screen, "code:d:1", "code:ok")

    assert any("недоступен" in e for e in screen.edits)
    assert state.state is None


# --- Пароль 2FA (текстом — Telegram перехватывает только коды входа) ------------


def test_password_message_deleted_and_sender_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)
    captured: dict[str, Any] = {}

    async def fake_pwd(sender_id: int, password: str) -> None:
        captured["sender_id"] = sender_id

    monkeypatch.setattr("bot.api_client.userbot_submit_password", fake_pwd)
    state = _FakeState()
    message = _FakeMessage("secret-pass")
    asyncio.run(link_userbot.enter_password(message, state))

    assert message.deleted
    assert state.state is None
    assert captured["sender_id"] == _OPERATOR_ID


def test_invalid_password_shows_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)

    async def fake_pwd(sender_id: int, password: str) -> None:
        raise UserbotAuthError("password_invalid")

    monkeypatch.setattr("bot.api_client.userbot_submit_password", fake_pwd)
    state = _FakeState()
    message = _FakeMessage("wrong-pass")
    asyncio.run(link_userbot.enter_password(message, state))

    assert any("Неверный пароль" in a for a in message.answers)
    assert message.deleted
    assert state.state is None
