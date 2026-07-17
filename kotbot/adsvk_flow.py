"""Пост-логин-флоу кабинета ads.vk.ru (VK Реклама) над `PageLike` (K-PR3, spec §3).

Автоматизируем то, что идёт ПОСЛЕ входа (сам вход VK ID captcha-gated и делается
оператором вручную, сессия переиспользуется через storage_state):
  1. определение залогиненности кабинета;
  2. открытие мастера и выбор кампании «подписчики» (Режим эксперта → Целевые
     действия → сообщество → «Подписка на сообщество»);
  3. дневной бюджет и переход к следующему шагу мастера.

Функции работают только через протокол `PageLike` и НЕ импортируют Playwright:
это держит модуль тестируемым на фейковой странице (Playwright в CI не поднимается,
spec §14). Шаги ссылаются на разведку 2026-07-17 (`kotbot/selectors.py`).
"""

from __future__ import annotations

from kotbot.pageapi import PageLike
from kotbot.selectors import (
    BUDGET_INPUT_TODO,
    CONTINUE_BUTTON_TEXT,
    LOGGED_IN_MARKER_TEXT,
    LOGGED_OUT_MARKER_TEXT,
    OBJECT_TAB_COMMUNITY_TEXT,
    PROMOTE_VK_CARD_TEXT,
    TARGET_ACTION_SUBSCRIBE_TEXT,
    WIZARD_URL,
)


class FlowError(Exception):
    """Ошибка шага флоу ads.vk.ru; `step` — машинное имя шага для диагностики."""

    def __init__(self, step: str, message: str = "") -> None:
        super().__init__(message or step)
        self.step = step


def text_locator(text: str) -> str:
    """Playwright-локатор по видимому тексту (подстрока, регистронезависимо).

    Обёртка вокруг text-движка Playwright: держит формат локатора в одном месте,
    чтобы флоу и тесты одинаково собирали селектор из текстовых констант.
    """
    return f"text={text}"


async def is_logged_in(page: PageLike) -> bool:
    """Залогинен ли кабинет ads.vk.ru (разведка 2026-07-17, «Маркеры залогиненности»).

    True, только если в тексте страницы есть маркер кабинета «Создать кампанию» и
    нет маркера анонимной главной «Регистрация» — иначе сессия не подхватилась.
    """
    text = await page.content_text()
    return LOGGED_IN_MARKER_TEXT in text and LOGGED_OUT_MARKER_TEXT not in text


async def open_campaign_wizard(page: PageLike) -> None:
    """Открыть мастер создания кампании (разведка 2026-07-17, `WIZARD_URL`)."""
    await page.goto(WIZARD_URL, wait_until="domcontentloaded")


async def select_subscribers_objective(page: PageLike) -> None:
    """Выбрать кампанию «подписчики»: карточка VK → вкладка сообщества (разведка 2026-07-17).

    Кликаем карточку «что рекламируем» и вкладку объекта-сообщества; целевое
    действие «Подписка на сообщество» для сообщества проставляется по умолчанию —
    проверяем его наличие в тексте экрана, иначе бросаем `FlowError`.
    """
    await page.click(text_locator(PROMOTE_VK_CARD_TEXT))
    await page.click(text_locator(OBJECT_TAB_COMMUNITY_TEXT))
    text = await page.content_text()
    if TARGET_ACTION_SUBSCRIBE_TEXT not in text:
        raise FlowError(
            "select_subscribers_objective",
            "целевое действие «Подписка на сообщество» не найдено на экране",
        )


async def set_daily_budget(page: PageLike, rub: int) -> None:
    """Задать дневной бюджет в рублях (разведка 2026-07-17, шаг «Настройка кампании»).

    `rub` должен быть положительным. Селектор поля — best-effort (TODO(live):
    точный не снят); заполняем через `fill`.
    """
    if rub <= 0:
        raise ValueError("daily budget must be positive")
    await page.fill(BUDGET_INPUT_TODO, str(rub))


async def continue_wizard(page: PageLike) -> None:
    """Перейти к следующему шагу мастера — кнопка «Продолжить» (разведка 2026-07-17)."""
    await page.click(text_locator(CONTINUE_BUTTON_TEXT))
