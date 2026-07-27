"""Опознание кабинета VK по токену (spec 2026-07-27 §8.2).

Формы ответов взяты из живых запросов 2026-07-27, а не выдуманы.
"""

import asyncio

import httpx
import pytest
import respx
from services.vk_identity import (
    VK_API_BASE,
    InvalidTokenError,
    VkUnreachableError,
    fetch_balance,
    fetch_identity,
)

USER_URL = f"{VK_API_BASE}/user.json"
ACCOUNT_URL = f"{VK_API_BASE}/user/account.json"

# Сокращённый живой ответ VK (2026-07-27).
LIVE_USER = {
    "id": 10000001,
    "username": "a1b2c3d4e5@agency_client",
    "firstname": "",
    "lastname": "",
    "status": "active",
    "additional_info": {
        "name": "",
        "client_name": "Студия «Пример»",
        "client_info": "DEMO0000",
    },
}


@respx.mock
def test_identity_reads_name_id_and_username() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(200, json=LIVE_USER))
    identity = asyncio.run(fetch_identity("token"))
    assert identity.external_id == "10000001"
    assert identity.username == "a1b2c3d4e5@agency_client"
    assert identity.title == "Студия «Пример»"
    assert identity.status == "active"


@respx.mock
def test_identity_sends_bearer_token() -> None:
    route = respx.get(USER_URL).mock(return_value=httpx.Response(200, json=LIVE_USER))
    asyncio.run(fetch_identity("secret-token"))
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret-token"


@respx.mock
def test_identity_requests_only_needed_fields() -> None:
    """Лишние поля VK отвергает ошибкой, а `permissions` — сотни строк."""
    route = respx.get(USER_URL).mock(return_value=httpx.Response(200, json=LIVE_USER))
    asyncio.run(fetch_identity("token"))
    fields = route.calls.last.request.url.params["fields"]
    assert "additional_info" in fields
    assert "permissions" not in fields


@respx.mock
def test_401_is_invalid_token() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(InvalidTokenError):
        asyncio.run(fetch_identity("token"))


@respx.mock
def test_403_is_invalid_token() -> None:
    respx.get(USER_URL).mock(
        return_value=httpx.Response(403, json={"error": {"code": "access_denied"}})
    )
    with pytest.raises(InvalidTokenError):
        asyncio.run(fetch_identity("token"))


@respx.mock
def test_500_is_unreachable_not_invalid_token() -> None:
    """Разделение принципиальное: 5xx не повод объявлять токен мёртвым."""
    respx.get(USER_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(VkUnreachableError):
        asyncio.run(fetch_identity("token"))


@respx.mock
def test_network_failure_is_unreachable() -> None:
    respx.get(USER_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(VkUnreachableError):
        asyncio.run(fetch_identity("token"))


@respx.mock
def test_token_is_not_leaked_into_error_message() -> None:
    respx.get(USER_URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(VkUnreachableError) as err:
        asyncio.run(fetch_identity("super-secret-token"))
    assert "super-secret-token" not in str(err.value)


@respx.mock
def test_non_json_body_is_unreachable() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(VkUnreachableError):
        asyncio.run(fetch_identity("token"))


@respx.mock
def test_missing_id_is_unreachable() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(200, json={"username": "x"}))
    with pytest.raises(VkUnreachableError):
        asyncio.run(fetch_identity("token"))


@respx.mock
def test_title_falls_back_to_username_then_id() -> None:
    respx.get(USER_URL).mock(
        return_value=httpx.Response(
            200, json={"id": 1, "username": "acc@agency_client", "additional_info": {}}
        )
    )
    assert asyncio.run(fetch_identity("t")).title == "acc@agency_client"


@respx.mock
def test_title_falls_back_to_id_when_nothing_else() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(200, json={"id": 42}))
    assert asyncio.run(fetch_identity("t")).title == "Кабинет 42"


@respx.mock
def test_balance_is_read_from_account_endpoint() -> None:
    respx.get(ACCOUNT_URL).mock(
        return_value=httpx.Response(200, json={"id": 20000002, "balance": "12345.67"})
    )
    assert asyncio.run(fetch_balance("token")) == "12345.67"


@respx.mock
def test_balance_returns_none_on_error() -> None:
    """Баланс справочный — ради него нельзя ронять добавление кабинета."""
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(500))
    assert asyncio.run(fetch_balance("token")) is None


@respx.mock
def test_balance_returns_none_on_network_failure() -> None:
    respx.get(ACCOUNT_URL).mock(side_effect=httpx.ConnectError("down"))
    assert asyncio.run(fetch_balance("token")) is None
