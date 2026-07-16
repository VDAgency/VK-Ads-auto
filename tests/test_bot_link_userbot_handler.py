"""Тесты хендлера `/link_userbot`: FSM-шаги per-sender, мок-режим, удаление, redact."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import UserbotAuthError, UserbotHealth, UserbotUnavailable
from bot.handlers import link_userbot
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
        self.deleted = False

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)

    async def delete(self) -> None:
        self.deleted = True


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

    code_msg = _FakeMessage("12345")
    asyncio.run(link_userbot.enter_code(code_msg, state))
    assert code_msg.deleted  # секрет стёрт даже в демо
    assert state.state is None  # завершили демо на коде


# --- Реальный режим: удаление секретов, sender_id -----------------------------


def test_code_message_deleted_and_sender_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)
    captured: dict[str, Any] = {}

    async def fake_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        captured["sender_id"] = sender_id
        return False

    monkeypatch.setattr("bot.api_client.userbot_submit_code", fake_code)
    state = _FakeState()
    state.data = {"phone": "+7999", "phone_code_hash": "hash"}
    message = _FakeMessage("54321")
    asyncio.run(link_userbot.enter_code(message, state))

    assert message.deleted
    assert any("подключён" in a for a in message.answers)
    assert state.state is None
    # Сессия привязывается к вызвавшему оператору.
    assert captured["sender_id"] == _OPERATOR_ID


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


def test_code_triggers_password_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)

    async def fake_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        return True  # 2FA включена

    monkeypatch.setattr("bot.api_client.userbot_submit_code", fake_code)
    state = _FakeState()
    state.data = {"phone": "+7999", "phone_code_hash": "hash"}
    message = _FakeMessage("11111")
    asyncio.run(link_userbot.enter_code(message, state))

    assert state.state == LinkUserbot.entering_password


# --- Redact в логах ----------------------------------------------------------


def test_code_redacted_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: False)
    state = _FakeState()
    message = _FakeMessage("999888")
    with caplog.at_level(logging.INFO, logger="bot.handlers.link_userbot"):
        asyncio.run(link_userbot.enter_code(message, state))

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "999888" not in joined  # сам код не утёк
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


def test_invalid_code_shows_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.userbot_configured", lambda: True)

    async def fake_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        raise UserbotAuthError("phone_code_invalid")

    monkeypatch.setattr("bot.api_client.userbot_submit_code", fake_code)
    state = _FakeState()
    state.data = {"phone": "+7999", "phone_code_hash": "hash"}
    message = _FakeMessage("00000")
    asyncio.run(link_userbot.enter_code(message, state))

    assert any("Неверный код" in a for a in message.answers)
    assert message.deleted  # секрет всё равно стёрли
    assert state.state is None


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
