"""Поток запуска в боте: кабинет → цель → креатив (spec 2026-07-27 §9).

Порядок принципиален: если запускать некуда, оператор должен узнать об этом
до того, как выгрузит материалы, а не после.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import AdAccountItem, BriefCard, CoreUnavailable
from bot.handlers import creative
from bot.states import LaunchCampaign, UploadCreative

_OPERATOR_ID = 111


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
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.answers: list[str] = []
        self.answer_kwargs: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)
        self.answer_kwargs.append(kwargs)


class _FakeCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.message = _FakeMessage()
        self.alerts: list[str] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        if text:
            self.alerts.append(text)


@pytest.fixture(autouse=True)
def _fake_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creative, "Message", _FakeMessage)


def _card(**over: Any) -> BriefCard:
    base: dict[str, Any] = {
        "brief_id": 5,
        "variant": "individual",
        "status": "received",
        "client_name": "Иван Петров",
        "client_email": None,
        "client_phone": None,
        "client_telegram": None,
        "fields": [],
        "has_creative": False,
        "campaign_status": None,
        "client_id": None,
    }
    base.update(over)
    return BriefCard(**base)


@pytest.fixture(autouse=True)
def _stub_get_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    """Карточка брифа для сценариев, которым важен лишь выбор кабинета/цели —
    тесты, которым важен сам бриф (`client_id` для сужения списка), переопределяют
    `bot.api_client.get_brief` заново поверх этого стаба."""

    async def fake(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    monkeypatch.setattr("bot.api_client.get_brief", fake)


def _item(**over: Any) -> AdAccountItem:
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


def _stub_list(monkeypatch: pytest.MonkeyPatch, items: list[AdAccountItem]) -> None:
    async def fake(client_id: int | None = None) -> list[AdAccountItem]:
        return items

    monkeypatch.setattr("bot.api_client.list_ad_accounts", fake)


def test_no_cabinets_stops_before_asking_for_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ни одного кабинета — материалы не запрашиваем вовсе."""
    _stub_list(monkeypatch, [])
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    assert "запускать некуда" in callback.message.answers[0]
    assert "/cabinets" in callback.message.answers[0]
    assert state.state is None


def test_all_cabinets_dead_stops_early(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [_item(health="unauthorized", is_usable=False)])
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    assert "не годится" in callback.message.answers[0]
    assert state.state is None


def test_single_cabinet_is_preselected_and_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выбирать не из чего, но оператор обязан видеть, куда уедет кампания."""
    _stub_list(monkeypatch, [_item()])
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    assert state.state == LaunchCampaign.choosing_goal
    assert state.data["ad_account_id"] == 1
    assert "Студия «Пример»" in callback.message.answers[0]


def test_several_cabinets_offer_a_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [_item(id=1), _item(id=2, external_id="10000002", title="Второй")])
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    assert state.state == LaunchCampaign.choosing_cabinet
    keyboard = callback.message.answer_kwargs[0]["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "adacc:launch:5:1" in datas
    assert "adacc:launch:5:2" in datas


def test_dead_cabinets_are_not_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(
        monkeypatch,
        [_item(id=1), _item(id=2, external_id="10000002", health="unauthorized", is_usable=False)],
    )
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    # Живой остался один — сразу переходим к цели, мёртвый не предлагается.
    assert state.state == LaunchCampaign.choosing_goal
    assert state.data["ad_account_id"] == 1


def test_picking_cabinet_moves_to_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [_item(id=1), _item(id=2, external_id="10000002", title="Второй")])
    callback, state = _FakeCallback("adacc:launch:5:2"), _FakeState()
    asyncio.run(creative.picked_cabinet(callback, state))
    assert state.state == LaunchCampaign.choosing_goal
    assert state.data["ad_account_id"] == 2
    assert "Второй" in callback.message.answers[0]


def test_picked_cabinet_filters_by_brief_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """То же ревью 2.5, что и `brief_card.py::picked_cabinet_for_launch`: этот
    хендлер — точная копия того же паттерна (кабинет выбран из клавиатуры,
    список перезапрашивается) и страдал тем же дефектом — фильтр по клиенту
    брифа терялся. `start_creative` сохраняет `client_id` брифа в FSM
    (`test_start_creative_remembers_brief_client` ниже) именно затем, чтобы
    этот хендлер мог его использовать."""
    captured: dict[str, Any] = {}

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        captured["client_id"] = client_id
        return [_item(id=2, external_id="10000002", title="Второй")]

    monkeypatch.setattr("bot.api_client.list_ad_accounts", fake_list)
    callback, state = _FakeCallback("adacc:launch:5:2"), _FakeState()
    state.data = {"client_id": 42}
    asyncio.run(creative.picked_cabinet(callback, state))
    assert captured["client_id"] == 42


def test_start_creative_remembers_brief_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """`start_creative` уже запрашивает кабинеты для клиента брифа — теперь этот
    id ещё и остаётся в FSM, чтобы `picked_cabinet` мог им воспользоваться при
    повторном запросе списка (см. тест выше)."""

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id, client_id=42)

    monkeypatch.setattr("bot.api_client.get_brief", fake_get_brief)
    _stub_list(monkeypatch, [_item(id=1), _item(id=2, external_id="10000002", title="Второй")])
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    assert state.data["client_id"] == 42


def test_goal_keyboard_offers_all_four_implemented_goals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Все четыре цели реализованы — молчаливой подмены нет ни у одной из них.

    Реализованы «подписчики», «сообщения», «лид-форма» (см. `services.launch_service.
    SUPPORTED_GOALS`) и «заявка через Senler» (решение 2026-08-24, технически тот же
    пакет VK, что и у «Сообщений», tests/test_senler_goal.py) — ни одна кнопка
    больше не ведёт на «goal:soon».
    """
    _stub_list(monkeypatch, [_item()])
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    keyboard = callback.message.answer_kwargs[-1]["reply_markup"]
    buttons = [b for row in keyboard.inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == [
        "goal:subscribers:5",
        "goal:messages:5",
        "goal:lead_form:5",
        "goal:senler:5",
    ]
    assert not any("скоро" in b.text for b in buttons)


def test_unimplemented_goal_explains_itself() -> None:
    callback = _FakeCallback("goal:soon")
    asyncio.run(creative.goal_not_ready(callback))
    assert any("ещё не реализована" in alert for alert in callback.alerts)


def test_picking_goal_finally_asks_for_media() -> None:
    callback, state = _FakeCallback("goal:subscribers:5"), _FakeState()
    asyncio.run(creative.picked_goal(callback, state))
    assert state.state == UploadCreative.waiting_media
    assert state.data["goal"] == "subscribers"
    assert "фото или видео" in callback.message.answers[0]


def test_core_down_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(client_id: int | None = None) -> list[AdAccountItem]:
        raise CoreUnavailable("down")

    monkeypatch.setattr("bot.api_client.list_ad_accounts", broken)
    callback, state = _FakeCallback("creative:5"), _FakeState()
    asyncio.run(creative.start_creative(callback, state))
    assert "недоступен" in callback.message.answers[0]


def test_selection_reaches_the_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выбор оператора обязан доехать до ядра, иначе он бесполезен."""
    captured: dict[str, Any] = {}

    async def fake_upload(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(campaign_status="prepared", campaign_id=1, message="ok")

    class _Bot:
        async def download(self, file_id: str) -> Any:
            import io

            return io.BytesIO(b"\xff\xd8\xff\x00")

    monkeypatch.setattr("bot.api_client.upload_creative", fake_upload)
    callback, state = _FakeCallback("creative_send"), _FakeState()
    state.data = {
        "brief_id": 5,
        "file_id": "f",
        "media_type": "photo",
        "width": 1080,
        "height": 1080,
        "title": "T",
        "body": "B",
        "ad_account_id": 42,
        "goal": "subscribers",
    }
    asyncio.run(creative.send_creative(callback, state, _Bot()))
    assert captured["ad_account_id"] == 42
    assert captured["goal"] == "subscribers"
