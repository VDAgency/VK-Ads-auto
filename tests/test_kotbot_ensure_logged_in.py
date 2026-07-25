"""`ensure_logged_in()` — 4 ступени перед каждым action (spec §4.2).

1. storage_state предпочтительной стратегии (порядок `strategy_order`);
2. протух → storage_state второй стратегии;
3. обе протухли → скриптовый релогин по сохранённым кредам (код/капча в
   неинтерактивном режиме → НЕ ретраим, `needs_reauth[strategy] = True`);
4. все ступени мимо → `ReauthRequired`.

Браузера нет: бэкенд фейковый, ответы задаются по стратегиям.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from kotbot.backend import LoginOutcome, SessionCheck
from kotbot.service import (
    AuthError,
    KotbotAutomation,
    ReauthRequired,
    parse_strategy_order,
)
from kotbot.store import CredentialStore, StateStore


class FakeBackend:
    """Бэкенд с настраиваемыми исходами проверки сессии и релогина по стратегиям."""

    def __init__(
        self,
        checks: dict[str, SessionCheck] | None = None,
        logins: dict[str, LoginOutcome] | None = None,
    ) -> None:
        self._checks = checks or {}
        self._logins = logins or {}
        self.check_calls: list[tuple[str, bytes]] = []
        self.login_calls: list[tuple[str, str, str]] = []
        self.closed: list[object] = []

    async def check_session(self, strategy: str, storage_state: bytes) -> SessionCheck:
        self.check_calls.append((strategy, storage_state))
        return self._checks.get(strategy, SessionCheck(logged_in=False))

    async def login(self, strategy: str, login: str, password: str) -> LoginOutcome:
        self.login_calls.append((strategy, login, password))
        return self._logins.get(strategy, LoginOutcome(status="error", error_code="login_failed"))

    async def submit_code(self, attempt: object, code: str) -> LoginOutcome:
        raise AssertionError("ensure_logged_in не должен спрашивать код")

    async def close_attempt(self, attempt: object) -> None:
        self.closed.append(attempt)


def _make(
    tmp_path: Path,
    backend: FakeBackend,
    *,
    order: tuple[str, ...] = ("email", "vk"),
    key: str | None = None,
) -> tuple[KotbotAutomation, CredentialStore, StateStore]:
    secret = Fernet.generate_key().decode("ascii") if key is None else key
    credentials = CredentialStore(secret, str(tmp_path))
    states = StateStore(secret, str(tmp_path))
    automation = KotbotAutomation(
        credentials=credentials,
        states=states,
        backend=backend,
        strategy_order=order,
    )
    return automation, credentials, states


# --- parse_strategy_order ---------------------------------------------------------


def test_parse_strategy_order_keeps_configured_priority() -> None:
    assert parse_strategy_order("vk,email") == ("vk", "email")


def test_parse_strategy_order_appends_missing_and_drops_unknown() -> None:
    assert parse_strategy_order("vk, telepathy") == ("vk", "email")


def test_parse_strategy_order_falls_back_to_default_when_empty() -> None:
    assert parse_strategy_order("") == ("email", "vk")


# --- ступень 1: storage_state предпочтительной стратегии ---------------------------


def test_stage1_uses_preferred_strategy_state(tmp_path: Path) -> None:
    backend = FakeBackend(
        checks={"email": SessionCheck(logged_in=True, storage_state=b"fresh-email")}
    )
    automation, _, states = _make(tmp_path, backend)
    states.save_raw("email", b"stored-email")
    states.save_raw("vk", b"stored-vk")

    assert asyncio.run(automation.ensure_logged_in()) == "email"
    # Проверяли только предпочтительную стратегию — вторую не трогали.
    assert backend.check_calls == [("email", b"stored-email")]
    # State-файл обновлён свежим состоянием.
    assert states.load_raw("email") == b"fresh-email"
    assert backend.login_calls == []


def test_stage1_respects_configured_order(tmp_path: Path) -> None:
    backend = FakeBackend(checks={"vk": SessionCheck(logged_in=True)})
    automation, _, states = _make(tmp_path, backend, order=("vk", "email"))
    states.save_raw("email", b"stored-email")
    states.save_raw("vk", b"stored-vk")

    assert asyncio.run(automation.ensure_logged_in()) == "vk"
    assert backend.check_calls == [("vk", b"stored-vk")]


def test_stage1_clears_needs_reauth_flag(tmp_path: Path) -> None:
    backend = FakeBackend(checks={"email": SessionCheck(logged_in=True)})
    automation, _, states = _make(tmp_path, backend)
    states.save_raw("email", b"stored-email")
    automation.mark_reauth_needed("email")

    assert asyncio.run(automation.ensure_logged_in()) == "email"
    strategies = automation.health()["strategies"]
    assert isinstance(strategies, dict)
    assert strategies["email"]["needs_reauth"] is False


# --- ступень 2: вторая стратегия --------------------------------------------------


def test_stage2_falls_back_to_second_strategy_state(tmp_path: Path) -> None:
    backend = FakeBackend(
        checks={
            "email": SessionCheck(logged_in=False),
            "vk": SessionCheck(logged_in=True, storage_state=b"fresh-vk"),
        }
    )
    automation, _, states = _make(tmp_path, backend)
    states.save_raw("email", b"stored-email")
    states.save_raw("vk", b"stored-vk")

    assert asyncio.run(automation.ensure_logged_in()) == "vk"
    assert backend.check_calls == [("email", b"stored-email"), ("vk", b"stored-vk")]
    assert states.load_raw("vk") == b"fresh-vk"
    assert backend.login_calls == []


def test_stage2_skips_strategy_without_saved_state(tmp_path: Path) -> None:
    backend = FakeBackend(checks={"vk": SessionCheck(logged_in=True)})
    automation, _, states = _make(tmp_path, backend)
    states.save_raw("vk", b"stored-vk")

    assert asyncio.run(automation.ensure_logged_in()) == "vk"
    assert backend.check_calls == [("vk", b"stored-vk")]


# --- ступень 3: скриптовый релогин ------------------------------------------------


def test_stage3_relogins_with_saved_credentials(tmp_path: Path) -> None:
    backend = FakeBackend(
        checks={"email": SessionCheck(logged_in=False), "vk": SessionCheck(logged_in=False)},
        logins={"vk": LoginOutcome(status="ok", storage_state=b"vk-state")},
    )
    automation, credentials, states = _make(tmp_path, backend)
    states.save_raw("email", b"stored-email")
    states.save_raw("vk", b"stored-vk")
    credentials.save("vk", "+79990000000", "secret")

    assert asyncio.run(automation.ensure_logged_in()) == "vk"
    assert backend.login_calls == [("vk", "+79990000000", "secret")]
    assert states.load_raw("vk") == b"vk-state"


def test_stage3_tries_strategies_in_configured_order(tmp_path: Path) -> None:
    backend = FakeBackend(
        logins={
            "email": LoginOutcome(status="error", error_code="invalid_credentials"),
            "vk": LoginOutcome(status="ok", storage_state=b"vk-state"),
        }
    )
    automation, credentials, _ = _make(tmp_path, backend)
    credentials.save("email", "user@example.com", "secret")
    credentials.save("vk", "+79990000000", "secret")

    assert asyncio.run(automation.ensure_logged_in()) == "vk"
    assert [call[0] for call in backend.login_calls] == ["email", "vk"]


def test_stage3_does_not_retry_when_code_is_required(tmp_path: Path) -> None:
    # Неинтерактивный режим: код спросить некому → НЕ ретраим (защита от блокировки).
    backend = FakeBackend(
        logins={
            "email": LoginOutcome(status="code_required", attempt="parked-flow"),
            "vk": LoginOutcome(status="error", error_code="captcha_required"),
        }
    )
    automation, credentials, _ = _make(tmp_path, backend)
    credentials.save("email", "user@example.com", "secret")
    credentials.save("vk", "+79990000000", "secret")

    with pytest.raises(ReauthRequired):
        asyncio.run(automation.ensure_logged_in())
    assert [call[0] for call in backend.login_calls] == ["email", "vk"]
    # Припаркованный флоу освобождён, флаги взведены.
    assert backend.closed == ["parked-flow"]
    strategies = automation.health()["strategies"]
    assert isinstance(strategies, dict)
    assert strategies["email"]["needs_reauth"] is True
    assert strategies["vk"]["needs_reauth"] is True
    assert automation.health()["healthy"] is False


def test_stage3_skips_strategy_flagged_for_reauth(tmp_path: Path) -> None:
    backend = FakeBackend(logins={"vk": LoginOutcome(status="ok", storage_state=b"vk-state")})
    automation, credentials, _ = _make(tmp_path, backend)
    credentials.save("email", "user@example.com", "secret")
    credentials.save("vk", "+79990000000", "secret")
    automation.mark_reauth_needed("email")

    assert asyncio.run(automation.ensure_logged_in()) == "vk"
    assert [call[0] for call in backend.login_calls] == ["vk"]


def test_stage3_skips_strategy_without_credentials(tmp_path: Path) -> None:
    backend = FakeBackend(logins={"vk": LoginOutcome(status="ok")})
    automation, credentials, _ = _make(tmp_path, backend)
    credentials.save("vk", "+79990000000", "secret")

    assert asyncio.run(automation.ensure_logged_in()) == "vk"
    assert [call[0] for call in backend.login_calls] == ["vk"]


# --- ступень 4: всё мимо ----------------------------------------------------------


def test_stage4_raises_reauth_required_without_state_and_credentials(tmp_path: Path) -> None:
    backend = FakeBackend()
    automation, _, _ = _make(tmp_path, backend)

    with pytest.raises(ReauthRequired):
        asyncio.run(automation.ensure_logged_in())
    assert backend.check_calls == []
    assert backend.login_calls == []


def test_stage4_raises_after_all_stages_fail(tmp_path: Path) -> None:
    backend = FakeBackend(
        checks={"email": SessionCheck(logged_in=False), "vk": SessionCheck(logged_in=False)},
        logins={
            "email": LoginOutcome(status="error", error_code="invalid_credentials"),
            "vk": LoginOutcome(status="error", error_code="login_failed"),
        },
    )
    automation, credentials, states = _make(tmp_path, backend)
    states.save_raw("email", b"stored-email")
    states.save_raw("vk", b"stored-vk")
    credentials.save("email", "user@example.com", "secret")
    credentials.save("vk", "+79990000000", "secret")

    with pytest.raises(ReauthRequired):
        asyncio.run(automation.ensure_logged_in())
    assert len(backend.check_calls) == 2
    assert len(backend.login_calls) == 2


def test_ensure_logged_in_requires_configured_service(tmp_path: Path) -> None:
    backend = FakeBackend()
    automation, _, _ = _make(tmp_path, backend, key="")

    with pytest.raises(AuthError) as exc:
        asyncio.run(automation.ensure_logged_in())
    assert exc.value.code == "not_configured"
