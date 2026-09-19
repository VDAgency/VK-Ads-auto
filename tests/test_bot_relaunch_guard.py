"""Тесты повторного запуска по брифу в боте (задача 6, spec §F).

409 `campaign_already_exists` от ядра должен показать оператору статус
существующей кампании и кнопку «Запустить ещё одну», а не просто отказ; повтор
идёт с `allow_relaunch=True`. Сценарий с креативом (`bot/handlers/creative.py`)
и без него (`bot/handlers/brief_card.py`) проверяются отдельно — у них разный
источник данных для повтора (FSM vs `callback_data`).
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from bot.api_client import AdAccountItem, BriefCard, CampaignAlreadyExists, CreativeResult
from bot.handlers import brief_card, creative


class _FakeState:
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
    def __init__(self) -> None:
        self.answers: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None, **kwargs: Any) -> None:
        self.answers.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = _FakeMessage()

    async def answer(self, *args: Any, **kwargs: Any) -> None:
        pass


class _FakeBot:
    async def download(self, file_id: str) -> io.BytesIO:
        return io.BytesIO(b"\xff\xd8\xff\x00")


def _card(**over: Any) -> BriefCard:
    base: dict[str, Any] = {
        "brief_id": 7,
        "variant": "individual",
        "status": "received",
        "client_name": "Иван Петров",
        "client_email": None,
        "client_phone": None,
        "client_telegram": None,
        "fields": [],
        "has_creative": True,
        "campaign_status": "prepared",
        "client_id": 42,
    }
    base.update(over)
    return BriefCard(**base)


def _stub_get_brief(monkeypatch: pytest.MonkeyPatch, card: BriefCard | None = None) -> None:
    async def fake(brief_id: int) -> BriefCard:
        return card or _card(brief_id=brief_id)

    monkeypatch.setattr("bot.api_client.get_brief", fake)


def _account_item(**over: Any) -> AdAccountItem:
    base: dict[str, Any] = {
        "id": 1,
        "title": "Студия «Пример»",
        "external_id": "10000001",
        "username": None,
        "token_tail": "0000",
        "advertiser_kind": "owner",
        "advertiser_name": None,
        "advertiser_inn": None,
        "status": "active",
        "health": "healthy",
        "health_checked_at": None,
        "health_error": None,
        "balance_rub": None,
        "is_usable": True,
    }
    base.update(over)
    return AdAccountItem(**base)


# --- сценарий с креативом (bot/handlers/creative.py) --------------------------


def test_send_creative_offers_relaunch_on_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """409 `campaign_already_exists` — показать статус кампании и не сбрасывать FSM."""
    monkeypatch.setattr(creative, "Message", _FakeMessage)
    _stub_get_brief(monkeypatch, _card(campaign_status="launched"))

    async def fake_upload(*args: Any, **kwargs: Any) -> CreativeResult:
        raise CampaignAlreadyExists

    monkeypatch.setattr("bot.api_client.upload_creative", fake_upload)
    state = _FakeState()
    state.data = {
        "brief_id": 7,
        "file_id": "fid",
        "media_type": "photo",
        "width": 800,
        "height": 800,
        "title": "T",
        "body": "B",
    }
    callback = _FakeCallback("creative_send")
    asyncio.run(creative.send_creative(callback, state, _FakeBot()))

    # Состояние НЕ сброшено — медиа/описание нужны, чтобы повторить запуск.
    assert state.data.get("file_id") == "fid"
    text, markup = callback.message.answers[-1]
    assert "уже есть кампания" in text
    assert "запущена" in text
    assert markup is not None
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "creative_relaunch:7" in datas


def test_relaunch_creative_sends_allow_relaunch_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Запустить ещё одну» повторяет тот же креатив с `allow_relaunch=True`."""
    monkeypatch.setattr(creative, "Message", _FakeMessage)
    captured: dict[str, Any] = {}

    async def fake_upload(*args: Any, **kwargs: Any) -> CreativeResult:
        captured.update(kwargs)
        return CreativeResult(campaign_status="launched", campaign_id=9, message="🚀 запущена")

    monkeypatch.setattr("bot.api_client.upload_creative", fake_upload)
    state = _FakeState()
    state.data = {
        "brief_id": 7,
        "file_id": "fid",
        "media_type": "photo",
        "width": 800,
        "height": 800,
        "title": "T",
        "body": "B",
    }
    callback = _FakeCallback("creative_relaunch:7")
    asyncio.run(creative.relaunch_creative(callback, state, _FakeBot()))

    assert captured["allow_relaunch"] is True
    assert state.state is None  # успех — состояние сброшено
    text, _ = callback.message.answers[-1]
    assert "запущена" in text


def test_send_creative_still_reports_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Прочие отказы (`CreativeRejected`) идут прежним путём — не подменены 409-веткой."""
    from bot.api_client import CreativeRejected

    monkeypatch.setattr(creative, "Message", _FakeMessage)

    async def fake_upload(*args: Any, **kwargs: Any) -> CreativeResult:
        raise CreativeRejected("что-то не так")

    monkeypatch.setattr("bot.api_client.upload_creative", fake_upload)
    state = _FakeState()
    state.data = {
        "brief_id": 7,
        "file_id": "fid",
        "media_type": "photo",
        "width": 800,
        "height": 800,
        "title": "T",
        "body": "B",
    }
    callback = _FakeCallback("creative_send")
    asyncio.run(creative.send_creative(callback, state, _FakeBot()))

    assert state.state is None  # сброшено, как и раньше для этого отказа
    text, _ = callback.message.answers[-1]
    assert "что-то не так" in text


# --- сценарий без креатива (bot/handlers/brief_card.py) ------------------------


def test_launch_without_creative_offers_relaunch_on_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)
    _stub_get_brief(monkeypatch, _card(campaign_status="moderation"))

    async def fake_launch(*args: Any, **kwargs: Any) -> CreativeResult:
        raise CampaignAlreadyExists

    monkeypatch.setattr("bot.api_client.launch_brief", fake_launch)
    message = _FakeMessage()
    asyncio.run(brief_card._launch_and_report(message, 7, 3))  # type: ignore[arg-type]

    text, markup = message.answers[-1]
    assert "уже есть кампания" in text
    assert "на модерации" in text
    assert markup is not None
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "nocre_relaunch:7:3" in datas


def test_relaunch_without_creative_sends_allow_relaunch_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)
    captured: dict[str, Any] = {}

    async def fake_launch(*args: Any, **kwargs: Any) -> CreativeResult:
        captured.update(kwargs)
        return CreativeResult(campaign_status="launched", campaign_id=10, message="🚀 запущена")

    monkeypatch.setattr("bot.api_client.launch_brief", fake_launch)
    callback = _FakeCallback("nocre_relaunch:7:3")
    asyncio.run(brief_card.relaunch_without_creative(callback))

    assert captured["allow_relaunch"] is True
    text, _ = callback.message.answers[-1]
    assert "запущена" in text
