"""Хендлер `/set_password`: задать пароль входа в веб-кабинет оператора.

Экран входа в веб-админку обещает «Пароль задаётся командой /set_password в
боте» — эта команда была забыта при добавлении возвратного входа паролём
(spec 2026-08-31, эндпоинты `POST /admin/login`/`POST /admin/password` уже на
проде). Отдельно закрепляем безопасность: сообщение с паролем удаляется из
чата, а в логи попадает только его длина — тот же приём, что в
`tests/test_bot_ad_accounts_handler.py`.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from bot.access import OperatorOnly
from bot.api_client import CoreUnavailable, WeakPassword
from bot.handlers import set_password
from bot.states import SetPassword

_OPERATOR_ID = 555
PASSWORD = "a-very-strong-password-0000"


class _FakeState:
    def __init__(self) -> None:
        self.state: Any = None
        self.cleared = False

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def clear(self) -> None:
        self.state = None
        self.cleared = True


class _FakeMessage:
    def __init__(self, text: str = "", user_id: int | None = _OPERATOR_ID) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.answers: list[str] = []
        self.deleted = False

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)

    async def delete(self) -> None:
        self.deleted = True


# --- Доступ --------------------------------------------------------------------


def test_router_requires_operator_on_message() -> None:
    """Команда приватная: обычный посторонний её не увидит."""
    guards = set_password.router.message._handler.filters or []
    assert any(isinstance(item.callback, OperatorOnly) for item in guards)


def test_operator_is_allowed_and_stranger_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from config.settings import Settings

    settings = Settings(_env_file=None, operator_telegram_ids=frozenset({_OPERATOR_ID}))
    monkeypatch.setattr("bot.access.get_settings", lambda: settings)

    operator_event: Any = SimpleNamespace(from_user=SimpleNamespace(id=_OPERATOR_ID))
    stranger_event: Any = SimpleNamespace(from_user=SimpleNamespace(id=999))
    assert asyncio.run(OperatorOnly()(operator_event)) is True
    assert asyncio.run(OperatorOnly()(stranger_event)) is False


# --- /set_password: приглашение -------------------------------------------------


def test_start_asks_for_password_and_sets_state() -> None:
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(set_password.start(message, state))
    assert state.state == SetPassword.entering_password
    assert any("10 символов" in answer for answer in message.answers)


# --- Приём пароля ----------------------------------------------------------------


def test_password_message_is_deleted_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_set(operator_telegram_id: int, password: str) -> None:
        return None

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage(PASSWORD), _FakeState()
    asyncio.run(set_password.got_password(message, state))
    assert message.deleted is True


def test_password_is_never_echoed_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_set(operator_telegram_id: int, password: str) -> None:
        return None

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage(PASSWORD), _FakeState()
    asyncio.run(set_password.got_password(message, state))
    assert all(PASSWORD not in answer for answer in message.answers)


def test_password_is_redacted_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fake_set(operator_telegram_id: int, password: str) -> None:
        return None

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage(PASSWORD), _FakeState()
    with caplog.at_level(logging.INFO):
        asyncio.run(set_password.got_password(message, state))
    assert PASSWORD not in caplog.text
    assert "redacted" in caplog.text


def test_success_confirms_and_links_to_the_web_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_set(operator_telegram_id: int, password: str) -> None:
        assert operator_telegram_id == _OPERATOR_ID
        assert password == PASSWORD

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage(PASSWORD), _FakeState()
    asyncio.run(set_password.got_password(message, state))
    assert any("admin.html" in answer for answer in message.answers)
    assert any(
        "номер" in answer.lower() and "пароль" in answer.lower() for answer in message.answers
    )
    assert state.cleared is True


def test_empty_password_asks_again_without_calling_core(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_set(operator_telegram_id: int, password: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage("   "), _FakeState()
    asyncio.run(set_password.got_password(message, state))
    assert any("Пустое сообщение" in answer for answer in message.answers)
    assert called is False


def test_weak_password_shows_the_human_reason_from_core(monkeypatch: pytest.MonkeyPatch) -> None:
    reason = "Пароль короче десяти символов — так его слишком просто подобрать"

    async def fake_set(operator_telegram_id: int, password: str) -> None:
        raise WeakPassword(reason)

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage("short"), _FakeState()
    asyncio.run(set_password.got_password(message, state))
    assert any(reason in answer for answer in message.answers)
    assert message.deleted is True


def test_core_unavailable_is_reported_honestly_not_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_set(operator_telegram_id: int, password: str) -> None:
        raise CoreUnavailable("down")

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage(PASSWORD), _FakeState()
    asyncio.run(set_password.got_password(message, state))
    assert any("недоступен" in answer for answer in message.answers)
    assert not any("admin.html" in answer for answer in message.answers)


def test_no_user_is_handled_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_set(operator_telegram_id: int, password: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("bot.api_client.set_operator_password", fake_set)
    message, state = _FakeMessage(PASSWORD, user_id=None), _FakeState()
    asyncio.run(set_password.got_password(message, state))
    assert called is False
