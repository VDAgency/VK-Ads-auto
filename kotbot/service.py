"""KotbotAutomation — state machine авторизации kotbot (spec §4, §4.1).

Держит хранилища (креды + storage_state), браузерный бэкенд и pending-попытки
входа с TTL 300с: браузер «паркуется» на странице челленджа, оператор вводит код
в Telegram, бот доносит его через `POST /auth/code`. Флаги `needs_reauth`
взводятся, когда неинтерактивный релогин невозможен (`ensure_logged_in`,
ступень 3), и сбрасываются успешной авторизацией.

Ошибки — `AuthError(code)` (как в userbot): API-слой превращает их в 400.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from kotbot.backend import AutomationBackend, LoginOutcome
from kotbot.store import CredentialStore, StateStore

logger = logging.getLogger(__name__)

# Стратегии входа на kotbot.ru (spec §1: почта+пароль и VK-аккаунт).
STRATEGIES = ("email", "vk")

# Сколько живёт припаркованный браузерный флоу в ожидании кода (spec §4.1).
_ATTEMPT_TTL_SECONDS = 300.0


class AuthError(Exception):
    """Ошибка авторизации; `code` — короткий машинный код для API-ответа."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class ReauthRequired(Exception):
    """Все ступени `ensure_logged_in` мимо: нужна ручная авторизация оператором.

    API-слой отвечает на неё 409 `{detail: "reauth_required"}` (spec §4.2 п.4).
    """


def parse_strategy_order(raw: str) -> tuple[str, ...]:
    """Разобрать `KOTBOT_STRATEGY_ORDER` в порядок стратегий для `ensure_logged_in`.

    Неизвестные значения отбрасываем, пропущенные дописываем в каноничном порядке —
    так опечатка в env не отключает стратегию молча.
    """
    configured = [item.strip() for item in raw.split(",")]
    ordered = [item for item in configured if item in STRATEGIES]
    ordered += [item for item in STRATEGIES if item not in ordered]
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Успешный итог шага авторизации (ошибки идут исключением `AuthError`)."""

    status: str  # "ok" | "code_required"
    attempt_id: str | None = None
    hint: str = ""


@dataclass(slots=True)
class _PendingAttempt:
    """Припаркованный флоу входа: ждём код от оператора до дедлайна."""

    attempt: object | None
    deadline: float
    strategy: str
    login: str
    password: str


class KotbotAutomation:
    """Фасад авторизации: хранилища + бэкенд + pending-попытки + needs_reauth."""

    def __init__(
        self,
        credentials: CredentialStore,
        states: StateStore,
        backend: AutomationBackend,
        *,
        clock: Callable[[], float] = time.monotonic,
        strategy_order: Sequence[str] = STRATEGIES,
    ) -> None:
        self._credentials = credentials
        self._states = states
        self._backend = backend
        self._clock = clock
        self._strategy_order = tuple(strategy_order)
        self._pending: dict[str, _PendingAttempt] = {}
        self._needs_reauth: dict[str, bool] = {}

    async def auth_start(self, strategy: str, login: str, password: str) -> AuthResult:
        """Начать вход стратегией: ok | code_required(attempt_id) | AuthError."""
        if strategy not in STRATEGIES:
            raise AuthError("invalid_strategy")
        self._require_configured()
        outcome = await self._backend.login(strategy, login, password)
        if outcome.status == "ok":
            self._persist_success(strategy, login, password, outcome)
            return AuthResult(status="ok")
        if outcome.status == "code_required":
            attempt_id = secrets.token_urlsafe(16)
            self._pending[attempt_id] = _PendingAttempt(
                attempt=outcome.attempt,
                deadline=self._clock() + _ATTEMPT_TTL_SECONDS,
                strategy=strategy,
                login=login,
                password=password,
            )
            return AuthResult(status="code_required", attempt_id=attempt_id, hint=outcome.hint)
        raise AuthError(outcome.error_code or "login_failed")

    async def auth_code(self, attempt_id: str, code: str) -> AuthResult:
        """Донести код подтверждения до припаркованного флоу."""
        self._require_configured()
        await self._prune_expired()
        pending = self._pending.get(attempt_id)
        if pending is None:
            # Неизвестный или уже вычищенный по TTL attempt — для оператора одно и то же.
            raise AuthError("attempt_expired")
        outcome = await self._backend.submit_code(pending.attempt, code)
        if outcome.status == "ok":
            self._pending.pop(attempt_id, None)
            self._persist_success(pending.strategy, pending.login, pending.password, outcome)
            return AuthResult(status="ok")
        error_code = outcome.error_code or "code_invalid"
        if error_code != "code_invalid":
            # Невосстановимая ошибка — освобождаем браузерный флоу.
            self._pending.pop(attempt_id, None)
            await self._backend.close_attempt(pending.attempt)
        # code_invalid — оставляем попытку живой: оператор может ввести код ещё раз.
        raise AuthError(error_code)

    async def ensure_logged_in(self) -> str:
        """Гарантировать живую сессию перед action; вернуть рабочую стратегию.

        Четыре ступени (spec §4.2): storage_state предпочтительной стратегии →
        storage_state второй → скриптовый релогин по сохранённым кредам →
        `ReauthRequired`. Порядок стратегий берётся из `strategy_order`.
        """
        self._require_configured()
        working = await self._try_saved_states()
        if working is not None:
            return working
        working = await self._try_scripted_relogin()
        if working is not None:
            return working
        logger.warning("kotbot: все ступени ensure_logged_in мимо — нужна ручная авторизация")
        raise ReauthRequired("reauth_required")

    async def _try_saved_states(self) -> str | None:
        """Ступени 1–2: проверить сохранённые storage_state по порядку стратегий.

        Флаг `needs_reauth` здесь не смотрим: живой state авторитетнее флага и
        сбрасывает его. Успех продлевает сессию — перезаписываем state-файл.
        """
        for strategy in self._strategy_order:
            state = self._states.load_raw(strategy)
            if state is None:
                continue
            check = await self._backend.check_session(strategy, state)
            if not check.logged_in:
                continue
            if check.storage_state is not None:
                self._states.save_raw(strategy, check.storage_state)
            self._needs_reauth[strategy] = False
            logger.info("kotbot: сессия стратегии %s жива", strategy)
            return strategy
        return None

    async def _try_scripted_relogin(self) -> str | None:
        """Ступень 3: неинтерактивный релогин по сохранённым кредам.

        Запрос кода или капча означают, что без оператора не пройти: НЕ ретраим
        (защита от блокировки аккаунта), взводим `needs_reauth[strategy]`.
        """
        for strategy in self._strategy_order:
            if self._needs_reauth.get(strategy, False):
                continue
            credentials = self._credentials.load(strategy)
            if credentials is None:
                continue
            login, password = credentials
            outcome = await self._backend.login(strategy, login, password)
            if outcome.status == "ok":
                self._persist_success(strategy, login, password, outcome)
                logger.info("kotbot: скриптовый релогин стратегией %s удался", strategy)
                return strategy
            if outcome.status == "code_required":
                # Кода спросить некому — освобождаем припаркованный флоу.
                await self._backend.close_attempt(outcome.attempt)
            logger.warning(
                "kotbot: релогин стратегией %s невозможен неинтерактивно (%s)",
                strategy,
                outcome.error_code or outcome.status,
            )
            self.mark_reauth_needed(strategy)
        return None

    def health(self) -> dict[str, object]:
        """Состояние стратегий (spec §4.1): дёшево — файлы + кеш-флаги, без браузера."""
        strategies: dict[str, dict[str, bool]] = {}
        for strategy in STRATEGIES:
            strategies[strategy] = {
                "has_credentials": self._credentials.has(strategy),
                "has_state": self._states.has(strategy),
                "needs_reauth": self._needs_reauth.get(strategy, False),
            }
        healthy = any(
            flags["has_state"] and not flags["needs_reauth"] for flags in strategies.values()
        )
        return {"healthy": healthy, "strategies": strategies}

    async def shutdown(self) -> None:
        """Освободить ресурсы бэкенда на остановке сервиса (закрыть браузер).

        Бэкенд без `close` (заглушка, фейк в тестах) — ничего не делаем.
        """
        close = getattr(self._backend, "close", None)
        if close is not None:
            await close()

    def mark_reauth_needed(self, strategy: str) -> None:
        """Взвести флаг «нужна повторная авторизация» (ensure_logged_in, K-PR3)."""
        self._needs_reauth[strategy] = True

    def _persist_success(
        self, strategy: str, login: str, password: str, outcome: LoginOutcome
    ) -> None:
        """Успешный вход: сохранить креды и storage_state (если бэкенд его вернул).

        `NullBackend` каркаса storage_state не отдаёт — файл состояния появится
        с реальным Playwright-бэкендом (K-PR3).
        """
        self._credentials.save(strategy, login, password)
        if outcome.storage_state is not None:
            self._states.save_raw(strategy, outcome.storage_state)
        self._needs_reauth[strategy] = False

    async def _prune_expired(self) -> None:
        """Вычистить просроченные попытки и освободить их браузерные флоу."""
        now = self._clock()
        expired = [key for key, pending in self._pending.items() if pending.deadline <= now]
        for key in expired:
            pending = self._pending.pop(key)
            await self._backend.close_attempt(pending.attempt)

    def _require_configured(self) -> None:
        """Пустой KOTBOT_SECRET_KEY → 400 `not_configured` (spec §4)."""
        if not (self._credentials.configured and self._states.configured):
            raise AuthError("not_configured")
