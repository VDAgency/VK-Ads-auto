"""Тесты хендлера `/stats` (PR-C): список, вход в кабинет, переключение периода."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bot.api_client import CabinetItem, CabinetStats, CoreUnavailable
from bot.handlers import stats


class _FakeMessage:
    def __init__(self) -> None:
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


def _cabinet(cid: str, is_mock: bool) -> CabinetItem:
    return CabinetItem(
        id=cid, name=f"Кабинет {cid}", status="active", launched_at="2026-08-01", is_mock=is_mock
    )


def _stats(cid: str, is_mock: bool, period: str = "all") -> CabinetStats:
    return CabinetStats(
        cabinet_id=cid,
        period=period,
        shows=1000,
        clicks=50,
        spent=100,
        results=6,
        ctr=5.0,
        cpc=2.0,
        is_mock=is_mock,
    )


def test_list_shows_mock_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake() -> list[CabinetItem]:
        return [_cabinet("demo-1", True)]

    monkeypatch.setattr("bot.api_client.get_cabinets", fake)
    message = _FakeMessage()
    asyncio.run(stats.show_stats(message))

    text, markup = message.answers[0]
    assert "Демо-данные" in text
    assert markup is not None  # клавиатура с кабинетами


def test_list_no_banner_for_real(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake() -> list[CabinetItem]:
        return [_cabinet("camp-1", False)]

    monkeypatch.setattr("bot.api_client.get_cabinets", fake)
    message = _FakeMessage()
    asyncio.run(stats.show_stats(message))

    assert "Демо-данные" not in message.answers[0][0]


def test_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake() -> list[CabinetItem]:
        return []

    monkeypatch.setattr("bot.api_client.get_cabinets", fake)
    message = _FakeMessage()
    asyncio.run(stats.show_stats(message))

    text, markup = message.answers[0]
    assert "Кабинетов пока нет" in text
    assert markup is None


def test_open_cabinet_renders_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def fake(cabinet_id: str, period: str) -> CabinetStats:
        captured["id"] = cabinet_id
        captured["period"] = period
        return _stats(cabinet_id, is_mock=True, period=period)

    monkeypatch.setattr("bot.api_client.get_cabinet_stats", fake)
    monkeypatch.setattr(stats, "Message", _FakeMessage)  # пройти isinstance-гард
    callback = _FakeCallback("cabinet:demo-1")
    asyncio.run(stats.open_cabinet(callback))

    assert captured == {"id": "demo-1", "period": "all"}
    text, markup = callback.message.answers[0]
    assert "CTR: 5.0%" in text
    assert markup is not None
    assert callback.answered


def test_switch_period_uses_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def fake(cabinet_id: str, period: str) -> CabinetStats:
        captured["period"] = period
        return _stats(cabinet_id, is_mock=False, period=period)

    monkeypatch.setattr("bot.api_client.get_cabinet_stats", fake)
    monkeypatch.setattr(stats, "Message", _FakeMessage)  # пройти isinstance-гард
    callback = _FakeCallback("stats:demo-1:week")
    asyncio.run(stats.switch_period(callback))

    assert captured["period"] == "week"


def test_core_unavailable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> list[CabinetItem]:
        raise CoreUnavailable("down")

    monkeypatch.setattr("bot.api_client.get_cabinets", boom)
    message = _FakeMessage()
    asyncio.run(stats.show_stats(message))

    assert "временно недоступен" in message.answers[0][0]
