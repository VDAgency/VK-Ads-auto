"""Пост-логин-флоу ads.vk.ru на фейковой странице (K-PR3, spec §3).

Playwright в CI не поднимается: флоу покрывается через протокол `PageLike` и
фейковую страницу `FakePage`, которая пишет вызовы и отдаёт заданный текст.
Async-функции гоняем через `asyncio.run(...)` — как в остальных тестах kotbot.
"""

from __future__ import annotations

import asyncio

import pytest
from kotbot.adsvk_flow import (
    FlowError,
    continue_wizard,
    is_logged_in,
    open_campaign_wizard,
    select_subscribers_objective,
    set_daily_budget,
    text_locator,
)
from kotbot.selectors import (
    BUDGET_INPUT_TODO,
    CONTINUE_BUTTON_TEXT,
    OBJECT_TAB_COMMUNITY_TEXT,
    PROMOTE_VK_CARD_TEXT,
    WIZARD_URL,
)


class FakePage:
    """Фейковая реализация `PageLike`: пишет вызовы, отдаёт заданный текст/видимость."""

    def __init__(self, content: str = "", visible: dict[str, bool] | None = None) -> None:
        self.content = content
        self.visible = visible or {}
        self.goto_calls: list[tuple[str, str]] = []
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.types: list[tuple[str, str]] = []
        self.waits: list[int] = []

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.goto_calls.append((url, wait_until))

    async def inner_text(self, selector: str) -> str:
        return self.content

    async def is_visible(self, selector: str) -> bool:
        return self.visible.get(selector, False)

    async def click(self, selector: str) -> None:
        self.clicks.append(selector)

    async def fill(self, selector: str, value: str) -> None:
        self.fills.append((selector, value))

    async def type_text(self, selector: str, value: str) -> None:
        self.types.append((selector, value))

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    async def content_text(self) -> str:
        return self.content


# --- is_logged_in -----------------------------------------------------------------


def test_is_logged_in_true_when_cabinet_marker_without_registration() -> None:
    page = FakePage(content="Кабинет. Создать кампанию, статистика, баланс.")
    assert asyncio.run(is_logged_in(page)) is True


def test_is_logged_in_false_when_registration_present() -> None:
    # Маркетинговая главная для анонима: есть «Регистрация» → не залогинены.
    page = FakePage(content="VK Реклама. Регистрация и вход. Создать кампанию бесплатно.")
    assert asyncio.run(is_logged_in(page)) is False


def test_is_logged_in_false_when_cabinet_marker_absent() -> None:
    page = FakePage(content="Что-то грузится...")
    assert asyncio.run(is_logged_in(page)) is False


# --- open_campaign_wizard ---------------------------------------------------------


def test_open_campaign_wizard_navigates_to_wizard_url() -> None:
    page = FakePage()
    asyncio.run(open_campaign_wizard(page))
    assert page.goto_calls == [(WIZARD_URL, "domcontentloaded")]


# --- select_subscribers_objective -------------------------------------------------


def test_select_subscribers_objective_clicks_card_and_tab() -> None:
    page = FakePage(content="Объект: Сообщество. Целевое действие: Подписка на сообщество.")
    asyncio.run(select_subscribers_objective(page))
    assert page.clicks == [
        text_locator(PROMOTE_VK_CARD_TEXT),
        text_locator(OBJECT_TAB_COMMUNITY_TEXT),
    ]


def test_select_subscribers_objective_raises_flowerror_without_subscribe_marker() -> None:
    # Целевого действия «Подписка на сообщество» на экране нет → останавливаемся.
    page = FakePage(content="Объект: Сообщество. Целевое действие: Сообщения.")
    with pytest.raises(FlowError) as exc:
        asyncio.run(select_subscribers_objective(page))
    assert exc.value.step == "select_subscribers_objective"


# --- set_daily_budget -------------------------------------------------------------


@pytest.mark.parametrize("rub", [0, -1, -100])
def test_set_daily_budget_rejects_non_positive(rub: int) -> None:
    page = FakePage()
    with pytest.raises(ValueError):
        asyncio.run(set_daily_budget(page, rub))
    assert page.fills == []


def test_set_daily_budget_fills_field_with_valid_amount() -> None:
    page = FakePage()
    asyncio.run(set_daily_budget(page, 500))
    assert page.fills == [(BUDGET_INPUT_TODO, "500")]


# --- continue_wizard --------------------------------------------------------------


def test_continue_wizard_clicks_continue_button() -> None:
    page = FakePage()
    asyncio.run(continue_wizard(page))
    assert page.clicks == [text_locator(CONTINUE_BUTTON_TEXT)]
