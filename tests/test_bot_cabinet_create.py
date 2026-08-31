"""Тесты шага C1 (план 2026-08-25-agency-cabinets, волна C): предложение
завести клиенту рекламный кабинет автоматически перед выбором кабинета.

Общая логика (`offer_cabinet_creation`, `render_cabinet_create_card`,
`create_cabinet_or_report`) живёт в `bot/handlers/creative.py` и используется
обоими сценариями запуска — с креативом (`creative.start_creative`) и без
(`brief_card.launch_without_creative`).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from bot import api_client
from bot.api_client import AdAccountItem, AgencyCabinetRejected, BriefCard, BriefFieldItem
from bot.handlers import brief_card, creative
from bot.states import LaunchCampaign

_CORE = "http://api:8000"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.get_settings", lambda: SimpleNamespace(core_base_url=_CORE))


def _confirmed(monkeypatch: pytest.MonkeyPatch, *, value: bool = True) -> None:
    """Мок предохранителя `vk_agency_confirmed`, который `offer_cabinet_creation`
    читает первым делом (ревью ветки §1) — тесты явно управляют им, а не
    полагаются на реальные настройки окружения процесса."""
    monkeypatch.setattr(
        creative, "get_settings", lambda: SimpleNamespace(vk_agency_confirmed=value)
    )


def _account(**over: Any) -> AdAccountItem:
    base: dict[str, Any] = {
        "id": 1,
        "title": "Общий кабинет",
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
        "client_id": None,
        "client_name": None,
    }
    base.update(over)
    return AdAccountItem(**base)


def _card(**over: Any) -> BriefCard:
    base: dict[str, Any] = {
        "brief_id": 7,
        "variant": "individual",
        "status": "received",
        "client_name": "Иван Петров",
        "client_email": None,
        "client_phone": None,
        "client_telegram": None,
        "fields": [BriefFieldItem(n=5, label="ИНН", value="770123456789")],
        "has_creative": False,
        "campaign_status": None,
        "client_id": 42,
    }
    base.update(over)
    return BriefCard(**base)


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


# --- offer_cabinet_creation: когда показывать шаг --------------------------------


def test_offer_cabinet_creation_shows_card_when_client_has_no_own_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Только общие кабинеты (или их нет вовсе) — карточка создания показана,
    а не сразу выбор кабинета. Предохранитель `vk_agency_confirmed` включён —
    иначе шаг не показывается вовсе (см. тесты ниже, ревью ветки §1)."""
    _confirmed(monkeypatch)
    message = _FakeMessage()
    card = _card()
    accounts = [_account(client_id=None)]  # общий кабинет, не клиента

    shown = asyncio.run(
        creative.offer_cabinet_creation(message, card, accounts, action="creative")  # type: ignore[arg-type]
    )

    assert shown is True
    text, markup = message.answers[-1]
    assert "нет своего рекламного кабинета" in text
    assert markup is not None
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "cabcreate:creative:7" in datas
    assert "cabcreate_skip:creative:7" in datas


def test_offer_cabinet_creation_skips_entirely_when_agency_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Предохранитель `vk_agency_confirmed` выключен (боевая проверка ещё не
    пройдена — сегодняшний дефолт) — шаг C1 не показывается вовсе, даже когда
    у клиента нет своего кабинета и не хватает ИНН: ни карточки, ни
    предупреждения про ИНН, поток идёт как раньше (ревью ветки §1)."""
    _confirmed(monkeypatch, value=False)
    message = _FakeMessage()
    card = _card(fields=[])  # ИНН тоже не хватает — предупреждения быть не должно
    accounts = [_account(client_id=None)]

    shown = asyncio.run(
        creative.offer_cabinet_creation(message, card, accounts, action="creative")  # type: ignore[arg-type]
    )

    assert shown is False
    assert message.answers == []


def test_offer_cabinet_creation_skips_when_client_already_has_own_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """У клиента уже есть кабинет, закреплённый именно за ним — шаг не нужен."""
    _confirmed(monkeypatch)
    message = _FakeMessage()
    card = _card()
    accounts = [_account(id=2, client_id=42, client_name="Иван Петров")]

    shown = asyncio.run(
        creative.offer_cabinet_creation(message, card, accounts, action="creative")  # type: ignore[arg-type]
    )

    assert shown is False
    assert message.answers == []


def test_offer_cabinet_creation_warns_without_blocking_when_tax_id_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ревью: без ИНН создание не предлагается вовсе, но поток не блокируется —
    оператор всё ещё может продолжить с общим кабинетом."""
    _confirmed(monkeypatch)
    message = _FakeMessage()
    card = _card(fields=[])  # ни одного поля — ИНН неизвестен
    accounts = [_account(client_id=None)]

    shown = asyncio.run(
        creative.offer_cabinet_creation(message, card, accounts, action="creative")  # type: ignore[arg-type]
    )

    assert shown is False  # не остановились — вызывающая сторона идёт дальше
    text, _markup = message.answers[-1]
    assert "ИНН" in text
    assert "не завести" in text


def test_offer_cabinet_creation_warns_without_blocking_when_name_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _confirmed(monkeypatch)
    message = _FakeMessage()
    card = _card(client_name=None)
    accounts: list[AdAccountItem] = []

    shown = asyncio.run(
        creative.offer_cabinet_creation(message, card, accounts, action="creative")  # type: ignore[arg-type]
    )

    assert shown is False
    text, _markup = message.answers[-1]
    assert "имя" in text.lower()


def test_offer_cabinet_creation_skips_when_brief_has_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`client_id=None` на брифе — заводить кабинет решительно не для кого."""
    _confirmed(monkeypatch)
    message = _FakeMessage()
    card = _card(client_id=None)
    accounts: list[AdAccountItem] = []

    shown = asyncio.run(
        creative.offer_cabinet_creation(message, card, accounts, action="creative")  # type: ignore[arg-type]
    )

    assert shown is False
    assert message.answers == []


def test_cabinet_create_card_shows_advertiser_and_proposed_name() -> None:
    text = creative.render_cabinet_create_card(_card())

    assert "Иван Петров" in text
    assert "770123456789" in text
    assert "«Иван Петров»" in text  # предлагаемое имя кабинета


def test_cabinet_create_card_escapes_html_special_characters() -> None:
    card = _card(client_name="Иван <b>Петров</b> & Co")

    text = creative.render_cabinet_create_card(card)

    assert "<b>Петров</b>" not in text
    assert "&lt;b&gt;Петров&lt;/b&gt;" in text
    assert "&amp; Co" in text


# --- create_cabinet_or_report: результат нажатия «Создать кабинет» ---------------


def test_create_cabinet_or_report_success_continues_with_fresh_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_create(
        client_id: int, full_name: str, tax_id: str, niche: str | None = None
    ) -> Any:
        captured.update(client_id=client_id, full_name=full_name, tax_id=tax_id, niche=niche)
        return _account(id=9, client_id=42, client_name="Иван Петров")

    # Двухфазно (ревью ветки §4): первый вызов — перепроверка ДО обращения в
    # ядро, у клиента своего кабинета ещё нет; второй — уже после создания.
    list_calls = {"n": 0}

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return [_account(id=1, client_id=None, title="Общий")]
        return [_account(id=9, client_id=42, client_name="Иван Петров")]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "create_agency_cabinet", fake_create)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    message = _FakeMessage()

    result = asyncio.run(creative.create_cabinet_or_report(message, 7))  # type: ignore[arg-type]

    assert result is not None
    card, accounts = result
    assert card.brief_id == 7
    assert [a.id for a in accounts] == [9]
    assert captured == {
        "client_id": 42,
        "full_name": "Иван Петров",
        "tax_id": "770123456789",
        "niche": None,
    }
    assert any("создан" in text.lower() for text, _ in message.answers)
    assert list_calls["n"] == 2


def test_create_cabinet_or_report_skips_create_when_cabinet_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повторное нажатие «Создать кабинет» (двойной тап/долгая операция уже
    завершилась с прошлого раза): перепроверка находит, что у клиента кабинет
    уже есть, и в VK второй раз не идёт — иначе завела бы второго реального
    клиента агентства (ревью ветки §4)."""

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fail_create(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("create_agency_cabinet не должен вызываться повторно")

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(id=9, client_id=42, client_name="Иван Петров")]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "create_agency_cabinet", fail_create)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    message = _FakeMessage()

    result = asyncio.run(creative.create_cabinet_or_report(message, 7))  # type: ignore[arg-type]

    assert result is not None
    card, accounts = result
    assert card.brief_id == 7
    assert [a.id for a in accounts] == [9]
    assert any("уже есть" in text for text, _ in message.answers)


def test_create_cabinet_or_report_shows_human_text_not_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ ядра доезжает человеческим текстом, а не кодом (`agency_disabled`)."""

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_create(*args: Any, **kwargs: Any) -> Any:
        raise AgencyCabinetRejected(
            "Автоматическое создание кабинетов пока выключено — агентский доступ "
            "VK ещё не подтверждён. Заведите кабинет вручную (/cabinets) или "
            "обратитесь к администратору."
        )

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(client_id=None, title="Общий")]  # своего кабинета ещё нет

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "create_agency_cabinet", fake_create)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    message = _FakeMessage()

    result = asyncio.run(creative.create_cabinet_or_report(message, 7))  # type: ignore[arg-type]

    assert result is None
    text, _markup = message.answers[-1]
    assert "agency_disabled" not in text
    assert "агентский доступ" in text


def test_create_cabinet_or_report_core_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_create(*args: Any, **kwargs: Any) -> Any:
        raise api_client.CoreUnavailable("down")

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(client_id=None, title="Общий")]  # своего кабинета ещё нет

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "create_agency_cabinet", fake_create)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    message = _FakeMessage()

    result = asyncio.run(creative.create_cabinet_or_report(message, 7))  # type: ignore[arg-type]

    assert result is None
    assert any("недоступен" in text.lower() for text, _ in message.answers)


def test_create_cabinet_or_report_stops_if_tax_id_disappeared_meanwhile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Гонка: между показом карточки и нажатием кнопки ИНН стёрли правкой —
    в ядро с заведомо отказным запросом не идём."""

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id, fields=[])

    async def fail_create(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("create_agency_cabinet не должен вызываться без ИНН")

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "create_agency_cabinet", fail_create)
    message = _FakeMessage()

    result = asyncio.run(creative.create_cabinet_or_report(message, 7))  # type: ignore[arg-type]

    assert result is None
    assert any("ИНН" in text for text, _ in message.answers)


# --- отказы ядра: разные тексты для разных причин (api_client) -------------------


@pytest.mark.parametrize(
    ("status_code", "detail", "must_contain", "must_not_contain"),
    [
        (403, "agency_disabled", "агентский доступ", "agency_disabled"),
        (422, "tax_id_required", "ИНН", "tax_id_required"),
        (422, "client_not_found", "клиента", "client_not_found"),
        (500, "encryption_key_missing", "администратора", "encryption_key_missing"),
        (500, "vk_oauth_not_configured", "администратору", "vk_oauth_not_configured"),
        (403, "vk_agency_not_confirmed", "агентский статус", "vk_agency_not_confirmed"),
        (400, "vk_rejected_client_data", "ФИО", "vk_rejected_client_data"),
        (404, "vk_client_not_found", "минуту", "vk_client_not_found"),
        (503, "vk_unreachable", "минуту", "vk_unreachable"),
        # Три кода ниже — выпуск собственного токена агентства, шаг до создания
        # клиента в VK (ревью ветки §3): раньше их не перехватывал ни один
        # блок роутера, кроме vk_oauth_not_configured.
        (500, "vk_oauth_invalid_credentials", "Ключи", "vk_oauth_invalid_credentials"),
        (502, "vk_oauth_rejected", "администратора", "vk_oauth_rejected"),
        (503, "vk_oauth_unavailable", "минуту", "vk_oauth_unavailable"),
        # Неопознанная строковая деталь (например, будущий код ядра, которому
        # ещё не завели свой текст) — честный общий фолбэк, а не выдумка.
        # Заодно закрепляет ревью: "duplicate_account" сюда больше не входит —
        # этот код возвращает только ручное добавление кабинета
        # (`_AD_ACCOUNT_ERRORS`), агентский эндпоинт при дубле отдаёт
        # структуру `cabinet_duplicate` (см. отдельные тесты половинчатых
        # отказов ниже), а не эту строку.
        (409, "some_future_unmapped_code", "получилось", "some_future_unmapped_code"),
    ],
)
def test_create_agency_cabinet_maps_each_detail_to_its_own_human_text(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    detail: str,
    must_contain: str,
    must_not_contain: str,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/ad-accounts/agency-cabinets").mock(
                return_value=httpx.Response(status_code, json={"detail": detail})
            )
            with pytest.raises(AgencyCabinetRejected) as excinfo:
                await api_client.create_agency_cabinet(42, "Иван Петров", "770123456789")
        return excinfo.value.reason

    reason = asyncio.run(scenario())
    assert must_contain in reason
    assert must_not_contain not in reason


def test_create_agency_cabinet_token_issuance_failed_names_the_vk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """502 `token_issuance_failed` — половинчатый провал: клиент в VK уже есть,
    об этом нельзя молчать (CLAUDE.md §7)."""
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/ad-accounts/agency-cabinets").mock(
                return_value=httpx.Response(
                    502,
                    json={
                        "detail": {
                            "error": "token_issuance_failed",
                            "vk_client_id": "vk-777",
                            "vk_username": None,
                        }
                    },
                )
            )
            with pytest.raises(AgencyCabinetRejected) as excinfo:
                await api_client.create_agency_cabinet(42, "Иван Петров", "770123456789")
        return excinfo.value.reason

    reason = asyncio.run(scenario())
    assert "vk-777" in reason
    assert "token_issuance_failed" not in reason
    assert "администратору" in reason


def test_create_agency_cabinet_duplicate_names_the_vk_client_and_does_not_suggest_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """409 `cabinet_duplicate` — клиент в VK уже создан, но кабинет с таким
    внешним номером у нас уже есть (похоже на дубль). Ревью: текст не должен
    звать «попробуйте ещё раз» — повтор в лучшем случае бесполезен, в худшем
    заведёт в VK ещё одного осиротевшего клиента."""
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/ad-accounts/agency-cabinets").mock(
                return_value=httpx.Response(
                    409,
                    json={
                        "detail": {
                            "error": "cabinet_duplicate",
                            "vk_client_id": "vk-101",
                            "vk_username": None,
                        }
                    },
                )
            )
            with pytest.raises(AgencyCabinetRejected) as excinfo:
                await api_client.create_agency_cabinet(42, "Иван Петров", "770123456789")
        return excinfo.value.reason

    reason = asyncio.run(scenario())
    assert "vk-101" in reason
    assert "cabinet_duplicate" not in reason
    assert "администратору" in reason
    assert "попробуйте" not in reason.lower()
    assert "повторная попытка не поможет" in reason.lower()


def test_create_agency_cabinet_client_gone_names_the_vk_client_and_does_not_suggest_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """422 `cabinet_client_gone` — клиент в VK уже создан, но клиент брифа, для
    которого заводили кабинет, пропал между проверкой и сохранением: привязывать
    не к кому. Тот же принцип — без «попробуйте ещё раз»."""
    _configure(monkeypatch)

    async def scenario() -> str:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/ad-accounts/agency-cabinets").mock(
                return_value=httpx.Response(
                    422,
                    json={
                        "detail": {
                            "error": "cabinet_client_gone",
                            "vk_client_id": "vk-202",
                            "vk_username": "ivan.petrov",
                        }
                    },
                )
            )
            with pytest.raises(AgencyCabinetRejected) as excinfo:
                await api_client.create_agency_cabinet(42, "Иван Петров", "770123456789")
        return excinfo.value.reason

    reason = asyncio.run(scenario())
    assert "vk-202" in reason
    assert "cabinet_client_gone" not in reason
    assert "администратору" in reason
    assert "попробуйте" not in reason.lower()
    assert "повторная попытка не поможет" in reason.lower()


def test_create_agency_cabinet_success_returns_ad_account_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    payload = {
        "id": 5,
        "title": "Иван Петров",
        "external_id": "10000005",
        "username": None,
        "token_tail": "abcd",
        "advertiser_kind": "third_party",
        "advertiser_name": "Иван Петров",
        "advertiser_inn": "770123456789",
        "client_id": 42,
        "client_name": "Иван Петров",
        "status": "active",
        "health": "healthy",
        "health_checked_at": None,
        "health_error": None,
        "balance_rub": None,
        "is_usable": True,
    }

    async def scenario() -> AdAccountItem:
        with respx.mock() as router:
            router.post("http://api:8000/api/v1/ad-accounts/agency-cabinets").mock(
                return_value=httpx.Response(201, json=payload)
            )
            return await api_client.create_agency_cabinet(42, "Иван Петров", "770123456789")

    item = asyncio.run(scenario())
    assert item.id == 5
    assert item.client_id == 42


# --- сквозной сценарий: обе точки входа -------------------------------------------


def test_start_creative_shows_cabinet_create_card_before_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`creative.start_creative`: у клиента нет своего кабинета — показывается
    карточка создания, а не сразу выбор цели."""
    _confirmed(monkeypatch)
    monkeypatch.setattr(creative, "Message", _FakeMessage)

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(client_id=None)]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    callback = _FakeCallback("creative:7")
    state = _FakeState()

    asyncio.run(creative.start_creative(callback, state))

    assert state.state is None  # цель ещё не спрашивали
    text, markup = callback.message.answers[-1]
    assert "нет своего рекламного кабинета" in text
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "cabcreate:creative:7" in datas


def test_start_creative_skips_cabinet_card_when_agency_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Флаг выключен (сегодняшний дефолт) — `start_creative` идёт прямо к
    выбору цели, как до появления шага C1, без карточки создания и без
    предупреждения (ревью ветки §1)."""
    _confirmed(monkeypatch, value=False)
    monkeypatch.setattr(creative, "Message", _FakeMessage)

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(client_id=None, title="Общий")]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    callback = _FakeCallback("creative:7")
    state = _FakeState()

    asyncio.run(creative.start_creative(callback, state))

    assert state.state == LaunchCampaign.choosing_goal
    text, _markup = callback.message.answers[-1]
    assert "нет своего рекламного кабинета" not in text
    assert "Выберите цель рекламы" in text


def test_skip_cabinet_create_continues_to_goal_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оператор нажал «Выбрать кабинет вручную» — поток продолжается как раньше."""
    monkeypatch.setattr(creative, "Message", _FakeMessage)

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(client_id=None, title="Общий")]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    callback = _FakeCallback("cabcreate_skip:creative:7")
    state = _FakeState()

    asyncio.run(creative.skip_cabinet_create_for_creative(callback, state))

    assert state.state == LaunchCampaign.choosing_goal
    text, _markup = callback.message.answers[-1]
    assert "Общий" in text
    assert "Выберите цель рекламы" in text


def test_confirm_cabinet_create_continues_to_goal_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Оператор нажал «Создать кабинет» — после успеха поток продолжается
    обычным выбором цели (единственный годный кабинет — новый)."""
    monkeypatch.setattr(creative, "Message", _FakeMessage)

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_create(
        client_id: int, full_name: str, tax_id: str, niche: str | None = None
    ) -> Any:
        return _account(id=9, client_id=42, client_name="Иван Петров", title="Иван Петров")

    # Двухфазно (ревью ветки §4): первый вызов — перепроверка ДО обращения в
    # ядро, у клиента своего кабинета ещё нет; второй — уже после создания.
    list_calls = {"n": 0}

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return [_account(id=1, client_id=None, title="Общий")]
        return [_account(id=9, client_id=42, client_name="Иван Петров", title="Иван Петров")]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "create_agency_cabinet", fake_create)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    callback = _FakeCallback("cabcreate:creative:7")
    state = _FakeState()

    asyncio.run(creative.confirm_cabinet_create_for_creative(callback, state))

    assert state.state == LaunchCampaign.choosing_goal
    texts = [text for text, _ in callback.message.answers]
    assert any("создан" in t.lower() for t in texts)
    assert any("Иван Петров" in t and "цель" in t.lower() for t in texts)


def test_launch_without_creative_offers_cabinet_creation_when_client_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`brief_card.launch_without_creative`: тот же шаг C1, тот же гейт."""
    _confirmed(monkeypatch)
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        return [_account(client_id=None)]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    callback = _FakeCallback("launch:7")

    asyncio.run(brief_card.launch_without_creative(callback))

    text, markup = callback.message.answers[-1]
    assert "нет своего рекламного кабинета" in text
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "cabcreate:nocre:7" in datas


def test_confirm_cabinet_create_for_launch_continues_to_launch_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(brief_card, "Message", _FakeMessage)

    async def fake_get_brief(brief_id: int) -> BriefCard:
        return _card(brief_id=brief_id)

    async def fake_create(
        client_id: int, full_name: str, tax_id: str, niche: str | None = None
    ) -> Any:
        return _account(id=9, client_id=42, client_name="Иван Петров", title="Иван Петров")

    # Двухфазно (ревью ветки §4): первый вызов — перепроверка ДО обращения в
    # ядро, у клиента своего кабинета ещё нет; второй — уже после создания.
    list_calls = {"n": 0}

    async def fake_list(client_id: int | None = None) -> list[AdAccountItem]:
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return [_account(id=1, client_id=None, title="Общий")]
        return [_account(id=9, client_id=42, client_name="Иван Петров", title="Иван Петров")]

    monkeypatch.setattr(api_client, "get_brief", fake_get_brief)
    monkeypatch.setattr(api_client, "create_agency_cabinet", fake_create)
    monkeypatch.setattr(api_client, "list_ad_accounts", fake_list)
    callback = _FakeCallback("cabcreate:nocre:7")

    asyncio.run(brief_card.confirm_cabinet_create_for_launch(callback))

    texts = [text for text, _ in callback.message.answers]
    assert any("создан" in t.lower() for t in texts)
    assert any("Проверьте перед запуском" in t for t in texts)
