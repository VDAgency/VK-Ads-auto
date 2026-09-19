"""Карточка подтверждения запуска (Т3, spec 2026-08-25-cabinet-client-binding-design §2).

Три вещи проверяются здесь:
  - карточка содержит всё, чем можно ошибиться (клиент, объект, цель, бюджет, кабинет);
  - в сценарии «без креатива» запуск не происходит до явного подтверждения оператором
    (сам факт уже проверен в tests/test_bot_brief_card_handler.py на уровне хендлеров —
    здесь ещё раз, сквозным сценарием через оба шага: показ карточки → подтверждение);
  - отказы ядра 409 (`ad_account_client_mismatch`, `advertiser_mismatch`) доезжают до
    оператора человеческим текстом, а не кодом (`bot/api_client.py`).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from bot import api_client
from bot.api_client import (
    AdAccountItem,
    BriefCard,
    BriefFieldItem,
    CabinetChoiceRequired,
    CreativeRejected,
)
from bot.handlers import brief_card, creative

_CORE = "http://api:8000"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.get_settings", lambda: SimpleNamespace(core_base_url=_CORE))


def _account(**over: Any) -> AdAccountItem:
    base: dict[str, Any] = {
        "id": 3,
        "title": "Кабинет Ромашки",
        "external_id": "10000003",
        "username": None,
        "token_tail": "abcd",
        "advertiser_kind": "owner",
        "advertiser_name": None,
        "advertiser_inn": None,
        "status": "active",
        "health": "healthy",
        "health_checked_at": None,
        "health_error": None,
        "balance_rub": None,
        "is_usable": True,
        "client_id": None,
        "client_name": None,
    }
    base.update(over)
    return AdAccountItem(**base)


def _card(**over: Any) -> BriefCard:
    base: dict[str, Any] = {
        "brief_id": 9,
        "variant": "individual",
        "status": "received",
        "client_name": "Иван Петров",
        "client_email": None,
        "client_phone": None,
        "client_telegram": None,
        "fields": [
            BriefFieldItem(n=5, label="ИНН", value="770123456789"),
            BriefFieldItem(n=6, label="Ссылка на страницу VK", value="vk.com/ivan"),
            BriefFieldItem(n=13, label="Бюджет", value="10 000 ₽"),
            BriefFieldItem(n=14, label="Срок / период", value="месяц"),
        ],
        "has_creative": False,
        "campaign_status": None,
        "client_id": 42,
    }
    base.update(over)
    return BriefCard(**base)


# --- Содержимое карточки: общее для обоих сценариев ------------------------------


def test_card_contains_client_object_goal_budget_and_cabinet() -> None:
    """Карточка держит всё, чем можно ошибиться: имя клиента, ИНН, объект, цель,
    бюджет, срок, название и id кабинета."""
    card = _card()
    account = _account(title="Кабинет Ромашки", external_id="10000003")

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "Иван Петров" in text
    assert "770123456789" in text
    assert "vk.com/ivan" in text
    assert "Подписчики" in text
    assert "10 000 ₽" in text
    assert "месяц" in text
    assert "Кабинет Ромашки" in text
    assert "10000003" in text


def test_card_missing_fields_show_a_placeholder_not_a_crash() -> None:
    """Пустой бриф (никаких полей ещё не пришло) — карточка не падает, честно
    показывает «не указан», а не пустоту или ошибку."""
    card = _card(client_name=None, fields=[])
    account = _account()

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "не указан" in text


def test_shared_cabinet_is_marked_as_common() -> None:
    """Кабинет без привязки — «общий», тот же корень формулировки, что в /cabinets
    (ревью 2.4: раньше здесь звучало «не закреплён», а в /cabinets — «общий»,
    хотя комментарий в коде утверждал, что формулировка та же)."""
    card = _card()
    account = _account(client_id=None, client_name=None)

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "Кабинет общий — доступен любому клиенту." in text
    assert "Проверьте, что запускаете с нужного счёта." in text


def test_bound_cabinet_names_the_client() -> None:
    """Кабинет, закреплённый за этим клиентом, называет клиента по имени."""
    card = _card()
    account = _account(client_id=42, client_name="Иван Петров")

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "закреплён за этим клиентом: Иван Петров" in text


def test_card_shows_the_recognized_surface() -> None:
    """Ревью 2.6: распознанная площадка (уже видна в карточке брифа) должна быть
    видна и здесь, рядом со ссылкой — это заметно ускоряет сверку глазами."""
    card = _card(surface_title="Сообщество ВКонтакте")
    account = _account()

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "Сообщество ВКонтакте" in text


def test_card_without_recognized_surface_omits_the_line() -> None:
    """Пустая `surface_title` (сервис ещё не распознал площадку) — карточка не
    придумывает строку из ничего."""
    card = _card(surface_title="")
    account = _account()

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "Площадка" not in text


def test_third_party_advertiser_is_shown_with_inn() -> None:
    """Конечный рекламодатель кабинета — видно, даже когда это не владелец кабинета."""
    account = _account(
        advertiser_kind="third_party", advertiser_name="ООО «Ромашка»", advertiser_inn="7701234567"
    )

    text = creative.render_launch_confirmation(_card(), account, "Подписчики")

    assert "ООО «Ромашка»" in text
    assert "7701234567" in text


def test_card_escapes_html_special_characters() -> None:
    """Клиент/кабинет — данные снаружи, могут содержать `<`/`&`: тот же дефект,
    что уже чинили для заголовка кабинета (`_ask_goal`) — здесь то же лечение."""
    card = _card(client_name="Иван <b>Петров</b> & Co")
    account = _account(title="Кабинет <script>alert(1)</script>")

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "<script>" not in text
    assert "<b>Петров</b>" not in text
    assert "&lt;script&gt;" in text
    assert "&amp; Co" in text


# --- C2: баланс кабинета в карточке подтверждения ---------------------------------
#
# `_card()` даёт «Бюджет: 10 000 ₽» — дневной бюджет по формуле MVP (бюджет / 30
# дней, `services.launch.DEFAULT_TERM_DAYS`) выходит 333.33 ₽.


def test_balance_shown_when_above_daily_budget() -> None:
    """Баланс известен и его достаточно — показан, предупреждения нет."""
    account = _account(balance_rub="5000.00")

    text = creative.render_launch_confirmation(_card(), account, "Подписчики")

    assert "💳 Баланс кабинета: 5000.00 ₽" in text
    assert "меньше дневного бюджета" not in text


def test_balance_below_daily_budget_warns_but_does_not_block() -> None:
    """Баланса меньше дневного бюджета брифа — спокойное предупреждение, но
    карточка по-прежнему строится (запуск ничем не блокируется)."""
    account = _account(balance_rub="100.00")

    text = creative.render_launch_confirmation(_card(), account, "Подписчики")

    assert "💳 Баланс кабинета: 100.00 ₽" in text
    assert "меньше дневного бюджета" in text
    assert "агентский аккаунт" in text  # ведёт к действию, а не грозит сбоем
    assert "решение за вами" in text.lower() or "не влияет" in text


def test_balance_unknown_omits_the_line() -> None:
    """Баланс не известен (VK не ответил, свежий кабинет) — строку не выдумываем."""
    account = _account(balance_rub=None)

    text = creative.render_launch_confirmation(_card(), account, "Подписчики")

    assert "Баланс кабинета" not in text


def test_balance_not_warned_when_budget_needs_discussion() -> None:
    """Бюджет брифа — «готов обсудить», сумма неизвестна: сравнивать не с чем,
    предупреждение не выдумываем из воздуха."""
    card = _card(
        fields=[
            BriefFieldItem(n=13, label="Бюджет", value="готов обсудить"),
        ]
    )
    account = _account(balance_rub="1.00")

    text = creative.render_launch_confirmation(card, account, "Подписчики")

    assert "💳 Баланс кабинета: 1.00 ₽" in text
    assert "меньше дневного бюджета" not in text


# --- «Без креатива»: запуск ждёт подтверждения, сквозной сценарий ----------------


class _FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
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


class _FakeState:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = dict(data)
        self.cleared = False
        self.state: Any = None

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def update_data(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def clear(self) -> None:
        self.cleared = True
        self.data = {}


def test_got_description_shows_full_card_with_creative(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же боевой путь (`bot/handlers/creative.py:got_description`): карточка
    подтверждения после ввода описания несёт клиента, объект, цель, бюджет и
    кабинет — не только заголовок и текст креатива, как было раньше.

    Между описанием и карточкой встал шаг «хэштеги?» (Task 5) — сценарий
    сквозной: описание → «Без хэштегов» → карточка, ровно как выберет оператор,
    которому хэштеги не нужны.
    """

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    monkeypatch.setattr("bot.api_client.get_brief", fake_get_brief)
    monkeypatch.setattr(creative, "Message", _FakeMessage)
    state = _FakeState(
        {
            "brief_id": 9,
            "file_id": "fid",
            "media_type": "photo",
            "width": 800,
            "height": 800,
            "ad_account_id": 3,
            "ad_account_title": "Кабинет Ромашки",
            "ad_account_external_id": "10000003",
            "ad_account_advertiser_kind": "owner",
            "ad_account_advertiser_name": None,
            "ad_account_advertiser_inn": None,
            "ad_account_client_id": None,
            "ad_account_client_name": None,
            "goal": "subscribers",
        }
    )
    message = _FakeMessage("Заголовок объявления\nТекст объявления")

    asyncio.run(creative.got_description(message, state))
    skip_callback = _FakeCallback("hashtags_skip")
    asyncio.run(creative.skip_hashtags(skip_callback, state))

    text, markup = skip_callback.message.answers[-1]
    assert "Иван Петров" in text  # клиент
    assert "vk.com/ivan" in text  # объект рекламы
    assert "Подписчики" in text  # цель по-русски
    assert "10 000 ₽" in text  # бюджет
    assert "Кабинет Ромашки" in text  # кабинет
    assert "Заголовок объявления" in text  # креатив по-прежнему на месте
    assert markup is not None
    assert not state.cleared


def test_launch_without_creative_end_to_end_waits_for_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Показ карточки сам по себе не запускает кампанию; запускает только явное
    нажатие «Запустить» на самой карточке (Т3 — раньше кнопка карточки брифа
    отправляла кампанию в ядро одним нажатием, без единого шанса передумать).

    Кабинет закреплён за клиентом брифа (`client_id=42`) — у клиента уже есть
    свой кабинет, поэтому шаг C1 (предложение завести кабинет автоматически)
    здесь не должен показываться; он проверен отдельно в
    `test_launch_without_creative_offers_cabinet_creation_when_client_has_none`.
    """
    launched: dict[str, Any] = {}

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(id=3, client_id=42, client_name="Иван Петров")]

    async def fake_launch(brief_id: int, ad_account_id: int | None = None) -> Any:
        launched["brief_id"] = brief_id
        launched["ad_account_id"] = ad_account_id
        return SimpleNamespace(campaign_status="prepared", campaign_id=1, message="🚀 подготовлена")

    monkeypatch.setattr("bot.api_client.get_brief", fake_get_brief)
    monkeypatch.setattr("bot.api_client.list_ad_accounts", fake_list)
    monkeypatch.setattr("bot.api_client.launch_brief", fake_launch)
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)

    # Шаг 1: карточка брифа → показывается карточка подтверждения, ядро молчит.
    show_callback = _FakeCallback("launch:9")
    asyncio.run(brief_card.launch_without_creative(show_callback))
    assert launched == {}
    text, markup = show_callback.message.answers[-1]
    assert "Иван Петров" in text
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "nocre_confirm:9:3" in datas

    # Шаг 2: оператор нажимает «Запустить» — только теперь ядро видит запрос.
    confirm_callback = _FakeCallback("nocre_confirm:9:3")
    asyncio.run(brief_card.confirm_launch_without_creative(confirm_callback))
    assert launched == {"brief_id": 9, "ad_account_id": 3}


# --- Отказы ядра 409 показаны человеческим текстом --------------------------------


def test_advertiser_mismatch_shown_as_human_text_on_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """`launch_brief` (сценарий без креатива): 409 `advertiser_mismatch` — понятный
    текст про ИНН, а не голый код ошибки."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/briefs/9/launch").mock(
                return_value=httpx.Response(409, json={"detail": "advertiser_mismatch"})
            )
            with pytest.raises(CreativeRejected) as excinfo:
                await api_client.launch_brief(9, ad_account_id=3)
        reason = excinfo.value.reason
        assert "advertiser_mismatch" not in reason
        assert "ИНН" in reason

    asyncio.run(scenario())


def test_ad_account_client_mismatch_shown_as_human_text_on_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`launch_brief`: 409 `ad_account_client_mismatch` — понятный текст про чужого
    клиента, а не голый код ошибки."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/briefs/9/launch").mock(
                return_value=httpx.Response(409, json={"detail": "ad_account_client_mismatch"})
            )
            with pytest.raises(CreativeRejected) as excinfo:
                await api_client.launch_brief(9, ad_account_id=3)
        reason = excinfo.value.reason
        assert "ad_account_client_mismatch" not in reason
        assert "другим клиентом" in reason

    asyncio.run(scenario())


def test_advertiser_mismatch_shown_as_human_text_on_creative_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тот же код 409, но со стороны загрузки креатива (`upload_creative`) —
    сценарий тоже должен получить понятный текст, а не код."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/briefs/9/creative").mock(
                return_value=httpx.Response(409, json={"detail": "advertiser_mismatch"})
            )
            with pytest.raises(CreativeRejected) as excinfo:
                await api_client.upload_creative(
                    9, "YQ==", "photo", 800, 800, "T", "B", ad_account_id=3, goal="subscribers"
                )
        reason = excinfo.value.reason
        assert "advertiser_mismatch" not in reason
        assert "ИНН" in reason

    asyncio.run(scenario())


def test_no_ad_account_still_maps_to_cabinet_choice_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Существующее поведение не задето: `no_ad_account`/`ambiguous_ad_account`
    по-прежнему `CabinetChoiceRequired`, а не человеческий текст отказа."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/briefs/9/launch").mock(
                return_value=httpx.Response(409, json={"detail": "no_ad_account"})
            )
            with pytest.raises(CabinetChoiceRequired) as excinfo:
                await api_client.launch_brief(9)
        assert excinfo.value.reason == "no_ad_account"

    asyncio.run(scenario())
