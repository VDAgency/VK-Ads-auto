"""Тесты хендлера загрузки креатива: FSM медиа → описание → отправка (триггер РК)."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import CreativeRejected, CreativeResult
from bot.handlers import creative
from bot.states import UploadCreative


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
    def __init__(self, text: str = "", photo: Any = None, video: Any = None) -> None:
        self.text = text
        self.photo = photo
        self.video = video
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


class _FakeBot:
    """Bot-заглушка: download возвращает поток байтов по file_id."""

    def __init__(self, payload: bytes = b"\xff\xd8\xff\x00") -> None:
        self.payload = payload
        self.downloaded: list[str] = []

    async def download(self, file_id: str) -> io.BytesIO:
        self.downloaded.append(file_id)
        return io.BytesIO(self.payload)


def _photo(size: int = 1000) -> Any:
    return SimpleNamespace(file_id="fid", width=800, height=800, file_size=size)


def test_start_creative_sets_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creative, "Message", _FakeMessage)
    callback = _FakeCallback("creative:7")
    state = _FakeState()
    asyncio.run(creative.start_creative(callback, state))

    assert state.state == UploadCreative.waiting_media
    assert state.data["brief_id"] == 7
    assert any("фото" in text.lower() for text, _ in callback.message.answers)


def test_got_media_photo_advances_to_description() -> None:
    state = _FakeState()
    state.state = UploadCreative.waiting_media
    state.data = {"brief_id": 7}
    message = _FakeMessage(photo=[_photo()])
    asyncio.run(creative.got_media(message, state))

    assert state.state == UploadCreative.waiting_description
    assert state.data["file_id"] == "fid"
    assert state.data["media_type"] == "photo"
    assert state.data["width"] == 800


def test_got_media_too_big_stays() -> None:
    state = _FakeState()
    state.state = UploadCreative.waiting_media
    state.data = {"brief_id": 7}
    message = _FakeMessage(photo=[_photo(size=21 * 1024 * 1024)])  # >20 МБ
    asyncio.run(creative.got_media(message, state))

    assert state.state == UploadCreative.waiting_media  # не продвинулись
    assert any("20 МБ" in text for text, _ in message.answers)


def test_got_media_non_media_asks_again() -> None:
    state = _FakeState()
    state.state = UploadCreative.waiting_media
    message = _FakeMessage(text="просто текст")
    asyncio.run(creative.got_media(message, state))

    assert state.state == UploadCreative.waiting_media
    assert message.answers


def test_got_description_shows_confirm() -> None:
    state = _FakeState()
    state.state = UploadCreative.waiting_description
    state.data = {
        "brief_id": 7,
        "file_id": "fid",
        "media_type": "photo",
        "width": 800,
        "height": 800,
    }
    message = _FakeMessage(text="Заголовок\nТекст объявления")
    asyncio.run(creative.got_description(message, state))

    assert state.data["title"] == "Заголовок"
    assert state.data["body"] == "Текст объявления"
    text, markup = message.answers[-1]
    assert markup is not None  # клавиатура подтверждения


def test_send_creative_uploads_and_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_upload(
        brief_id: int,
        media_b64: str,
        media_type: str,
        width: int,
        height: int,
        title: str,
        body: str,
    ) -> CreativeResult:
        captured.update(brief_id=brief_id, media_type=media_type, title=title)
        return CreativeResult(campaign_status="prepared", campaign_id=5, message="🚀 подготовлена")

    monkeypatch.setattr("bot.api_client.upload_creative", fake_upload)
    monkeypatch.setattr(creative, "Message", _FakeMessage)
    state = _FakeState()
    state.state = UploadCreative.waiting_description
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
    bot = _FakeBot()
    asyncio.run(creative.send_creative(callback, state, bot))

    assert bot.downloaded == ["fid"]
    assert captured["brief_id"] == 7
    assert state.state is None
    text, markup = callback.message.answers[-1]
    assert "подготовлена" in text
    assert markup is not None  # карточка с кнопками


def test_send_creative_rejected_shows_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_upload(*args: Any, **kwargs: Any) -> CreativeResult:
        raise CreativeRejected("Минимальный размер 600×600.")

    monkeypatch.setattr("bot.api_client.upload_creative", fake_upload)
    monkeypatch.setattr(creative, "Message", _FakeMessage)
    state = _FakeState()
    state.state = UploadCreative.waiting_description
    state.data = {
        "brief_id": 7,
        "file_id": "fid",
        "media_type": "photo",
        "width": 100,
        "height": 100,
        "title": "",
        "body": "",
    }
    callback = _FakeCallback("creative_send")
    asyncio.run(creative.send_creative(callback, state, _FakeBot()))

    assert state.state is None
    assert any("600" in text for text, _ in callback.message.answers)


def test_cancel_creative_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creative, "Message", _FakeMessage)
    state = _FakeState()
    state.state = UploadCreative.waiting_media
    callback = _FakeCallback("creative_cancel")
    asyncio.run(creative.cancel_creative(callback, state))

    assert state.state is None
    assert any("Отменено" in text for text, _ in callback.message.answers)
