"""Тесты поллера здоровья kotbot (`bot/kotbot_watch.py`, spec §5).

Уведомление операторам шлётся РОВНО на переходе healthy(True)→unhealthy(False):
не на старте, не повторно, не при неизвестном состоянии (None).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiogram import Bot
from bot import kotbot_watch
from bot.api_client import KotbotHealth, KotbotStrategyHealth, KotbotUnavailable

_OPERATORS = frozenset({10, 20})


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _health(healthy: bool) -> KotbotHealth:
    flags = KotbotStrategyHealth(has_credentials=True, has_state=healthy, needs_reauth=False)
    return KotbotHealth(healthy=healthy, email=flags, vk=flags)


def _set_status(monkeypatch: pytest.MonkeyPatch, healthy: bool | None) -> None:
    """Замокать ответ kotbot_status: True/False — здоровье, None — недоступен."""

    async def fake_status() -> KotbotHealth:
        if healthy is None:
            raise KotbotUnavailable("down")
        return _health(healthy)

    monkeypatch.setattr("bot.api_client.kotbot_status", fake_status)


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    kotbot_watch.reset()
    yield
    kotbot_watch.reset()


@pytest.fixture()
def _operators(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bot.kotbot_watch.get_settings",
        lambda: SimpleNamespace(operator_telegram_ids=_OPERATORS),
    )


def _check(monkeypatch: pytest.MonkeyPatch, bot: _FakeBot, healthy: bool | None) -> None:
    _set_status(monkeypatch, healthy)
    asyncio.run(kotbot_watch.check_once(cast(Bot, bot)))


def test_unknown_before_first_poll() -> None:
    assert kotbot_watch.is_healthy() is None


def test_refresh_populates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_status(monkeypatch, True)
    asyncio.run(kotbot_watch.refresh_once())
    assert kotbot_watch.is_healthy() is True

    _set_status(monkeypatch, None)
    asyncio.run(kotbot_watch.refresh_once())
    # Кеш не «врёт»: состояние неизвестно, а не «всё ещё здоров».
    assert kotbot_watch.is_healthy() is None


@pytest.mark.usefixtures("_operators")
def test_notifies_exactly_on_true_to_false_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _FakeBot()
    _check(monkeypatch, bot, True)
    assert bot.sent == []  # стало здорово — не уведомляем

    _check(monkeypatch, bot, False)
    # Переход True→False: уведомлены ВСЕ операторы, ровно по разу.
    assert sorted(chat_id for chat_id, _ in bot.sent) == sorted(_OPERATORS)
    assert all("/link_kotbot" in text for _, text in bot.sent)


@pytest.mark.usefixtures("_operators")
def test_no_notification_on_startup_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _FakeBot()
    _check(monkeypatch, bot, False)  # None→False: старт с нездоровым — молчим
    assert bot.sent == []


@pytest.mark.usefixtures("_operators")
def test_no_repeat_notification_while_still_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _FakeBot()
    _check(monkeypatch, bot, True)
    _check(monkeypatch, bot, False)
    assert len(bot.sent) == len(_OPERATORS)

    _check(monkeypatch, bot, False)  # False→False: без повторов
    assert len(bot.sent) == len(_OPERATORS)


@pytest.mark.usefixtures("_operators")
def test_no_notification_on_unknown_state(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _FakeBot()
    _check(monkeypatch, bot, True)
    _check(monkeypatch, bot, None)  # True→None: сервис недоступен — правды не знаем
    assert bot.sent == []

    _check(monkeypatch, bot, False)  # None→False: тоже молчим
    assert bot.sent == []


@pytest.mark.usefixtures("_operators")
def test_recovery_then_second_failure_notifies_again(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _FakeBot()
    _check(monkeypatch, bot, True)
    _check(monkeypatch, bot, False)
    _check(monkeypatch, bot, True)  # канал починили
    _check(monkeypatch, bot, False)  # и снова сломался → новое уведомление
    assert len(bot.sent) == 2 * len(_OPERATORS)


@pytest.mark.usefixtures("_operators")
def test_notify_failure_for_one_operator_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FlakyBot(_FakeBot):
        async def send_message(self, chat_id: int, text: str) -> None:
            if chat_id == min(_OPERATORS):
                raise RuntimeError("blocked by user")
            await super().send_message(chat_id, text)

    bot = _FlakyBot()
    _check(monkeypatch, bot, True)
    _check(monkeypatch, bot, False)
    # Второй оператор уведомление получил, несмотря на сбой у первого.
    assert [chat_id for chat_id, _ in bot.sent] == [max(_OPERATORS)]
