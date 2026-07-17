"""Kotbot-секция bot/api_client: маппинг ошибок и парсинг ответов (respx-моки)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from bot import api_client
from bot.api_client import KotbotAuthError, KotbotUnavailable

_URL = "http://kotbot:8002"


def _configure(monkeypatch: pytest.MonkeyPatch, url: str = _URL) -> None:
    monkeypatch.setattr("bot.api_client.get_settings", lambda: SimpleNamespace(kotbot_base_url=url))


# --- kotbot_configured ------------------------------------------------------------


def test_configured_false_when_base_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "")
    assert api_client.kotbot_configured() is False


def test_configured_true_when_base_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    assert api_client.kotbot_configured() is True


def test_empty_base_url_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "")
    with pytest.raises(KotbotUnavailable):
        asyncio.run(api_client.kotbot_status())


# --- Маппинг ошибок ---------------------------------------------------------------


def test_400_maps_to_auth_error_with_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/auth/start").mock(
                return_value=httpx.Response(400, json={"detail": "invalid_credentials"})
            )
            with pytest.raises(KotbotAuthError) as exc:
                await api_client.kotbot_start_auth("email", "ops@example.com", "p")
        assert exc.value.code == "invalid_credentials"

    asyncio.run(scenario())


def test_5xx_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(return_value=httpx.Response(500))
            with pytest.raises(KotbotUnavailable):
                await api_client.kotbot_status()

    asyncio.run(scenario())


def test_transport_error_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/auth/code").mock(side_effect=httpx.ConnectError("boom"))
            with pytest.raises(KotbotUnavailable):
                await api_client.kotbot_submit_code("att-1", "123")

    asyncio.run(scenario())


# --- Парсинг ответов ----------------------------------------------------------------


def test_start_auth_ok_and_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(f"{_URL}/auth/start").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            result = await api_client.kotbot_start_auth("email", "ops@example.com", "p@ss")
        assert result.status == "ok"
        assert result.attempt_id is None
        body = json.loads(route.calls.last.request.content)
        assert body == {"strategy": "email", "login": "ops@example.com", "password": "p@ss"}

    asyncio.run(scenario())


def test_start_auth_code_required_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/auth/start").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "code_required",
                        "attempt_id": "att-1",
                        "hint": "код на почте",
                    },
                )
            )
            result = await api_client.kotbot_start_auth("email", "l", "p")
        assert result.status == "code_required"
        assert result.attempt_id == "att-1"
        assert result.hint == "код на почте"

    asyncio.run(scenario())


def test_submit_code_posts_attempt_and_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(f"{_URL}/auth/code").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            await api_client.kotbot_submit_code("att-1", "654321")
        body = json.loads(route.calls.last.request.content)
        assert body == {"attempt_id": "att-1", "code": "654321"}

    asyncio.run(scenario())


def test_status_parses_health_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "healthy": True,
                        "strategies": {
                            "email": {
                                "has_credentials": True,
                                "has_state": True,
                                "needs_reauth": False,
                            },
                            "vk": {
                                "has_credentials": False,
                                "has_state": False,
                                "needs_reauth": True,
                            },
                        },
                    },
                )
            )
            health = await api_client.kotbot_status()
        assert health.healthy is True
        assert health.email.has_state is True
        assert health.vk.needs_reauth is True
        assert health.vk.has_credentials is False

    asyncio.run(scenario())


def test_status_tolerates_missing_strategies(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(
                return_value=httpx.Response(200, json={"healthy": False})
            )
            health = await api_client.kotbot_status()
        assert health.healthy is False
        assert health.email.has_state is False

    asyncio.run(scenario())
