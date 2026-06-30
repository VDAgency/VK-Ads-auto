import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Dispatcher
from bot.access import OperatorOnly
from bot.main import build_dispatcher
from config.settings import Settings


def test_build_dispatcher() -> None:
    assert isinstance(build_dispatcher(), Dispatcher)


def _check_operator(user_id: int | None, monkeypatch: pytest.MonkeyPatch) -> bool:
    settings = Settings(_env_file=None, operator_telegram_ids=frozenset({42}))
    monkeypatch.setattr("bot.access.get_settings", lambda: settings)
    from_user = SimpleNamespace(id=user_id) if user_id is not None else None
    event: Any = SimpleNamespace(from_user=from_user)
    return asyncio.run(OperatorOnly()(event))


def test_operator_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _check_operator(42, monkeypatch) is True


def test_non_operator_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _check_operator(99, monkeypatch) is False


def test_missing_user_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _check_operator(None, monkeypatch) is False
