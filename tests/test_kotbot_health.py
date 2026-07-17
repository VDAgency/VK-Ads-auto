"""Health kotbot: флаги стратегий и агрегат healthy (spec §4.1, K-PR1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from kotbot.backend import NullBackend
from kotbot.main import create_app
from kotbot.service import KotbotAutomation
from kotbot.store import CredentialStore, StateStore


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def _automation(tmp_path: Path, key: str | None = None) -> KotbotAutomation:
    secret = _key() if key is None else key
    return KotbotAutomation(
        credentials=CredentialStore(secret, str(tmp_path)),
        states=StateStore(secret, str(tmp_path)),
        backend=NullBackend(),
    )


def _strategies(automation: KotbotAutomation) -> dict[str, Any]:
    strategies = automation.health()["strategies"]
    assert isinstance(strategies, dict)
    return strategies


def test_fresh_service_is_unhealthy_with_all_flags_false(tmp_path: Path) -> None:
    automation = _automation(tmp_path)
    health = automation.health()

    assert health["healthy"] is False
    for strategy in ("email", "vk"):
        flags = _strategies(automation)[strategy]
        assert flags == {"has_credentials": False, "has_state": False, "needs_reauth": False}


def test_credentials_alone_do_not_make_healthy(tmp_path: Path) -> None:
    key = _key()
    CredentialStore(key, str(tmp_path)).save("email", "ops@example.com", "p")
    automation = _automation(tmp_path, key=key)

    flags = _strategies(automation)["email"]
    assert flags["has_credentials"] is True
    assert flags["has_state"] is False
    # healthy требует storage_state, кредов недостаточно.
    assert automation.health()["healthy"] is False


def test_state_makes_strategy_healthy(tmp_path: Path) -> None:
    key = _key()
    StateStore(key, str(tmp_path)).save_raw("email", b"{}")
    automation = _automation(tmp_path, key=key)

    assert _strategies(automation)["email"]["has_state"] is True
    assert automation.health()["healthy"] is True


def test_needs_reauth_disables_strategy(tmp_path: Path) -> None:
    key = _key()
    StateStore(key, str(tmp_path)).save_raw("email", b"{}")
    automation = _automation(tmp_path, key=key)
    automation.mark_reauth_needed("email")

    flags = _strategies(automation)["email"]
    assert flags["needs_reauth"] is True
    # Единственная стратегия со state требует reauth → канал нездоров.
    assert automation.health()["healthy"] is False


def test_second_strategy_keeps_channel_healthy(tmp_path: Path) -> None:
    key = _key()
    store = StateStore(key, str(tmp_path))
    store.save_raw("email", b"{}")
    store.save_raw("vk", b"{}")
    automation = _automation(tmp_path, key=key)
    automation.mark_reauth_needed("email")

    # Протухла одна стратегия, вторая жива → healthy (фолбэк, spec §1).
    assert automation.health()["healthy"] is True


def test_unconfigured_service_health_is_calm_false(tmp_path: Path) -> None:
    # Пустой ключ: /health работает и честно говорит «ничего нет», не бросая.
    automation = _automation(tmp_path, key="")
    health = automation.health()
    assert health["healthy"] is False
    flags = _strategies(automation)["email"]
    assert flags == {"has_credentials": False, "has_state": False, "needs_reauth": False}


def test_health_endpoint_shape(tmp_path: Path) -> None:
    key = _key()
    StateStore(key, str(tmp_path)).save_raw("vk", b"{}")
    CredentialStore(key, str(tmp_path)).save("vk", "+7999", "p")
    automation = _automation(tmp_path, key=key)

    app = create_app()
    app.state.automation = automation
    with TestClient(app) as tc:
        resp = tc.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {
        "healthy": True,
        "strategies": {
            "email": {"has_credentials": False, "has_state": False, "needs_reauth": False},
            "vk": {"has_credentials": True, "has_state": True, "needs_reauth": False},
        },
    }
