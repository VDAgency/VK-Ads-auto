"""Тесты фонового поллера сессий юзербота (`bot/userbot_watch.py`, spec §9)."""

from __future__ import annotations

import asyncio

import pytest
from bot import userbot_watch
from bot.api_client import UserbotUnavailable


def teardown_function() -> None:
    userbot_watch.reset()


def test_unknown_before_first_poll() -> None:
    assert userbot_watch.is_authorized(111) is None


def test_refresh_populates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_all() -> dict[int, bool]:
        return {111: True, 222: False}

    monkeypatch.setattr("bot.api_client.userbot_health_all", fake_all)
    asyncio.run(userbot_watch.refresh_once())

    assert userbot_watch.is_authorized(111) is True
    assert userbot_watch.is_authorized(222) is False
    # Оператор без сессии при известном состоянии — не авторизован (баннер нужен).
    assert userbot_watch.is_authorized(333) is False


def test_unavailable_resets_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_all() -> dict[int, bool]:
        return {111: True}

    monkeypatch.setattr("bot.api_client.userbot_health_all", fake_all)
    asyncio.run(userbot_watch.refresh_once())
    assert userbot_watch.is_authorized(111) is True

    async def boom() -> dict[int, bool]:
        raise UserbotUnavailable("down")

    monkeypatch.setattr("bot.api_client.userbot_health_all", boom)
    asyncio.run(userbot_watch.refresh_once())
    # Кеш не «врёт»: состояние неизвестно, а не «всё ещё авторизован».
    assert userbot_watch.is_authorized(111) is None
