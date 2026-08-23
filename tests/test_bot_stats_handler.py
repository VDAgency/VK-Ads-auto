"""Тесты хендлера `/stats` (PR-C + задача 2): список, вход в кабинет, период.

Задача 2: вход в кабинет синкает метрики перед показом (дефект 1, честная пометка
при сбое синка — экран всё равно показывается по данным из базы), CPL печатается
рядом с CPC/CTR (дефект 3).
"""

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
        spent=250,
        results=10,
        ctr=5.0,
        cpc=5.0,
        cpl=25.0,
        is_mock=is_mock,
    )


def _stub_sync(monkeypatch: pytest.MonkeyPatch, *, ok: bool = True) -> list[str]:
    """Подменить синк кабинета перед показом; вернуть список id, которыми его вызвали."""
    calls: list[str] = []

    async def fake(cabinet_id: str) -> bool:
        calls.append(cabinet_id)
        return ok

    monkeypatch.setattr("bot.api_client.sync_cabinet_stats", fake)
    return calls


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
    _stub_sync(monkeypatch)

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
    _stub_sync(monkeypatch)

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


# --- дефект 3: CPL рядом с CPC/CTR --------------------------------------------------


def test_open_cabinet_shows_cpl(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sync(monkeypatch)

    async def fake(cabinet_id: str, period: str) -> CabinetStats:
        return _stats(cabinet_id, is_mock=False, period=period)

    monkeypatch.setattr("bot.api_client.get_cabinet_stats", fake)
    monkeypatch.setattr(stats, "Message", _FakeMessage)
    callback = _FakeCallback("cabinet:camp-1")
    asyncio.run(stats.open_cabinet(callback))

    text, _ = callback.message.answers[0]
    assert "CPL: 25.0" in text


# --- дефект 1: вход в кабинет обновляет метрики перед показом -----------------------


def test_open_cabinet_syncs_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_sync(monkeypatch)

    async def fake(cabinet_id: str, period: str) -> CabinetStats:
        # На момент чтения синк того же кабинета уже должен был случиться.
        assert calls == [cabinet_id]
        return _stats(cabinet_id, is_mock=False, period=period)

    monkeypatch.setattr("bot.api_client.get_cabinet_stats", fake)
    monkeypatch.setattr(stats, "Message", _FakeMessage)
    callback = _FakeCallback("cabinet:camp-1")
    asyncio.run(stats.open_cabinet(callback))

    assert calls == ["camp-1"]


def test_switch_period_also_syncs_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_sync(monkeypatch)

    async def fake(cabinet_id: str, period: str) -> CabinetStats:
        return _stats(cabinet_id, is_mock=False, period=period)

    monkeypatch.setattr("bot.api_client.get_cabinet_stats", fake)
    monkeypatch.setattr(stats, "Message", _FakeMessage)
    callback = _FakeCallback("stats:camp-1:week")
    asyncio.run(stats.switch_period(callback))

    assert calls == ["camp-1"]


def test_sync_failure_still_renders_saved_data_with_honest_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Синк не удался — экран всё равно показывается по данным из БД, с пометкой."""
    _stub_sync(monkeypatch, ok=False)

    async def fake(cabinet_id: str, period: str) -> CabinetStats:
        return _stats(cabinet_id, is_mock=False, period=period)

    monkeypatch.setattr("bot.api_client.get_cabinet_stats", fake)
    monkeypatch.setattr(stats, "Message", _FakeMessage)
    callback = _FakeCallback("cabinet:camp-1")
    asyncio.run(stats.open_cabinet(callback))

    text, _ = callback.message.answers[0]
    assert "Показы: 1000" in text  # данные из БД всё равно показаны
    assert "не удалось обновить" in text.lower()


def test_sync_success_has_no_honest_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_sync(monkeypatch, ok=True)

    async def fake(cabinet_id: str, period: str) -> CabinetStats:
        return _stats(cabinet_id, is_mock=False, period=period)

    monkeypatch.setattr("bot.api_client.get_cabinet_stats", fake)
    monkeypatch.setattr(stats, "Message", _FakeMessage)
    callback = _FakeCallback("cabinet:camp-1")
    asyncio.run(stats.open_cabinet(callback))

    text, _ = callback.message.answers[0]
    assert "не удалось обновить" not in text.lower()
