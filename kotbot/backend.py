"""Протокол браузерного бэкенда автоматизации kotbot (spec §3, §4).

`KotbotAutomation` (service.py) не знает про Playwright: вся работа с браузером
скрыта за `AutomationBackend`. В K-PR1 существует только `NullBackend`-заглушка;
реальный Playwright-бэкенд (browser.py + flows.py) придёт в K-PR3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """Результат шага логина/ввода кода в браузере.

    - `status` — "ok" | "code_required" | "error";
    - `error_code` — машинный код при `status == "error"` (spec §4.1);
    - `attempt` — непрозрачный объект припаркованного браузерного флоу
      (страница челленджа), передаётся обратно в `submit_code`;
    - `hint` — подсказка оператору при `code_required` (куда пришёл код);
    - `storage_state` — сериализованный Playwright storage_state при успехе:
      сервис сохраняет его через `StateStore`, не заглядывая внутрь (K-PR3).
    """

    status: str
    error_code: str | None = None
    attempt: object | None = None
    hint: str = ""
    storage_state: bytes | None = None


class AutomationBackend(Protocol):
    """Контракт браузерной автоматизации входа на kotbot.ru."""

    async def login(self, strategy: str, login: str, password: str) -> LoginOutcome:
        """Выполнить вход стратегией (`email`/`vk`); может запросить код."""
        ...

    async def submit_code(self, attempt: object, code: str) -> LoginOutcome:
        """Ввести код подтверждения в припаркованный флоу `attempt`."""
        ...

    async def close_attempt(self, attempt: object) -> None:
        """Закрыть припаркованный флоу (истёк TTL / отмена) — освободить браузер."""
        ...


class NullBackend:
    """Заглушка до K-PR3: браузерной автоматизации ещё нет.

    Любая попытка входа отвечает `not_implemented` — бот показывает оператору
    честную подсказку, что автоматизация ещё не выкачена.
    """

    async def login(self, strategy: str, login: str, password: str) -> LoginOutcome:
        return LoginOutcome(status="error", error_code="not_implemented")

    async def submit_code(self, attempt: object, code: str) -> LoginOutcome:
        return LoginOutcome(status="error", error_code="not_implemented")

    async def close_attempt(self, attempt: object) -> None:
        return None
