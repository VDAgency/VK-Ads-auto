import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from integrations.vk_api import (
    BASE_URL,
    VkAgencyClient,
    VkAgencyClientForbidden,
    VkAgencyClientNotFound,
    VkAgencyClientPage,
    VkAgencyClientUnavailable,
    VkAgencyClientValidationError,
    VkApiAdapter,
)
from pydantic import SecretStr

# Форма ответа VK на `/agency/clients.json` — из документации
# (ads.vk.ru/en/doc/api/resource/AgencyClients), боевым вызовом ещё не проверена.
_AGENCY_CLIENT_ITEM: dict[str, Any] = {
    "access_type": "full_access",
    "status": "active",
    "user": {
        "id": 888,
        "username": "client888",
        "status": "active",
        "account": {"id": 999, "balance": "1500.50", "currency_balance_hold": 0},
        "additional_info": {"client_name": "ООО Ромашка", "email": "client@example.com"},
    },
}

_PARSED_AGENCY_CLIENT = VkAgencyClient(
    client_id="888",
    username="client888",
    ad_account_id="999",
    balance="1500.50",
    status="active",
    access_type="full_access",
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("Authorization") == "Bearer tok"
    path = request.url.path
    if path.endswith("/user.json"):
        return httpx.Response(200, json={"id": 1, "username": "u"})
    if path.endswith("/agency/clients.json"):
        return httpx.Response(
            200, json={"count": 1, "items": [_AGENCY_CLIENT_ITEM], "limit": 20, "offset": 0}
        )
    if path.endswith("/ad_plans.json"):
        return httpx.Response(200, json={"id": 555})
    if path.endswith("/content/static.json"):
        return httpx.Response(200, json={"id": 777})
    if "/statistics/" in path:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"total": {"base": {"shows": 100, "clicks": 5, "spent": 250.0, "ctr": 5.0}}}
                ]
            },
        )
    if "/ad_plans/" in path:
        return httpx.Response(200, json={})
    return httpx.Response(404)


def _adapter(handler: httpx.MockTransport | None = None) -> VkApiAdapter:
    transport = handler or httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport)
    return VkApiAdapter(SecretStr("tok"), client=client)


def test_health_check_true() -> None:
    assert asyncio.run(_adapter().health_check()) is True


def test_health_check_false_on_error() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert asyncio.run(_adapter(httpx.MockTransport(boom)).health_check()) is False


def test_create_campaign_returns_id() -> None:
    assert asyncio.run(_adapter().create_campaign("cab-1", "socialengagement")) == "555"


def test_create_cabinet_returns_id() -> None:
    assert asyncio.run(_adapter().create_cabinet(1, "ООО Ромашка")) == "888"


def test_launch_does_not_raise() -> None:
    asyncio.run(_adapter().launch("555"))


def test_get_stats_parses_base_metrics() -> None:
    stats = asyncio.run(_adapter().get_stats("555"))
    assert stats["shows"] == 100.0
    assert stats["clicks"] == 5.0
    assert stats["spent"] == 250.0


def test_upload_creative_returns_content_id(tmp_path: Path) -> None:
    creative = tmp_path / "ad.png"
    creative.write_bytes(b"\x89PNG\r\n")
    assert asyncio.run(_adapter().upload_creative("555", str(creative))) == "777"


# --- create_cabinet: обёртка контракта поверх create_agency_client ----------


@respx.mock
def test_create_cabinet_sends_correct_nested_body() -> None:
    route = respx.post(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"items": [_AGENCY_CLIENT_ITEM]})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    result = _run(adapter.create_cabinet(1, "ООО Ромашка"))
    assert result == "888"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "access_type": "full_access",
        "user": {"additional_info": {"client_name": "ООО Ромашка"}},
    }


# --- create_agency_client: тело запроса, разбор ответа, отказы --------------


@respx.mock
def test_create_agency_client_parses_full_response() -> None:
    respx.post(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"count": 1, "items": [_AGENCY_CLIENT_ITEM]})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    client = _run(adapter.create_agency_client(client_name="ООО Ромашка"))
    assert client == _PARSED_AGENCY_CLIENT


@respx.mock
def test_create_agency_client_sends_client_info() -> None:
    route = respx.post(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"items": [_AGENCY_CLIENT_ITEM]})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    _run(adapter.create_agency_client(client_name="ООО Ромашка", client_info="ИНН 7700000000"))
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "access_type": "full_access",
        "user": {
            "additional_info": {
                "client_name": "ООО Ромашка",
                "client_info": "ИНН 7700000000",
            }
        },
    }


@respx.mock
def test_create_agency_client_adds_existing_by_user_id() -> None:
    route = respx.post(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"items": [_AGENCY_CLIENT_ITEM]})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    _run(adapter.create_agency_client(client_name="x", user_id="42"))
    sent = json.loads(route.calls.last.request.content)
    assert sent["user"]["id"] == 42
    assert "username" not in sent["user"]


@respx.mock
def test_create_agency_client_adds_existing_by_username() -> None:
    route = respx.post(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"items": [_AGENCY_CLIENT_ITEM]})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    _run(adapter.create_agency_client(client_name="x", username="client888"))
    sent = json.loads(route.calls.last.request.content)
    assert sent["user"]["username"] == "client888"
    assert "id" not in sent["user"]


def test_create_agency_client_both_user_id_and_username_is_value_error() -> None:
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(ValueError):
        _run(adapter.create_agency_client(client_name="x", user_id="1", username="u"))


@respx.mock
def test_create_agency_client_validation_error_is_typed() -> None:
    respx.post(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(400, json={"error": "validation error"})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(VkAgencyClientValidationError):
        _run(adapter.create_agency_client(client_name="x"))


@respx.mock
def test_create_agency_client_forbidden_when_agency_not_confirmed() -> None:
    """403 значит «агентский статус не подтверждён либо нет create_clients» —
    отдельный тип ошибки, отличимый от обычного отказа (нужен внятный текст оператору)."""
    respx.post(f"{BASE_URL}/agency/clients.json").mock(return_value=httpx.Response(403))
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(VkAgencyClientForbidden):
        _run(adapter.create_agency_client(client_name="x"))


@respx.mock
def test_create_agency_client_not_found_is_typed() -> None:
    respx.post(f"{BASE_URL}/agency/clients.json").mock(return_value=httpx.Response(404))
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(VkAgencyClientNotFound):
        _run(adapter.create_agency_client(client_name="x", user_id="404404"))


@respx.mock
def test_create_agency_client_server_error_is_unavailable() -> None:
    respx.post(f"{BASE_URL}/agency/clients.json").mock(return_value=httpx.Response(503))
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(VkAgencyClientUnavailable):
        _run(adapter.create_agency_client(client_name="x"))


@respx.mock
def test_create_agency_client_empty_items_is_unavailable() -> None:
    respx.post(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(VkAgencyClientUnavailable):
        _run(adapter.create_agency_client(client_name="x"))


# --- list_agency_clients: постраничность и фильтры ---------------------------


@respx.mock
def test_list_agency_clients_returns_page() -> None:
    respx.get(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(
            200, json={"count": 1, "items": [_AGENCY_CLIENT_ITEM], "limit": 20, "offset": 0}
        )
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    page = _run(adapter.list_agency_clients())
    assert page == VkAgencyClientPage(items=[_PARSED_AGENCY_CLIENT], count=1, limit=20, offset=0)


@respx.mock
def test_list_agency_clients_sends_pagination_and_filters() -> None:
    route = respx.get(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    _run(
        adapter.list_agency_clients(
            limit=5, offset=10, username="client888", status="active", query="ромашка"
        )
    )
    sent = route.calls.last.request.url.params
    assert sent["limit"] == "5"
    assert sent["offset"] == "10"
    assert sent["_user__username"] == "client888"
    assert sent["_status"] == "active"
    assert sent["_q"] == "ромашка"


@respx.mock
def test_list_agency_clients_filters_by_user_id() -> None:
    route = respx.get(f"{BASE_URL}/agency/clients.json").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    adapter = VkApiAdapter(SecretStr("tok"))
    _run(adapter.list_agency_clients(user_id="888"))
    sent = route.calls.last.request.url.params
    assert sent["_user__id"] == "888"


def test_list_agency_clients_limit_out_of_range_is_value_error() -> None:
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(ValueError):
        _run(adapter.list_agency_clients(limit=51))


def test_list_agency_clients_zero_limit_is_value_error() -> None:
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(ValueError):
        _run(adapter.list_agency_clients(limit=0))


def test_list_agency_clients_negative_offset_is_value_error() -> None:
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(ValueError):
        _run(adapter.list_agency_clients(offset=-1))


@respx.mock
def test_list_agency_clients_forbidden_when_agency_not_confirmed() -> None:
    respx.get(f"{BASE_URL}/agency/clients.json").mock(return_value=httpx.Response(403))
    adapter = VkApiAdapter(SecretStr("tok"))
    with pytest.raises(VkAgencyClientForbidden):
        _run(adapter.list_agency_clients())
