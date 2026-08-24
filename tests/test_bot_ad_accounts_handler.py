"""Хендлер `/cabinets`: список, добавление, проверка, удаление (spec §10).

Отдельно закрепляем безопасность: сообщение с токеном удаляется из чата, а в
логи попадает только длина. Сам токен не должен встречаться ни в одном ответе.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from bot.api_client import (
    AdAccountItem,
    AdAccountNotFound,
    AdAccountRejected,
    ClientItem,
    CoreUnavailable,
)
from bot.handlers import ad_accounts
from bot.states import AddAdAccount

_OPERATOR_ID = 111
TOKEN = "fake-access-token-for-tests-0000000000000000"


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
        self.deleted = False

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)
        self.answer_kwargs.append(kwargs)

    async def delete(self) -> None:
        self.deleted = True


class _FakeCallback:
    def __init__(self, data: str, message: _FakeMessage | None = None) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=_OPERATOR_ID)
        self.message = message or _FakeMessage()
        self.alerts: list[str] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        if text:
            self.alerts.append(text)


@pytest.fixture(autouse=True)
def _fake_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Хендлеры проверяют `isinstance(..., Message)` — подменяем сам тип."""
    monkeypatch.setattr(ad_accounts, "Message", _FakeMessage)


def _item(**over: Any) -> AdAccountItem:
    base: dict[str, Any] = {
        "id": 1,
        "title": "Студия «Пример»",
        "external_id": "10000001",
        "username": "a1b2c3d4e5@agency_client",
        "token_tail": "0000",
        "advertiser_kind": "owner",
        "advertiser_name": None,
        "advertiser_inn": None,
        "status": "active",
        "health": "healthy",
        "health_checked_at": None,
        "health_error": None,
        "balance_rub": "12345.67",
        "is_usable": True,
    }
    base.update(over)
    return AdAccountItem(**base)


def _client(**over: Any) -> ClientItem:
    base: dict[str, Any] = {
        "id": 1,
        "full_name": "Клиент",
        "email": None,
        "phone": None,
        "telegram": None,
        "brief_count": 0,
    }
    base.update(over)
    return ClientItem(**base)


def _stub_list(monkeypatch: pytest.MonkeyPatch, items: list[AdAccountItem]) -> None:
    async def fake() -> list[AdAccountItem]:
        return items

    monkeypatch.setattr("bot.api_client.list_ad_accounts", fake)


def _stub_clients(monkeypatch: pytest.MonkeyPatch, items: list[ClientItem]) -> None:
    async def fake(operator_telegram_id: int) -> list[ClientItem]:
        return items

    monkeypatch.setattr("bot.api_client.list_clients", fake)


# --- список -------------------------------------------------------------------


def test_empty_list_explains_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [])
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    assert "Пока ни одного кабинета" in message.answers[0]
    assert "access_token" in message.answers[0]


def test_list_shows_health_and_masked_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    text = message.answers[0]
    assert "Студия «Пример»" in text
    assert "10000001" in text
    assert "…0000" in text
    assert "✅ жив" in text
    assert TOKEN not in text


def test_list_shows_general_cabinet_mark_when_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кабинет без привязки — общий, это должно быть видно, а не молчать (spec §1.4)."""
    _stub_list(monkeypatch, [_item(client_id=None, client_name=None)])
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    assert "доступен любому клиенту" in message.answers[0].lower()


def test_list_shows_bound_client_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [_item(client_id=7, client_name="Иванов Иван")])
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    text = message.answers[0]
    assert "закреплён" in text.lower()
    assert "Иванов Иван" in text


def test_list_shows_bound_client_without_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Client.full_name` необязательное — привязка есть, а имени может не быть."""
    _stub_list(monkeypatch, [_item(client_id=7, client_name=None)])
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    text = message.answers[0]
    assert "закреплён" in text.lower()
    assert "None" not in text


def test_list_shows_third_party_advertiser(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(
        monkeypatch,
        [
            _item(
                advertiser_kind="third_party",
                advertiser_name="ООО «Ромашка»",
                advertiser_inn="7701234567",
            )
        ],
    )
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    assert "реклама третьего лица: ООО «Ромашка», ИНН 7701234567" in message.answers[0]


def test_dead_cabinet_shows_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(
        monkeypatch,
        [_item(health="unauthorized", health_error="VK отклонил токен — выпустите новый")],
    )
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    assert "⛔ токен не принят" in message.answers[0]
    assert "выпустите новый" in message.answers[0]


def test_empty_list_shows_setup_instruction_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оператор пересылает клиенту ссылку на инструкцию по созданию кабинета вручную."""
    _stub_list(monkeypatch, [])
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    assert "vk-ads-auto.ru/instrukciya-vk-cabinet.html" in message.answers[0]


def test_list_with_items_shows_setup_instruction_link(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    assert "vk-ads-auto.ru/instrukciya-vk-cabinet.html" in message.answers[0]


def test_core_down_shows_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken() -> list[AdAccountItem]:
        raise CoreUnavailable("down")

    monkeypatch.setattr("bot.api_client.list_ad_accounts", broken)
    message, state = _FakeMessage(), _FakeState()
    asyncio.run(ad_accounts.cabinets_command(message, state))
    assert "недоступен" in message.answers[0]


# --- добавление ---------------------------------------------------------------


def test_add_flow_asks_kind_first() -> None:
    callback, state = _FakeCallback("adacc:add"), _FakeState()
    asyncio.run(ad_accounts.start_add(callback, state))
    assert state.state == AddAdAccount.choosing_kind
    assert "Чью рекламу" in callback.message.answers[0]


def test_owner_kind_skips_advertiser_question_and_asks_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Реклама владельца — сразу к выбору клиента, минуя вопрос о рекламодателе."""
    _stub_clients(monkeypatch, [])
    callback, state = _FakeCallback("adacckind:owner"), _FakeState()
    asyncio.run(ad_accounts.got_kind(callback, state))
    assert state.state == AddAdAccount.choosing_client
    assert "клиент" in callback.message.answers[0].lower()


def test_third_party_kind_asks_advertiser() -> None:
    callback, state = _FakeCallback("adacckind:third_party"), _FakeState()
    asyncio.run(ad_accounts.got_kind(callback, state))
    assert state.state == AddAdAccount.entering_advertiser


def test_advertiser_line_splits_name_and_inn(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_clients(monkeypatch, [])
    message, state = _FakeMessage("ООО «Ромашка», 7701234567"), _FakeState()
    asyncio.run(ad_accounts.got_advertiser(message, state))
    assert state.data["advertiser_name"] == "ООО «Ромашка»"
    assert state.data["advertiser_inn"] == "7701234567"
    assert state.state == AddAdAccount.choosing_client


def test_advertiser_line_without_inn_is_kept_as_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_clients(monkeypatch, [])
    message, state = _FakeMessage("ИП Иванов"), _FakeState()
    asyncio.run(ad_accounts.got_advertiser(message, state))
    assert state.data["advertiser_name"] == "ИП Иванов"
    assert state.data["advertiser_inn"] is None
    assert state.state == AddAdAccount.choosing_client


# --- выбор клиента (привязка при добавлении) -----------------------------------


def test_client_choice_offers_clients_and_general_option(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_clients(
        monkeypatch,
        [_client(id=7, full_name="Иван Петров", brief_count=2)],
    )
    callback, state = _FakeCallback("adacckind:owner"), _FakeState()
    asyncio.run(ad_accounts.got_kind(callback, state))
    keyboard = callback.message.answer_kwargs[0]["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "addclient:none" in datas
    assert "addclient:7" in datas


def test_client_choice_unavailable_core_bails_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(operator_telegram_id: int) -> list[ClientItem]:
        raise CoreUnavailable("down")

    monkeypatch.setattr("bot.api_client.list_clients", broken)
    callback, state = _FakeCallback("adacckind:owner"), _FakeState()
    asyncio.run(ad_accounts.got_kind(callback, state))
    assert "недоступен" in callback.message.answers[0]
    assert state.state is None


def test_picking_general_moves_to_token_with_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оператор может оставить кабинет общим — доступным любому клиенту."""
    callback, state = _FakeCallback("addclient:none"), _FakeState()
    state.state = AddAdAccount.choosing_client
    asyncio.run(ad_accounts.got_client(callback, state))
    assert state.state == AddAdAccount.entering_token
    assert state.data["client_id"] is None
    assert "access_token" in callback.message.answers[0]


def test_picking_a_client_stores_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оператор может закрепить кабинет за конкретным клиентом."""
    callback, state = _FakeCallback("addclient:7"), _FakeState()
    state.state = AddAdAccount.choosing_client
    asyncio.run(ad_accounts.got_client(callback, state))
    assert state.state == AddAdAccount.entering_token
    assert state.data["client_id"] == 7


def test_client_choice_pagination_shows_next_page(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_client(id=i, full_name=f"Клиент {i}") for i in range(1, 10)]
    _stub_clients(monkeypatch, items)
    callback, state = _FakeCallback("addclient:pg:1"), _FakeState()
    state.state = AddAdAccount.choosing_client
    asyncio.run(ad_accounts.got_client(callback, state))
    assert state.state == AddAdAccount.choosing_client  # остаёмся на этом же шаге
    keyboard = callback.message.answer_kwargs[-1]["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "addclient:9" in datas


def test_token_message_is_deleted_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Главное свойство безопасности: токен не остаётся в переписке."""

    async def fake_add(token: str, **kwargs: Any) -> AdAccountItem:
        return _item()

    monkeypatch.setattr("bot.api_client.add_ad_account", fake_add)
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(TOKEN), _FakeState()
    asyncio.run(ad_accounts.got_token(message, state))
    assert message.deleted is True


def test_token_is_never_echoed_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_add(token: str, **kwargs: Any) -> AdAccountItem:
        return _item()

    monkeypatch.setattr("bot.api_client.add_ad_account", fake_add)
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(TOKEN), _FakeState()
    asyncio.run(ad_accounts.got_token(message, state))
    assert all(TOKEN not in answer for answer in message.answers)


def test_token_is_redacted_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fake_add(token: str, **kwargs: Any) -> AdAccountItem:
        return _item()

    monkeypatch.setattr("bot.api_client.add_ad_account", fake_add)
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(TOKEN), _FakeState()
    with caplog.at_level(logging.INFO):
        asyncio.run(ad_accounts.got_token(message, state))
    assert TOKEN not in caplog.text
    assert "redacted" in caplog.text


def test_token_passes_advertiser_details(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_add(token: str, **kwargs: Any) -> AdAccountItem:
        captured.update(kwargs)
        return _item()

    monkeypatch.setattr("bot.api_client.add_ad_account", fake_add)
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(TOKEN), _FakeState()
    state.data = {
        "advertiser_kind": "third_party",
        "advertiser_name": "ООО «Ромашка»",
        "advertiser_inn": "7701234567",
    }
    asyncio.run(ad_accounts.got_token(message, state))
    assert captured["advertiser_kind"] == "third_party"
    assert captured["advertiser_name"] == "ООО «Ромашка»"


def test_rejected_token_shows_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_add(token: str, **kwargs: Any) -> AdAccountItem:
        raise AdAccountRejected("VK не принял этот токен.")

    monkeypatch.setattr("bot.api_client.add_ad_account", fake_add)
    message, state = _FakeMessage(TOKEN), _FakeState()
    asyncio.run(ad_accounts.got_token(message, state))
    assert any("VK не принял" in answer for answer in message.answers)
    assert message.deleted is True


def test_empty_token_message_asks_again(monkeypatch: pytest.MonkeyPatch) -> None:
    message, state = _FakeMessage("   "), _FakeState()
    asyncio.run(ad_accounts.got_token(message, state))
    assert any("Пустое сообщение" in answer for answer in message.answers)


def test_token_submission_binds_the_chosen_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_add(token: str, **kwargs: Any) -> AdAccountItem:
        captured.update(kwargs)
        return _item(client_id=7, client_name="Иван Петров")

    monkeypatch.setattr("bot.api_client.add_ad_account", fake_add)
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(TOKEN), _FakeState()
    state.data = {"client_id": 7}
    asyncio.run(ad_accounts.got_token(message, state))
    assert captured["client_id"] == 7
    assert any("Иван Петров" in answer for answer in message.answers)


def test_token_submission_without_client_choice_stays_general(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_add(token: str, **kwargs: Any) -> AdAccountItem:
        captured.update(kwargs)
        return _item(client_id=None, client_name=None)

    monkeypatch.setattr("bot.api_client.add_ad_account", fake_add)
    _stub_list(monkeypatch, [_item()])
    message, state = _FakeMessage(TOKEN), _FakeState()
    state.data = {"client_id": None}
    asyncio.run(ad_accounts.got_token(message, state))
    assert captured["client_id"] is None


# --- перепривязка существующего кабинета ---------------------------------------


def test_rebind_start_lists_cabinets_to_pick_from(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_list(monkeypatch, [_item(id=3, title="Кабинет А")])
    callback = _FakeCallback("adacc:client")
    asyncio.run(ad_accounts.start_rebind(callback))
    keyboard = callback.message.answer_kwargs[0]["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "adacc:clientpick:3" in datas


def test_rebind_pick_cabinet_then_asks_for_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_clients(
        monkeypatch,
        [_client(id=9, full_name="Мария", brief_count=1)],
    )
    callback = _FakeCallback("adacc:clientpick:3")
    asyncio.run(ad_accounts.pick_client_for_rebind(callback))
    keyboard = callback.message.answer_kwargs[0]["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "adaccbind:3:none" in datas
    assert "adaccbind:3:9" in datas


def test_rebind_to_a_client_reaches_the_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оператор может закрепить уже заведённый кабинет за клиентом."""
    captured: dict[str, Any] = {}

    async def fake_set(account_id: int, client_id: int | None) -> AdAccountItem:
        captured["account_id"] = account_id
        captured["client_id"] = client_id
        return _item(client_id=9, client_name="Мария")

    monkeypatch.setattr("bot.api_client.set_ad_account_client", fake_set)
    _stub_list(monkeypatch, [_item()])
    callback = _FakeCallback("adaccbind:3:9")
    asyncio.run(ad_accounts.rebind_client(callback))
    assert captured == {"account_id": 3, "client_id": 9}
    assert any("Мария" in answer for answer in callback.message.answers)


def test_rebind_to_general_reaches_the_core_with_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оператор может снять привязку — снова сделать кабинет общим."""
    captured: dict[str, Any] = {}

    async def fake_set(account_id: int, client_id: int | None) -> AdAccountItem:
        captured["client_id"] = client_id
        return _item(client_id=None, client_name=None)

    monkeypatch.setattr("bot.api_client.set_ad_account_client", fake_set)
    _stub_list(monkeypatch, [_item()])
    callback = _FakeCallback("adaccbind:3:none")
    asyncio.run(ad_accounts.rebind_client(callback))
    assert captured["client_id"] is None


def test_rebind_pagination_shows_next_page(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_client(id=i, full_name=f"Клиент {i}") for i in range(1, 10)]
    _stub_clients(monkeypatch, items)
    callback = _FakeCallback("adaccbind:3:pg:1")
    asyncio.run(ad_accounts.rebind_client(callback))
    keyboard = callback.message.answer_kwargs[-1]["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "adaccbind:3:9" in datas


def test_rebind_missing_cabinet_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_set(account_id: int, client_id: int | None) -> AdAccountItem:
        raise AdAccountNotFound(str(account_id))

    monkeypatch.setattr("bot.api_client.set_ad_account_client", fake_set)
    callback = _FakeCallback("adaccbind:3:none")
    asyncio.run(ad_accounts.rebind_client(callback))
    assert any("удалён" in alert for alert in callback.alerts)


# --- проверка и удаление ------------------------------------------------------


def test_check_one_shows_fresh_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check(account_id: int) -> AdAccountItem:
        return _item(health="unauthorized", health_error="токен отозван")

    monkeypatch.setattr("bot.api_client.check_ad_account", fake_check)
    callback = _FakeCallback("adacc:checkone:1")
    asyncio.run(ad_accounts.check_one(callback))
    assert "⛔ токен не принят" in callback.message.answers[0]


def test_check_of_missing_cabinet_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check(account_id: int) -> AdAccountItem:
        raise AdAccountNotFound("1")

    monkeypatch.setattr("bot.api_client.check_ad_account", fake_check)
    callback = _FakeCallback("adacc:checkone:1")
    asyncio.run(ad_accounts.check_one(callback))
    assert any("удалён" in alert for alert in callback.alerts)


def test_delete_asks_for_confirmation() -> None:
    """Удаление стирает токен безвозвратно — подтверждение обязательно."""
    callback = _FakeCallback("adacc:delpick:1")
    asyncio.run(ad_accounts.confirm_delete(callback))
    assert "безвозвратно" in callback.message.answers[0]
    assert callback.message.answer_kwargs[0].get("reply_markup") is not None


def test_delete_confirmed_calls_core(monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[int] = []

    async def fake_delete(account_id: int) -> None:
        deleted.append(account_id)

    monkeypatch.setattr("bot.api_client.delete_ad_account", fake_delete)
    _stub_list(monkeypatch, [])
    callback = _FakeCallback("adacc:delok:7")
    asyncio.run(ad_accounts.do_delete(callback))
    assert deleted == [7]
    assert any("удалён" in answer for answer in callback.message.answers)


def test_cancel_clears_state() -> None:
    callback, state = _FakeCallback("adacc:cancel"), _FakeState()
    state.state = AddAdAccount.entering_token
    asyncio.run(ad_accounts.cancel(callback, state))
    assert state.state is None
