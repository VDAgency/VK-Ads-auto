"""Auth-API kotbot: контракт /auth/start, /auth/code, actions→501 (spec §4.1, §4.3).

Автоматизация собирается с фейковым бэкендом (без Playwright и сети) и кладётся
прямо в `app.state` — lifespan её не перезаписывает (как в test_userbot_api).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from kotbot.backend import LoginOutcome
from kotbot.main import create_app
from kotbot.service import KotbotAutomation
from kotbot.store import CredentialStore, StateStore


class _FakeBackend:
    """Управляемый бэкенд: исходы задаются в конструкторе, вызовы записываются."""

    def __init__(
        self,
        login_outcome: LoginOutcome | None = None,
        code_outcome: LoginOutcome | None = None,
    ) -> None:
        self.login_calls: list[tuple[str, str, str]] = []
        self.code_calls: list[tuple[object, str]] = []
        self.closed: list[object] = []
        self._login_outcome = login_outcome or LoginOutcome(status="ok")
        self._code_outcome = code_outcome or LoginOutcome(status="ok")

    async def login(self, strategy: str, login: str, password: str) -> LoginOutcome:
        self.login_calls.append((strategy, login, password))
        return self._login_outcome

    async def submit_code(self, attempt: object, code: str) -> LoginOutcome:
        self.code_calls.append((attempt, code))
        return self._code_outcome

    async def close_attempt(self, attempt: object) -> None:
        self.closed.append(attempt)


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def _make_client(
    tmp_path: Path,
    backend: Any,
    *,
    key: str | None = None,
    clock: Any = time.monotonic,
) -> tuple[TestClient, KotbotAutomation]:
    secret = _key() if key is None else key
    automation = KotbotAutomation(
        credentials=CredentialStore(secret, str(tmp_path)),
        states=StateStore(secret, str(tmp_path)),
        backend=backend,
        clock=clock,
    )
    app = create_app()
    app.state.automation = automation
    return TestClient(app), automation


def _start(tc: TestClient, strategy: str = "email") -> Any:
    return tc.post(
        "/auth/start",
        json={"strategy": strategy, "login": "ops@example.com", "password": "p@ss"},
    )


# --- /auth/start ---------------------------------------------------------------


def test_start_ok_saves_credentials_and_state(tmp_path: Path) -> None:
    backend = _FakeBackend(
        login_outcome=LoginOutcome(status="ok", storage_state=b'{"cookies": []}')
    )
    tc, automation = _make_client(tmp_path, backend)
    with tc:
        resp = _start(tc)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert backend.login_calls == [("email", "ops@example.com", "p@ss")]
    # Креды и storage_state сохранены → стратегия здорова.
    health = automation.health()
    strategies = health["strategies"]
    assert isinstance(strategies, dict)
    assert strategies["email"]["has_credentials"] is True
    assert strategies["email"]["has_state"] is True
    assert health["healthy"] is True


def test_start_ok_without_storage_state_keeps_has_state_false(tmp_path: Path) -> None:
    # Каркасный бэкенд может не отдать storage_state — отметки has_state нет.
    tc, automation = _make_client(tmp_path, _FakeBackend(LoginOutcome(status="ok")))
    with tc:
        resp = _start(tc)
    assert resp.status_code == 200
    strategies = automation.health()["strategies"]
    assert isinstance(strategies, dict)
    assert strategies["email"]["has_credentials"] is True
    assert strategies["email"]["has_state"] is False


def test_start_code_required_returns_attempt_id_and_hint(tmp_path: Path) -> None:
    attempt = object()
    backend = _FakeBackend(
        login_outcome=LoginOutcome(status="code_required", attempt=attempt, hint="код на почте")
    )
    tc, _ = _make_client(tmp_path, backend)
    with tc:
        resp = _start(tc)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "code_required"
    assert payload["attempt_id"]
    assert payload["hint"] == "код на почте"


def test_start_invalid_credentials_returns_400(tmp_path: Path) -> None:
    backend = _FakeBackend(
        login_outcome=LoginOutcome(status="error", error_code="invalid_credentials")
    )
    tc, _ = _make_client(tmp_path, backend)
    with tc:
        resp = _start(tc)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_credentials"


def test_start_unknown_strategy_returns_400(tmp_path: Path) -> None:
    tc, _ = _make_client(tmp_path, _FakeBackend())
    with tc:
        resp = _start(tc, strategy="telegram")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_strategy"


def test_null_backend_reports_not_implemented(tmp_path: Path) -> None:
    from kotbot.backend import NullBackend

    tc, _ = _make_client(tmp_path, NullBackend())
    with tc:
        resp = _start(tc)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not_implemented"


# --- /auth/code ------------------------------------------------------------------


def test_code_ok_persists_credentials(tmp_path: Path) -> None:
    backend = _FakeBackend(
        login_outcome=LoginOutcome(status="code_required", attempt=object()),
        code_outcome=LoginOutcome(status="ok", storage_state=b'{"cookies": []}'),
    )
    tc, automation = _make_client(tmp_path, backend)
    with tc:
        attempt_id = _start(tc).json()["attempt_id"]
        # До кода креды не сохраняются — вход ещё не подтверждён.
        strategies = automation.health()["strategies"]
        assert isinstance(strategies, dict)
        assert strategies["email"]["has_credentials"] is False

        resp = tc.post("/auth/code", json={"attempt_id": attempt_id, "code": "123456"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert backend.code_calls[0][1] == "123456"
    assert automation.health()["healthy"] is True


def test_code_invalid_returns_400_and_allows_retry(tmp_path: Path) -> None:
    backend = _FakeBackend(
        login_outcome=LoginOutcome(status="code_required", attempt=object()),
        code_outcome=LoginOutcome(status="error", error_code="code_invalid"),
    )
    tc, _ = _make_client(tmp_path, backend)
    with tc:
        attempt_id = _start(tc).json()["attempt_id"]
        first = tc.post("/auth/code", json={"attempt_id": attempt_id, "code": "000000"})
        assert first.status_code == 400
        assert first.json()["detail"] == "code_invalid"

        # Попытка не закрыта: тот же attempt_id принимает повторный код.
        backend._code_outcome = LoginOutcome(status="ok")
        second = tc.post("/auth/code", json={"attempt_id": attempt_id, "code": "111111"})
    assert second.status_code == 200


def test_code_unknown_attempt_returns_attempt_expired(tmp_path: Path) -> None:
    tc, _ = _make_client(tmp_path, _FakeBackend())
    with tc:
        resp = tc.post("/auth/code", json={"attempt_id": "no-such", "code": "1"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "attempt_expired"


def test_code_after_ttl_expires_attempt_and_frees_browser(tmp_path: Path) -> None:
    now = [1000.0]
    attempt = object()
    backend = _FakeBackend(login_outcome=LoginOutcome(status="code_required", attempt=attempt))
    tc, _ = _make_client(tmp_path, backend, clock=lambda: now[0])
    with tc:
        attempt_id = _start(tc).json()["attempt_id"]
        now[0] += 301.0  # TTL попытки — 300с (spec §4.1)
        resp = tc.post("/auth/code", json={"attempt_id": attempt_id, "code": "123"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "attempt_expired"
    # Просроченный браузерный флоу освобождён.
    assert backend.closed == [attempt]
    # Код в браузер не отправлялся.
    assert backend.code_calls == []


# --- Пустой ключ → not_configured -------------------------------------------------


@pytest.mark.parametrize(
    "path,body",
    [
        ("/auth/start", {"strategy": "email", "login": "l", "password": "p"}),
        ("/auth/code", {"attempt_id": "x", "code": "1"}),
    ],
)
def test_empty_secret_key_returns_not_configured(
    tmp_path: Path, path: str, body: dict[str, str]
) -> None:
    tc, _ = _make_client(tmp_path, _FakeBackend(), key="")
    with tc:
        resp = tc.post(path, json=body)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not_configured"


# --- Action-роуты (K-PR3) отвечают 501 --------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/cabinets"),
        ("POST", "/campaigns"),
        ("POST", "/campaigns/ext-1/creative"),
        ("POST", "/campaigns/ext-1/launch"),
        ("POST", "/campaigns/ext-1/stop"),
        ("GET", "/campaigns/ext-1/stats"),
        ("GET", "/campaigns/ext-1/status"),
        ("POST", "/session/validate"),
    ],
)
def test_action_routes_respond_501(tmp_path: Path, method: str, path: str) -> None:
    tc, _ = _make_client(tmp_path, _FakeBackend())
    with tc:
        resp = tc.request(method, path)
    assert resp.status_code == 501
    assert resp.json()["detail"] == "not_implemented"
