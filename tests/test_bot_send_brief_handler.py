"""Тесты хендлера `/send_brief` (PR#5): создание инвайта через ядро, сценарии §8.1."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import ContactNotRecognized, CoreUnavailable, InviteCreated
from bot.handlers import send_brief
from bot.states import SendBrief

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

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


def _invite(
    status: str, channel: str, *, fallback: str | None = None, error: str | None = None
) -> InviteCreated:
    return InviteCreated(
        invite_id=1, status=status, channel=channel, fallback_text=fallback, error=error
    )


def _run_got_contact(
    monkeypatch: pytest.MonkeyPatch,
    result: InviteCreated | Exception,
    contact: str = "@ivanov",
) -> tuple[_FakeMessage, _FakeState, dict[str, Any]]:
    """Прогнать got_contact с замоканным create_invite; вернуть message/state/захват."""
    captured: dict[str, Any] = {}

    async def fake_create(variant: str, raw: str, operator_telegram_id: int) -> InviteCreated:
        captured.update(variant=variant, contact=raw, operator_telegram_id=operator_telegram_id)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("bot.api_client.create_invite", fake_create)
    state = _FakeState()
    state.state = SendBrief.entering_contact
    state.data = {"variant": "individual"}
    message = _FakeMessage(contact)
    asyncio.run(send_brief.got_contact(message, state))
    return message, state, captured


# --- Три сценария §8.1 --------------------------------------------------------


def test_sent_via_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    message, state, captured = _run_got_contact(monkeypatch, _invite("sent", "telegram"))
    assert message.answers == ["✅ Отправлено клиенту @ivanov через Telegram. Ожидаем бриф."]
    assert state.state is None
    # Отправителем инвайта уходит вызвавший оператор.
    assert captured["operator_telegram_id"] == _OPERATOR_ID
    assert captured["variant"] == "individual"


def test_sent_via_email(monkeypatch: pytest.MonkeyPatch) -> None:
    message, _, _ = _run_got_contact(monkeypatch, _invite("sent", "email"), contact="ivan@mail.ru")
    assert message.answers == ["✅ Отправлено клиенту ivan@mail.ru через email. Ожидаем бриф."]


def test_sent_manual_shows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    message, _, _ = _run_got_contact(
        monkeypatch,
        _invite("sent", "manual", fallback="Здравствуйте! Ссылка: https://x?t=abc"),
        contact="+79990001122",
    )
    text = message.answers[0]
    assert "вручную" in text
    assert "Здравствуйте! Ссылка: https://x?t=abc" in text


def test_failed_username_not_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    message, state, _ = _run_got_contact(
        monkeypatch,
        _invite("failed", "telegram", fallback="текст-фолбэк", error="username_not_occupied"),
    )
    text = message.answers[0]
    assert "пользователь не найден" in text
    assert "текст-фолбэк" in text
    assert "/send_brief" in text
    assert state.state is None


def test_failed_sender_not_authorized_hints_link(monkeypatch: pytest.MonkeyPatch) -> None:
    message, _, _ = _run_got_contact(
        monkeypatch,
        _invite("failed", "telegram", fallback="текст", error="sender_not_authorized"),
    )
    assert "/link_userbot" in message.answers[0]


def test_failed_email_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    message, _, _ = _run_got_contact(
        monkeypatch,
        _invite("failed", "email", fallback="текст", error="smtp_unreachable"),
        contact="ivan@mail.ru",
    )
    assert "SMTP" in message.answers[0]


# --- Ошибки взаимодействия с ядром ---------------------------------------------


def test_unrecognized_contact_keeps_state(monkeypatch: pytest.MonkeyPatch) -> None:
    message, state, _ = _run_got_contact(monkeypatch, ContactNotRecognized("bad"), contact="???")
    assert any("Не распознал контакт" in a for a in message.answers)
    # Состояние не сброшено — оператор вводит контакт заново.
    assert state.state == SendBrief.entering_contact


def test_core_unavailable_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    message, state, _ = _run_got_contact(monkeypatch, CoreUnavailable("down"))
    assert any("недоступен" in a for a in message.answers)
    assert state.state is None


# --- Баннер о неподключённой сессии ---------------------------------------------


def _run_start(monkeypatch: pytest.MonkeyPatch, authorized: bool | None) -> _FakeMessage:
    monkeypatch.setattr("bot.userbot_watch.is_authorized", lambda sender_id: authorized)
    message = _FakeMessage("/send_brief")
    state = _FakeState()
    asyncio.run(send_brief.start_send_brief(message, state))
    return message


def test_banner_when_session_not_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _run_start(monkeypatch, authorized=False)
    assert any("юзербот не подключён" in a for a in message.answers)


def test_no_banner_when_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _run_start(monkeypatch, authorized=True)
    assert not any("не подключён" in a for a in message.answers)


def test_no_banner_when_state_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # None = поллер ещё не опросил сервис — зря не пугаем.
    message = _run_start(monkeypatch, authorized=None)
    assert not any("не подключён" in a for a in message.answers)
