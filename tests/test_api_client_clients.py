"""Клиенты в `bot/api_client`: список для привязки кабинета (respx-моки).

`GET /admin/clients` защищён `require_admin` (веб-сессия по cookie) — у бота
собственной веб-сессии нет, поэтому он подписывает одноразовый admin-session
токен тем же секретом, что и ядро (`services.admin_auth.generate_admin_session`,
тот же приём, что `bot/handlers/admin.py:generate_admin_link`), и кладёт его в
Cookie. Тесты здесь проверяют, что токен реально подписан правильным секретом и
операторским id, а не просто «какая-то строка».
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import respx
from bot import api_client
from bot.api_client import CoreUnavailable
from pydantic import SecretStr
from services.admin_auth import verify_admin_session

_CORE = "http://api:8000"
_SECRET = "test-secret-key"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bot.api_client.get_settings",
        lambda: SimpleNamespace(core_base_url=_CORE, secret_key=SecretStr(_SECRET)),
    )


def test_list_clients_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_CORE}/api/v1/admin/clients").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": 1,
                                "full_name": "Иван Петров",
                                "email": "ivan@example.com",
                                "phone": None,
                                "telegram": "@ivan",
                                "brief_count": 3,
                            }
                        ]
                    },
                )
            )
            items = await api_client.list_clients(555)
        assert len(items) == 1
        assert items[0].id == 1
        assert items[0].full_name == "Иван Петров"
        assert items[0].email == "ivan@example.com"
        assert items[0].brief_count == 3

    asyncio.run(scenario())


def test_list_clients_sends_a_signed_admin_cookie_for_the_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ядро видит `admin_session`, подписанный тем же секретом и operator_id,
    который передал вызывающий код — иначе `require_admin` ответит 401."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            route = router.get(f"{_CORE}/api/v1/admin/clients").mock(
                return_value=httpx.Response(200, json={"items": []})
            )
            await api_client.list_clients(555)
        sent_cookie = route.calls.last.request.headers["cookie"]
        assert "admin_session=" in sent_cookie
        token = sent_cookie.split("admin_session=", 1)[1].split(";", 1)[0]
        assert verify_admin_session(token, _SECRET) == 555

    asyncio.run(scenario())


def test_list_clients_empty_payload_is_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_CORE}/api/v1/admin/clients").mock(
                return_value=httpx.Response(200, json={"items": []})
            )
            items = await api_client.list_clients(555)
        assert items == []

    asyncio.run(scenario())


def test_list_clients_500_maps_to_core_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_CORE}/api/v1/admin/clients").mock(return_value=httpx.Response(500))
            with pytest.raises(CoreUnavailable):
                await api_client.list_clients(555)

    asyncio.run(scenario())


def test_list_clients_transport_error_maps_to_core_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_CORE}/api/v1/admin/clients").mock(side_effect=httpx.ConnectError("boom"))
            with pytest.raises(CoreUnavailable):
                await api_client.list_clients(555)

    asyncio.run(scenario())
