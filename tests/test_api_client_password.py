"""Пароль оператора в `bot/api_client` (respx-моки): `POST /admin/password`.

Тот же приём авторизации, что и `list_clients` (`tests/test_api_client_clients.py`):
своей веб-сессии у бота нет, поэтому запрос подписывает одноразовый admin-cookie
секретом, общим с ядром. 422 с уже человекочитаемой причиной ядра
(`core/api/v1/admin.py::set_password`) должен дойти до оператора как есть, а не
как машинный код.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import respx
from bot import api_client
from bot.api_client import CoreUnavailable, WeakPassword
from pydantic import SecretStr
from services.admin_auth import verify_admin_session

_CORE = "http://api:8000"
_SECRET = "test-secret-key"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bot.api_client.get_settings",
        lambda: SimpleNamespace(core_base_url=_CORE, secret_key=SecretStr(_SECRET)),
    )


def test_set_operator_password_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/admin/password").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            await api_client.set_operator_password(555, "a-strong-password")

    asyncio.run(scenario())


def test_set_operator_password_sends_a_signed_admin_cookie_for_the_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(f"{_CORE}/api/v1/admin/password").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            await api_client.set_operator_password(555, "a-strong-password")
        sent_cookie = route.calls.last.request.headers["cookie"]
        assert "admin_session=" in sent_cookie
        token = sent_cookie.split("admin_session=", 1)[1].split(";", 1)[0]
        assert verify_admin_session(token, _SECRET) == 555

    asyncio.run(scenario())


def test_set_operator_password_sends_password_in_body_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(f"{_CORE}/api/v1/admin/password").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            await api_client.set_operator_password(555, "a-strong-password")
        assert route.calls.last.request.content == b'{"password":"a-strong-password"}'

    asyncio.run(scenario())


def test_set_operator_password_422_carries_the_core_human_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            reason = "Пароль короче десяти символов — так его слишком просто подобрать"
            router.post(f"{_CORE}/api/v1/admin/password").mock(
                return_value=httpx.Response(422, json={"detail": reason})
            )
            with pytest.raises(WeakPassword) as exc_info:
                await api_client.set_operator_password(555, "short")
        assert exc_info.value.reason == (
            "Пароль короче десяти символов — так его слишком просто подобрать"
        )

    asyncio.run(scenario())


def test_set_operator_password_422_without_json_body_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/admin/password").mock(
                return_value=httpx.Response(422, content=b"not json")
            )
            with pytest.raises(WeakPassword) as exc_info:
                await api_client.set_operator_password(555, "short")
        assert "10" in exc_info.value.reason

    asyncio.run(scenario())


def test_set_operator_password_500_maps_to_core_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/admin/password").mock(return_value=httpx.Response(500))
            with pytest.raises(CoreUnavailable):
                await api_client.set_operator_password(555, "a-strong-password")

    asyncio.run(scenario())


def test_set_operator_password_401_maps_to_core_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """require_admin отверг подпись — со стороны бота это тоже «сервис не сработал»,

    не забота вызывающего хендлера различать причину.
    """
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/admin/password").mock(return_value=httpx.Response(401))
            with pytest.raises(CoreUnavailable):
                await api_client.set_operator_password(555, "a-strong-password")

    asyncio.run(scenario())


def test_set_operator_password_transport_error_maps_to_core_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/admin/password").mock(
                side_effect=httpx.ConnectError("boom")
            )
            with pytest.raises(CoreUnavailable):
                await api_client.set_operator_password(555, "a-strong-password")

    asyncio.run(scenario())
