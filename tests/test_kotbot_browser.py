"""`PlaywrightBackend` на фейковом браузере (K-PR3, spec §3, §4.1).

Живой Playwright в тестах и в CI не поднимается: бэкенд работает через протоколы
`BrowserLauncher`/`BrowserHandle`/`ContextHandle`/`PageHandle`, а здесь их
реализуют фейки. Проверяем контракт `AutomationBackend` (login / submit_code /
close_attempt), глобальный Lock «один флоу за раз», ленивый старт браузера,
скриншот при падении шага и отсутствие секретов в именах файлов.
"""

from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from kotbot.adsvk_flow import text_locator
from kotbot.browser import LAUNCH_ARGS, PlaywrightBackend
from kotbot.config import KotbotSettings
from kotbot.selectors import (
    CAPTCHA_MARKER_TEXT,
    CONTINUE_BUTTON_TEXT,
    HQ_URL,
    VKID_CONSENT_PREFIX_TEXT,
    VKID_OTP_CELL,
    VKID_PASSWORD_INPUT,
    VKID_PHONE_INPUT,
)

# Тексты экранов: маркер кабинета (залогинены) и анонимная главная.
LOGGED_IN_TEXT = "Кабинет VK Рекламы. Создать кампанию."
ANON_TEXT = "VK Реклама. Регистрация и вход."


@dataclass
class Screen:
    """Один экран фейкового браузера: видимый текст + видимость селекторов."""

    text: str = ""
    visible: dict[str, bool] = field(default_factory=dict)
    fails: bool = False  # действия на этом экране падают — проверяем скриншот


class FakePage:
    """Фейковая страница: экраны переключаются на `wait_for_timeout` (UI «доехал»)."""

    def __init__(self, screens: list[Screen]) -> None:
        self._screens = screens or [Screen()]
        self.index = 0
        self.goto_calls: list[str] = []
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.types: list[tuple[str, str]] = []
        self.screenshots: list[str] = []

    @property
    def _screen(self) -> Screen:
        return self._screens[min(self.index, len(self._screens) - 1)]

    def _guard(self) -> None:
        if self._screen.fails:
            raise RuntimeError("fake page step failed")

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.goto_calls.append(url)
        self.index = 0
        self._guard()

    async def inner_text(self, selector: str) -> str:
        return self._screen.text

    async def content_text(self) -> str:
        return self._screen.text

    async def is_visible(self, selector: str) -> bool:
        return self._screen.visible.get(selector, False)

    async def click(self, selector: str) -> None:
        self._guard()
        self.clicks.append(selector)

    async def fill(self, selector: str, value: str) -> None:
        self._guard()
        self.fills.append((selector, value))

    async def type_text(self, selector: str, value: str) -> None:
        self._guard()
        self.types.append((selector, value))

    async def wait_for_timeout(self, ms: int) -> None:
        self.index = min(self.index + 1, len(self._screens) - 1)

    async def screenshot(self, path: str) -> None:
        self.screenshots.append(path)


class FakeContext:
    """Фейковый контекст: помнит storage_state, из которого создан, и закрытие."""

    def __init__(self, screens: list[Screen], storage_state: bytes | None) -> None:
        self.incoming_state = storage_state
        self.closed = False
        self.pages: list[FakePage] = []
        self._screens = screens

    async def new_page(self) -> FakePage:
        page = FakePage(self._screens)
        self.pages.append(page)
        return page

    async def storage_state(self) -> bytes:
        return json.dumps({"cookies": ["fake"]}).encode("utf-8")

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    """Фейковый браузер: на каждый контекст выдаёт свежий набор экранов."""

    def __init__(self, screens_factory: Callable[[], list[Screen]]) -> None:
        self._screens_factory = screens_factory
        self.contexts: list[FakeContext] = []
        self.closed = False

    async def new_context(self, storage_state: bytes | None) -> FakeContext:
        context = FakeContext(self._screens_factory(), storage_state)
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class FakeLauncher:
    """Фейковый запускатор: записывает параметры запуска, отдаёт один браузер."""

    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.launches: list[tuple[bool, str, tuple[str, ...]]] = []

    async def launch(self, *, headless: bool, channel: str, args: Sequence[str]) -> FakeBrowser:
        self.launches.append((headless, channel, tuple(args)))
        return self.browser


def _settings(tmp_path: Path, **overrides: Any) -> KotbotSettings:
    """Настройки kotbot для теста: каталог секретов — временный."""
    values: dict[str, Any] = {
        "secret_key": "",
        "secrets_dir": str(tmp_path),
        "headless": True,
        "browser_channel": "",
    }
    values.update(overrides)
    return KotbotSettings(**values)


def _backend(
    tmp_path: Path,
    screens: Callable[[], list[Screen]],
    **overrides: Any,
) -> tuple[PlaywrightBackend, FakeLauncher, FakeBrowser]:
    browser = FakeBrowser(screens)
    launcher = FakeLauncher(browser)
    backend = PlaywrightBackend(settings=_settings(tmp_path, **overrides), launcher=launcher)
    return backend, launcher, browser


# --- сценарии экранов -------------------------------------------------------------


def _screens_code_required() -> list[Screen]:
    """Форма VK ID → после «Продолжить» просят код (6 ячеек) → кабинет."""
    return [
        Screen(text="Вход VK ID", visible={VKID_PHONE_INPUT: True}),
        Screen(text="Введите код", visible={VKID_OTP_CELL: True}),
        Screen(text=LOGGED_IN_TEXT),
    ]


def _screens_password_then_ok() -> list[Screen]:
    """Форма VK ID → пароль → кабинет (без кода)."""
    return [
        Screen(text="Вход VK ID", visible={VKID_PHONE_INPUT: True}),
        Screen(text="Введите пароль", visible={VKID_PASSWORD_INPUT: True}),
        Screen(text=LOGGED_IN_TEXT),
    ]


def _screens_captcha() -> list[Screen]:
    return [
        Screen(text="Вход VK ID", visible={VKID_PHONE_INPUT: True}),
        Screen(text=f"{CAPTCHA_MARKER_TEXT}. Я не робот"),
    ]


# --- ленивый старт браузера -------------------------------------------------------


def test_browser_is_not_launched_until_first_flow(tmp_path: Path) -> None:
    backend, launcher, _ = _backend(tmp_path, _screens_code_required)
    assert launcher.launches == []
    asyncio.run(backend.login("vk", "+79990000000", "secret"))
    assert len(launcher.launches) == 1


def test_browser_launched_once_with_configured_flags(tmp_path: Path) -> None:
    backend, launcher, _ = _backend(
        tmp_path, _screens_captcha, headless=False, browser_channel="chrome"
    )

    async def scenario() -> None:
        await backend.login("vk", "+79990000000", "secret")
        await backend.login("vk", "+79990000000", "secret")

    asyncio.run(scenario())
    assert launcher.launches == [(False, "chrome", LAUNCH_ARGS)]


def test_launch_args_carry_container_safe_flags() -> None:
    assert LAUNCH_ARGS == ("--disable-dev-shm-usage", "--disable-gpu")


def test_close_closes_browser_and_allows_relaunch(tmp_path: Path) -> None:
    backend, launcher, browser = _backend(tmp_path, _screens_captcha)

    async def scenario() -> None:
        await backend.login("vk", "+79990000000", "secret")
        await backend.close()
        await backend.close()  # повторный вызов безопасен
        await backend.login("vk", "+79990000000", "secret")

    asyncio.run(scenario())
    assert browser.closed is True
    assert len(launcher.launches) == 2


# --- login ------------------------------------------------------------------------


def test_login_parks_flow_when_code_required(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, _screens_code_required)
    outcome = asyncio.run(backend.login("vk", "+79990000000", "secret"))
    assert outcome.status == "code_required"
    assert outcome.attempt is not None
    assert outcome.hint  # оператору говорим, куда придёт код
    # Флоу припаркован: контекст жив, storage_state ещё не отдан.
    assert browser.contexts[0].closed is False
    assert outcome.storage_state is None


def test_login_opens_ads_cabinet_and_types_login_into_masked_field(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, _screens_code_required)
    asyncio.run(backend.login("vk", "+79990000000", "secret"))
    page = browser.contexts[0].pages[0]
    assert page.goto_calls == [HQ_URL]
    # Поле телефона под маской — вводим посимвольно (spec §10.1 п.2), не fill.
    assert page.types == [(VKID_PHONE_INPUT, "+79990000000")]
    assert text_locator(CONTINUE_BUTTON_TEXT) in page.clicks


def test_login_returns_ok_with_storage_state_when_password_is_enough(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, _screens_password_then_ok)
    outcome = asyncio.run(backend.login("vk", "+79990000000", "secret"))
    assert outcome.status == "ok"
    assert outcome.storage_state is not None
    assert json.loads(outcome.storage_state.decode("utf-8")) == {"cookies": ["fake"]}
    page = browser.contexts[0].pages[0]
    assert (VKID_PASSWORD_INPUT, "secret") in page.fills
    # Терминальный исход — контекст закрыт, браузер свободен.
    assert browser.contexts[0].closed is True


def test_login_returns_ok_when_session_already_alive(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, lambda: [Screen(text=LOGGED_IN_TEXT)])
    outcome = asyncio.run(backend.login("vk", "+79990000000", "secret"))
    assert outcome.status == "ok"
    assert outcome.storage_state is not None
    # Форму не трогали: уже залогинены.
    assert browser.contexts[0].pages[0].types == []


def test_login_reports_captcha_required(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, _screens_captcha)
    outcome = asyncio.run(backend.login("vk", "+79990000000", "secret"))
    assert outcome.status == "error"
    assert outcome.error_code == "captcha_required"
    assert browser.contexts[0].closed is True


def test_login_reports_login_failed_on_unknown_screen(tmp_path: Path) -> None:
    screens = [
        Screen(text="Вход VK ID", visible={VKID_PHONE_INPUT: True}),
        Screen(text="Что-то пошло не так"),
    ]
    backend, _, _ = _backend(tmp_path, lambda: list(screens))
    outcome = asyncio.run(backend.login("vk", "+79990000000", "secret"))
    assert outcome.status == "error"
    assert outcome.error_code == "login_failed"


def test_login_email_strategy_is_not_scouted(tmp_path: Path) -> None:
    # Форма входа по почте на kotbot.ru на разведке не снималась (spec §10.1):
    # селекторы не выдумываем — честный машинный код ошибки.
    backend, launcher, _ = _backend(tmp_path, _screens_code_required)
    outcome = asyncio.run(backend.login("email", "user@example.com", "secret"))
    assert outcome.status == "error"
    assert outcome.error_code == "not_scouted"
    assert launcher.launches == []  # браузер ради этого не поднимаем


def test_login_rejects_unknown_strategy(tmp_path: Path) -> None:
    backend, launcher, _ = _backend(tmp_path, _screens_code_required)
    outcome = asyncio.run(backend.login("carrier-pigeon", "login", "secret"))
    assert outcome.status == "error"
    assert outcome.error_code == "invalid_strategy"
    assert launcher.launches == []


# --- submit_code ------------------------------------------------------------------


def test_submit_code_completes_login_and_returns_storage_state(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, _screens_code_required)

    async def scenario() -> Any:
        started = await backend.login("vk", "+79990000000", "secret")
        return await backend.submit_code(started.attempt, "315340")

    outcome = asyncio.run(scenario())
    assert outcome.status == "ok"
    assert outcome.storage_state is not None
    page = browser.contexts[0].pages[0]
    assert (VKID_OTP_CELL, "315340") in page.types
    assert browser.contexts[0].closed is True


def test_submit_code_clicks_consent_button_when_shown(tmp_path: Path) -> None:
    screens = [
        Screen(text="Вход VK ID", visible={VKID_PHONE_INPUT: True}),
        Screen(text="Введите код", visible={VKID_OTP_CELL: True}),
        Screen(text=f"{VKID_CONSENT_PREFIX_TEXT} Анастасия"),
        Screen(text=LOGGED_IN_TEXT),
    ]
    backend, _, browser = _backend(tmp_path, lambda: list(screens))

    async def scenario() -> Any:
        started = await backend.login("vk", "+79990000000", "secret")
        return await backend.submit_code(started.attempt, "315340")

    outcome = asyncio.run(scenario())
    assert outcome.status == "ok"
    assert text_locator(VKID_CONSENT_PREFIX_TEXT) in browser.contexts[0].pages[0].clicks


def test_submit_code_keeps_flow_parked_on_invalid_code(tmp_path: Path) -> None:
    screens = [
        Screen(text="Вход VK ID", visible={VKID_PHONE_INPUT: True}),
        Screen(text="Введите код", visible={VKID_OTP_CELL: True}),
        Screen(text="Неверный код", visible={VKID_OTP_CELL: True}),
    ]
    backend, _, browser = _backend(tmp_path, lambda: list(screens))

    async def scenario() -> Any:
        started = await backend.login("vk", "+79990000000", "secret")
        return await backend.submit_code(started.attempt, "000000")

    outcome = asyncio.run(scenario())
    assert outcome.status == "error"
    assert outcome.error_code == "code_invalid"
    # Оператор может ввести код ещё раз — контекст остаётся живым.
    assert browser.contexts[0].closed is False


def test_submit_code_rejects_foreign_attempt(tmp_path: Path) -> None:
    backend, _, _ = _backend(tmp_path, _screens_code_required)
    outcome = asyncio.run(backend.submit_code(object(), "315340"))
    assert outcome.status == "error"
    assert outcome.error_code == "attempt_expired"


# --- close_attempt ----------------------------------------------------------------


def test_close_attempt_closes_context_and_is_idempotent(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, _screens_code_required)

    async def scenario() -> None:
        started = await backend.login("vk", "+79990000000", "secret")
        await backend.close_attempt(started.attempt)
        await backend.close_attempt(started.attempt)

    asyncio.run(scenario())
    assert browser.contexts[0].closed is True


def test_close_attempt_ignores_foreign_object(tmp_path: Path) -> None:
    backend, _, _ = _backend(tmp_path, _screens_code_required)
    asyncio.run(backend.close_attempt(object()))


# --- глобальный Lock: один флоу за раз --------------------------------------------


def test_second_flow_waits_until_parked_attempt_is_closed(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, _screens_code_required)

    async def scenario() -> str:
        first = await backend.login("vk", "+79990000000", "secret")
        task = asyncio.create_task(backend.login("vk", "+79990000000", "secret"))
        await asyncio.sleep(0.01)
        assert task.done() is False  # Lock держит припаркованный флоу
        await backend.close_attempt(first.attempt)
        second = await asyncio.wait_for(task, timeout=2)
        return str(second.status)

    assert asyncio.run(scenario()) == "code_required"
    assert len(browser.contexts) == 2


def test_lock_is_released_after_terminal_outcome(tmp_path: Path) -> None:
    backend, _, _ = _backend(tmp_path, _screens_captcha)

    async def scenario() -> str:
        await backend.login("vk", "+79990000000", "secret")
        second = await asyncio.wait_for(backend.login("vk", "+79990000000", "secret"), timeout=2)
        return str(second.error_code)

    assert asyncio.run(scenario()) == "captcha_required"


# --- check_session ----------------------------------------------------------------


def test_check_session_reports_alive_session_and_refreshes_state(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, lambda: [Screen(text=LOGGED_IN_TEXT)])
    check = asyncio.run(backend.check_session("vk", b'{"cookies": []}'))
    assert check.logged_in is True
    assert check.storage_state is not None
    assert browser.contexts[0].incoming_state == b'{"cookies": []}'
    assert browser.contexts[0].closed is True


def test_check_session_reports_expired_session(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, lambda: [Screen(text=ANON_TEXT)])
    check = asyncio.run(backend.check_session("email", b'{"cookies": []}'))
    assert check.logged_in is False
    assert check.storage_state is None
    assert browser.contexts[0].closed is True


def test_check_session_survives_navigation_failure(tmp_path: Path) -> None:
    backend, _, browser = _backend(tmp_path, lambda: [Screen(text="", fails=True)])
    check = asyncio.run(backend.check_session("vk", b'{"cookies": []}'))
    assert check.logged_in is False
    assert browser.contexts[0].closed is True


# --- скриншоты падений ------------------------------------------------------------


def test_failed_step_writes_screenshot_without_secrets_in_name(tmp_path: Path) -> None:
    # Экран падает на любом действии (недоступный VK ID, редизайн, таймаут).
    backend, _, browser = _backend(tmp_path, lambda: [Screen(text="", fails=True)])
    outcome = asyncio.run(backend.login("vk", "+79990000000", "hunter2"))
    assert outcome.status == "error"
    assert outcome.error_code == "flow_failed"
    shots = browser.contexts[0].pages[0].screenshots
    assert len(shots) == 1
    path = Path(shots[0])
    assert path.parent == tmp_path / "debug"
    assert path.parent.is_dir()  # каталог debug создан бэкендом
    assert path.suffix == ".png"
    assert "hunter2" not in path.name and "+79990000000" not in path.name


def test_no_screenshot_on_expected_outcomes(tmp_path: Path) -> None:
    backend, _, _ = _backend(tmp_path, _screens_captcha)
    asyncio.run(backend.login("vk", "+79990000000", "secret"))
    assert not (tmp_path / "debug").exists()


# --- логи без секретов ------------------------------------------------------------


def test_logs_never_contain_password_or_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backend, _, _ = _backend(tmp_path, _screens_code_required)
    caplog.set_level("DEBUG")

    async def scenario() -> None:
        started = await backend.login("vk", "+79990000000", "hunter2")
        await backend.submit_code(started.attempt, "315340")

    asyncio.run(scenario())
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "hunter2" not in joined
    assert "315340" not in joined
    assert "+79990000000" not in joined


# --- Playwright не импортируется на уровне модуля ----------------------------------


def test_browser_module_has_no_toplevel_playwright_import() -> None:
    """Импорт playwright — только отложенный, внутри функции запуска (spec §14)."""
    import kotbot.browser

    source = Path(str(kotbot.browser.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:  # только верхний уровень модуля
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("playwright") for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("playwright")


def test_importing_backend_does_not_import_playwright() -> None:
    import sys

    import kotbot.browser  # noqa: F401 — важен сам факт импорта без playwright

    assert "playwright" not in sys.modules
