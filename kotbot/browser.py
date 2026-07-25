"""Playwright-бэкенд браузерной автоматизации (K-PR3, spec §3, §4.1, §4.2).

Реализует `AutomationBackend` поверх Chromium:

- **ленивый старт** браузера (первый флоу поднимает, дальше переиспользуем) с
  контейнер-безопасными аргументами `--disable-dev-shm-usage`, `--disable-gpu`;
  `headless` и `browser_channel` — из `kotbot/config.py`;
- **глобальный `asyncio.Lock`** — один флоу за раз (spec §3): припаркованный в
  ожидании кода флоу держит Lock до `submit_code`/`close_attempt` (TTL попытки
  в сервисе гарантирует освобождение);
- **контекст на флоу** из `storage_state`; при успехе state сериализуется в
  `LoginOutcome.storage_state` — шифрует и хранит его сервис (`StateStore`),
  бэкенд внутрь не заглядывает;
- при падении шага — **скриншот** в `{secrets_dir}/debug/`, имя составляется
  только из машинного имени шага и метки времени (никаких секретов).

Что именно автоматизируем: вход в кабинет **ads.vk.ru** через VK ID (разведка
2026-07-17, spec §10.1). Форма входа по почте на kotbot.ru не снималась и
кабинет ads.vk.ru всё равно не авторизует — стратегия `email` честно отвечает
`not_scouted`, селекторы не выдумываем.

Playwright импортируется **только** внутри `PlaywrightLauncher.launch` — модуль
импортируется на машине и в CI без установленных браузеров (spec §14), а тесты
работают на фейках, реализующих протоколы `BrowserLauncher`/`BrowserHandle`/
`ContextHandle`/`PageHandle`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from kotbot.adsvk_flow import is_logged_in, text_locator
from kotbot.backend import LoginOutcome, SessionCheck
from kotbot.config import KotbotSettings, get_settings
from kotbot.pageapi import PageLike
from kotbot.selectors import (
    CAPTCHA_MARKER_TEXT,
    CONTINUE_BUTTON_TEXT,
    HQ_URL,
    VKID_CONSENT_PREFIX_TEXT,
    VKID_OTP_CELL,
    VKID_PASSWORD_INPUT,
    VKID_PHONE_INPUT,
)

logger = logging.getLogger(__name__)

# Аргументы запуска Chromium: контейнер с ограниченным /dev/shm и без GPU (spec §11).
LAUNCH_ARGS: tuple[str, ...] = ("--disable-dev-shm-usage", "--disable-gpu")

# Пауза после действия — дать VK ID дорисовать следующий экран (маски/анимации).
SETTLE_MS = 1500

# Задержка между символами при вводе в маскированные поля (телефон, ячейки кода).
TYPE_DELAY_MS = 50

# Стратегии, поддержанные браузерным бэкендом (см. заголовок модуля про `email`).
_SUPPORTED_STRATEGIES = ("email", "vk")


# --- Протоколы браузера (в тестах реализуются фейками) ------------------------------


class PageHandle(PageLike, Protocol):
    """Страница браузера: `PageLike` (его ждёт `adsvk_flow`) + скриншот для отладки."""

    async def screenshot(self, path: str) -> None:
        """Сохранить PNG страницы по абсолютному пути."""
        ...


class ContextHandle(Protocol):
    """Контекст браузера — изолированная сессия одного флоу."""

    async def new_page(self) -> PageHandle:
        """Открыть новую страницу в контексте."""
        ...

    async def storage_state(self) -> bytes:
        """Сериализованный storage_state (JSON в utf-8) для сохранения сервисом."""
        ...

    async def close(self) -> None:
        """Закрыть контекст и освободить память браузера."""
        ...


class BrowserHandle(Protocol):
    """Запущенный браузер: раздаёт контексты, закрывается на остановке сервиса."""

    async def new_context(self, storage_state: bytes | None) -> ContextHandle:
        """Создать контекст, при наличии — восстановив сессию из `storage_state`."""
        ...

    async def close(self) -> None:
        """Закрыть браузер."""
        ...


class BrowserLauncher(Protocol):
    """Запуск браузера — точка подмены Playwright на фейк в тестах."""

    async def launch(self, *, headless: bool, channel: str, args: Sequence[str]) -> BrowserHandle:
        """Поднять браузер с заданными параметрами."""
        ...


# --- Припаркованный флоу ------------------------------------------------------------


@dataclass(slots=True)
class _Attempt:
    """Флоу, припаркованный на экране кода: держит контекст, страницу и Lock."""

    strategy: str
    context: ContextHandle
    page: PageHandle
    holds_lock: bool = True
    closed: bool = False


# --- Бэкенд -------------------------------------------------------------------------


class PlaywrightBackend:
    """Браузерная автоматизация входа: реализация `AutomationBackend` (spec §3)."""

    def __init__(
        self,
        settings: KotbotSettings | None = None,
        launcher: BrowserLauncher | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._launcher = launcher or PlaywrightLauncher(self._settings.nav_timeout_ms)
        self._lock = asyncio.Lock()
        self._browser: BrowserHandle | None = None

    # --- публичный контракт ---------------------------------------------------------

    async def login(self, strategy: str, login: str, password: str) -> LoginOutcome:
        """Войти стратегией: ok | code_required (флоу припаркован) | error."""
        if strategy not in _SUPPORTED_STRATEGIES:
            logger.warning("kotbot: неизвестная стратегия входа %r", strategy)
            return LoginOutcome(status="error", error_code="invalid_strategy")
        if strategy == "email":
            # Селекторы формы kotbot.ru не сняты на разведке — не выдумываем (§10.1).
            logger.warning("kotbot: стратегия email не реализована браузером (not_scouted)")
            return LoginOutcome(status="error", error_code="not_scouted")

        await self._lock.acquire()
        attempt: _Attempt | None = None
        try:
            browser = await self._ensure_browser()
            context = await browser.new_context(None)
            page = await context.new_page()
            attempt = _Attempt(strategy=strategy, context=context, page=page)
            outcome = await self._run_login(attempt, login, password)
            if outcome.status != "code_required":
                # Терминальный исход: контекст закрыт, Lock отпущен.
                await self._finish(attempt)
            return outcome
        except Exception:
            logger.exception("kotbot: вход стратегией %s не выполнен", strategy)
            if attempt is not None:
                await self._screenshot(attempt.page, "login")
                await self._finish(attempt)
            else:
                self._lock.release()
            return LoginOutcome(status="error", error_code="flow_failed")

    async def submit_code(self, attempt: object, code: str) -> LoginOutcome:
        """Ввести код подтверждения в припаркованный флоу."""
        parked = self._as_attempt(attempt)
        if parked is None:
            return LoginOutcome(status="error", error_code="attempt_expired")
        try:
            outcome = await self._run_submit_code(parked, code)
        except Exception:
            logger.exception("kotbot: ввод кода не выполнен (стратегия %s)", parked.strategy)
            await self._screenshot(parked.page, "submit_code")
            await self._finish(parked)
            return LoginOutcome(status="error", error_code="flow_failed")
        if outcome.error_code != "code_invalid":
            # Терминальный исход: освобождаем контекст и глобальный Lock.
            await self._finish(parked)
        return outcome

    async def close_attempt(self, attempt: object) -> None:
        """Закрыть припаркованный флоу (истёк TTL / отмена) — освободить браузер."""
        parked = self._as_attempt(attempt)
        if parked is None:
            return
        await self._finish(parked)

    async def check_session(self, strategy: str, storage_state: bytes) -> SessionCheck:
        """Проверить браузером, жива ли сохранённая сессия (spec §4.2, ступени 1–2)."""
        async with self._lock:
            browser = await self._ensure_browser()
            context = await browser.new_context(storage_state)
            page = await context.new_page()
            try:
                await page.goto(HQ_URL, wait_until="domcontentloaded")
                if not await is_logged_in(page):
                    logger.info("kotbot: сессия стратегии %s протухла", strategy)
                    return SessionCheck(logged_in=False)
                return SessionCheck(logged_in=True, storage_state=await context.storage_state())
            except Exception:
                logger.exception("kotbot: проверка сессии %s не выполнена", strategy)
                await self._screenshot(page, "check_session")
                return SessionCheck(logged_in=False)
            finally:
                await context.close()

    async def close(self) -> None:
        """Закрыть браузер (остановка сервиса). Повторный вызов безопасен."""
        browser, self._browser = self._browser, None
        if browser is not None:
            await browser.close()

    # --- шаги флоу ------------------------------------------------------------------

    async def _run_login(self, attempt: _Attempt, login: str, password: str) -> LoginOutcome:
        """Вход в кабинет ads.vk.ru через VK ID (разведка §10.1, пп. 1–8)."""
        page = attempt.page
        await page.goto(HQ_URL, wait_until="domcontentloaded")
        if await is_logged_in(page):
            # VK уже пустил в кабинет (например, локальный `browser_channel=chrome`
            # с живым профилем) — форма входа не нужна, сразу снимаем storage_state.
            logger.info("kotbot: сессия стратегии %s уже активна", attempt.strategy)
            return LoginOutcome(status="ok", storage_state=await attempt.context.storage_state())

        if not await page.is_visible(VKID_PHONE_INPUT):
            logger.warning("kotbot: форма входа VK ID не найдена")
            return LoginOutcome(status="error", error_code="login_failed")
        # Поле под маской — вводим посимвольно (§10.1 п.2), логин в лог не пишем.
        await page.type_text(VKID_PHONE_INPUT, login)
        await page.click(text_locator(CONTINUE_BUTTON_TEXT))
        await page.wait_for_timeout(SETTLE_MS)

        captcha = await self._captcha_outcome(page)
        if captcha is not None:
            return captcha

        if await page.is_visible(VKID_PASSWORD_INPUT):
            await page.fill(VKID_PASSWORD_INPUT, password)
            await page.click(text_locator(CONTINUE_BUTTON_TEXT))
            await page.wait_for_timeout(SETTLE_MS)
            captcha = await self._captcha_outcome(page)
            if captcha is not None:
                return captcha

        if await page.is_visible(VKID_OTP_CELL):
            logger.info("kotbot: VK ID запросил код подтверждения — флоу припаркован")
            return LoginOutcome(
                status="code_required",
                attempt=attempt,
                hint="Код подтверждения VK ID (SMS или приложение VK)",
            )

        return await self._complete(attempt, step="login")

    async def _run_submit_code(self, attempt: _Attempt, code: str) -> LoginOutcome:
        """Ввести код в 6 ячеек VK ID (§10.1 п. 4) и дойти до кабинета."""
        page = attempt.page
        # Ячейки раскидывают ввод сами — печатаем в первую, код в лог не пишем.
        await page.type_text(VKID_OTP_CELL, code)
        await page.wait_for_timeout(SETTLE_MS)

        captcha = await self._captcha_outcome(page)
        if captcha is not None:
            return captcha

        if await page.is_visible(VKID_OTP_CELL):
            # Экран кода никуда не делся — код не подошёл, попытка остаётся живой.
            logger.info("kotbot: код не принят, флоу остаётся припаркованным")
            return LoginOutcome(status="error", error_code="code_invalid")
        return await self._complete(attempt, step="submit_code")

    async def _complete(self, attempt: _Attempt, *, step: str) -> LoginOutcome:
        """Добрать экран согласия и убедиться, что кабинет открыт (§10.1 пп. 6–8)."""
        page = attempt.page
        text = await page.content_text()
        if VKID_CONSENT_PREFIX_TEXT in text:
            await page.click(text_locator(VKID_CONSENT_PREFIX_TEXT))
            await page.wait_for_timeout(SETTLE_MS)
        if await is_logged_in(page):
            logger.info("kotbot: вход выполнен, кабинет ads.vk.ru открыт")
            return LoginOutcome(status="ok", storage_state=await attempt.context.storage_state())
        # Непонятный экран (выбор профиля бизнеса, ошибка, редизайн) — скриншот оператору.
        logger.warning("kotbot: неизвестный экран на шаге %s", step)
        await self._screenshot(page, step)
        return LoginOutcome(status="error", error_code="login_failed")

    async def _captcha_outcome(self, page: PageHandle) -> LoginOutcome | None:
        """Капча VK ID программно не проходится (§10.1 п.3) — сразу останавливаемся."""
        text = await page.content_text()
        if CAPTCHA_MARKER_TEXT not in text:
            return None
        logger.warning("kotbot: VK ID показал капчу — нужен ручной вход оператора")
        return LoginOutcome(status="error", error_code="captcha_required")

    # --- инфраструктура -------------------------------------------------------------

    async def _ensure_browser(self) -> BrowserHandle:
        """Ленивый старт браузера: поднимаем на первом флоу, дальше переиспользуем."""
        if self._browser is None:
            logger.info(
                "kotbot: запуск Chromium (headless=%s, channel=%s)",
                self._settings.headless,
                self._settings.browser_channel or "default",
            )
            self._browser = await self._launcher.launch(
                headless=self._settings.headless,
                channel=self._settings.browser_channel,
                args=LAUNCH_ARGS,
            )
        return self._browser

    def _as_attempt(self, attempt: object) -> _Attempt | None:
        """Свой ли это припаркованный флоу (чужой объект → `None`, без падения)."""
        if isinstance(attempt, _Attempt) and not attempt.closed:
            return attempt
        return None

    async def _finish(self, attempt: _Attempt) -> None:
        """Закрыть контекст флоу и отпустить глобальный Lock (идемпотентно)."""
        if attempt.closed:
            return
        attempt.closed = True
        try:
            await attempt.context.close()
        finally:
            if attempt.holds_lock:
                attempt.holds_lock = False
                self._lock.release()

    async def _screenshot(self, page: PageHandle, step: str) -> None:
        """Скриншот упавшего шага в `{secrets_dir}/debug/`.

        В имени файла только машинное имя шага и метка времени — ни логина, ни
        пароля, ни кода (репозиторий и том с секретами не должны их видеть).
        """
        try:
            directory = Path(self._settings.secrets_dir) / "debug"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            path = directory / f"{step}-{stamp}.png"
            await page.screenshot(str(path))
            logger.warning("kotbot: скриншот падения шага %s сохранён в debug/", step)
        except Exception:
            logger.exception("kotbot: не удалось сохранить скриншот шага %s", step)


# --- Реальный Playwright (единственное место импорта библиотеки) --------------------


class PlaywrightLauncher:
    """Запуск настоящего Chromium. Playwright импортируется отложенно, внутри `launch`."""

    def __init__(self, nav_timeout_ms: int = 30000) -> None:
        self._nav_timeout_ms = nav_timeout_ms

    async def launch(self, *, headless: bool, channel: str, args: Sequence[str]) -> BrowserHandle:
        """Поднять Chromium; браузеры ставятся только в образе kotbot (spec §11)."""
        from playwright.async_api import async_playwright  # отложенный импорт (CI без браузеров)

        playwright = await async_playwright().start()
        options: dict[str, Any] = {"headless": headless, "args": list(args)}
        if channel:
            # Локально удобен системный Chrome (VPN/TLS-перехват, spec §14).
            options["channel"] = channel
        browser = await playwright.chromium.launch(**options)
        return _PwBrowser(playwright, browser, self._nav_timeout_ms)


class _PwBrowser:
    """Обёртка `playwright.Browser` под `BrowserHandle` (объекты Playwright — `Any`)."""

    def __init__(self, playwright: Any, browser: Any, nav_timeout_ms: int) -> None:
        self._playwright = playwright
        self._browser = browser
        self._nav_timeout_ms = nav_timeout_ms

    async def new_context(self, storage_state: bytes | None) -> ContextHandle:
        state = json.loads(storage_state.decode("utf-8")) if storage_state else None
        context = await self._browser.new_context(storage_state=state)
        context.set_default_timeout(self._nav_timeout_ms)
        context.set_default_navigation_timeout(self._nav_timeout_ms)
        return _PwContext(context)

    async def close(self) -> None:
        await self._browser.close()
        await self._playwright.stop()


class _PwContext:
    """Обёртка `playwright.BrowserContext` под `ContextHandle`."""

    def __init__(self, context: Any) -> None:
        self._context = context

    async def new_page(self) -> PageHandle:
        return _PwPage(await self._context.new_page())

    async def storage_state(self) -> bytes:
        state = await self._context.storage_state()
        return json.dumps(state).encode("utf-8")

    async def close(self) -> None:
        await self._context.close()


class _PwPage:
    """Обёртка `playwright.Page` под `PageHandle` — единственный мост к живому API."""

    def __init__(self, page: Any) -> None:
        self._page = page

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        await self._page.goto(url, wait_until=wait_until)

    async def inner_text(self, selector: str) -> str:
        text: str = await self._page.inner_text(selector)
        return text

    async def content_text(self) -> str:
        text: str = await self._page.inner_text("body")
        return text

    async def is_visible(self, selector: str) -> bool:
        visible: bool = await self._page.is_visible(selector)
        return visible

    async def click(self, selector: str) -> None:
        await self._page.click(selector)

    async def fill(self, selector: str, value: str) -> None:
        await self._page.fill(selector, value)

    async def type_text(self, selector: str, value: str) -> None:
        # Маскированные поля VK ID (телефон, ячейки кода) не принимают `fill`:
        # фокусируемся и печатаем с клавиатуры посимвольно (§10.1 пп. 2, 4).
        await self._page.click(selector)
        await self._page.keyboard.type(value, delay=TYPE_DELAY_MS)

    async def wait_for_timeout(self, ms: int) -> None:
        await self._page.wait_for_timeout(ms)

    async def screenshot(self, path: str) -> None:
        await self._page.screenshot(path=path)


__all__ = [
    "LAUNCH_ARGS",
    "BrowserHandle",
    "BrowserLauncher",
    "ContextHandle",
    "PageHandle",
    "PlaywrightBackend",
    "PlaywrightLauncher",
]
