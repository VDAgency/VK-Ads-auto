"""Протокол страницы браузера для автоматизации ads.vk.ru (K-PR3, spec §3).

Модуль `adsvk_flow` работает не с живым Playwright, а с этим узким протоколом:
в проде его реализует тонкая обёртка над `playwright.async_api.Page`, а в тестах —
фейковая страница. Так пост-логин-флоу покрывается юнит-тестами без запуска
браузера (Playwright в CI не поднимается, spec §14). Playwright здесь не
импортируется — модуль остаётся чистым от браузерных зависимостей.
"""

from __future__ import annotations

from typing import Protocol


class PageLike(Protocol):
    """Минимальный набор операций страницы, нужный флоу ads.vk.ru."""

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        """Перейти по URL и дождаться события загрузки `wait_until`."""
        ...

    async def inner_text(self, selector: str) -> str:
        """Видимый текст первого элемента по селектору."""
        ...

    async def is_visible(self, selector: str) -> bool:
        """Виден ли элемент по селектору прямо сейчас."""
        ...

    async def click(self, selector: str) -> None:
        """Кликнуть по элементу, найденному селектором."""
        ...

    async def fill(self, selector: str, value: str) -> None:
        """Очистить поле и вписать значение одним действием."""
        ...

    async def type_text(self, selector: str, value: str) -> None:
        """Ввести значение посимвольно — для маскированных полей VK (телефон/код)."""
        ...

    async def wait_for_timeout(self, ms: int) -> None:
        """Пауза на `ms` миллисекунд (мягкое ожидание анимаций/масок)."""
        ...

    async def content_text(self) -> str:
        """Весь видимый текст body — по нему определяем экран и его маркеры."""
        ...
