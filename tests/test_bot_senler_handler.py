"""Хендлер `/senler_unlink`: снять привязку токена сообщества (дефект 3, ревью 2026-08-24).

До этой доработки `db.community_tokens.delete_community_token` существовал, но
не вызывался ниоткуда — у оператора не было способа снять устаревшую или
ошибочную привязку иначе как правкой базы руками. Тонкий хендлер: ввод и
рендер здесь, вся логика — в ядре через `bot/api_client` (CLAUDE.md §1.3), тем
же приёмом, что у `/senler_token` и `/cabinets`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import CommunityTokenNotFound, CoreUnavailable
from bot.handlers import senler
from bot.states import UnlinkCommunityToken

_OPERATOR_ID = 111


class _FakeState:
    def __init__(self) -> None:
        self.state: Any = None

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def clear(self) -> None:
        self.state = None


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


@pytest.fixture(autouse=True)
def _fake_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Хендлер проверяет `isinstance(..., Message)` — подменяем сам тип."""
    monkeypatch.setattr(senler, "Message", _FakeMessage)


def test_command_asks_for_the_community_reference() -> None:
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(senler.start_unlink(message, state))
    assert state.state == UnlinkCommunityToken.entering_reference
    assert message.answers


def test_unlink_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete(reference: str) -> None:
        assert reference == "djbeauty"

    monkeypatch.setattr("bot.api_client.delete_community_token", fake_delete)
    message, state = _FakeMessage("djbeauty"), _FakeState()
    asyncio.run(senler.got_reference(message, state))
    assert state.state is None
    assert any("djbeauty" in a for a in message.answers)


def test_unlink_reports_when_there_was_nothing_to_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ обязан быть понятным и когда привязки не было — не молчать и не
    выдавать это за успех."""

    async def fake_delete(reference: str) -> None:
        raise CommunityTokenNotFound(reference)

    monkeypatch.setattr("bot.api_client.delete_community_token", fake_delete)
    message, state = _FakeMessage("unknown-address"), _FakeState()
    asyncio.run(senler.got_reference(message, state))
    assert "не было" in message.answers[0].lower()


def test_unlink_reports_core_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_delete(reference: str) -> None:
        raise CoreUnavailable("boom")

    monkeypatch.setattr("bot.api_client.delete_community_token", fake_delete)
    message, state = _FakeMessage("djbeauty"), _FakeState()
    asyncio.run(senler.got_reference(message, state))
    assert "недоступен" in message.answers[0].lower()


def test_unlink_rejects_empty_input() -> None:
    message, state = _FakeMessage("   "), _FakeState()
    asyncio.run(senler.got_reference(message, state))
    assert "Пустое" in message.answers[0]
