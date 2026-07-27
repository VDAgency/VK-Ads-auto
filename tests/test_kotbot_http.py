"""Тесты адаптера канала kotbot (`integrations/kotbot_http`) на respx-моках.

Живой сервис не поднимаем: проверяем маршруты спеки §4.3, формы запросов,
сериализацию спеки и карту ошибок (409 → переавторизация, 502 `flow_failed:<шаг>`,
501/прочие → понятное исключение, транспорт → `health_check() is False`).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from integrations.adapter import PlatformAdapter
from integrations.kotbot_http import (
    KotbotAdapter,
    KotbotFlowFailed,
    KotbotReauthRequired,
    KotbotRequestError,
)
from services.mapping import CampaignSpec

_URL = "http://kotbot:8002"

_SPEC = CampaignSpec(
    objective="socialengagement",
    name="Кампания",
    object_url="https://vk.com/club1",
    geo_raw="Самара",
    age_list=[18, 19],
    sex=["female"],
    budget_rub=30000,
)


def _adapter() -> KotbotAdapter:
    return KotbotAdapter(_URL)


def _body(request: httpx.Request) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(request.content.decode())
    return payload


# --- health_check -------------------------------------------------------------------


def test_health_check_true_when_service_healthy() -> None:
    async def scenario() -> bool:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(
                return_value=httpx.Response(200, json={"healthy": True, "strategies": {}})
            )
            return await _adapter().health_check()

    assert asyncio.run(scenario()) is True


def test_health_check_false_when_service_needs_reauth() -> None:
    # Сервис жив, но ни одна стратегия не годится → канал нездоров, роутер уйдёт на фолбэк.
    async def scenario() -> bool:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(
                return_value=httpx.Response(200, json={"healthy": False, "strategies": {}})
            )
            return await _adapter().health_check()

    assert asyncio.run(scenario()) is False


def test_health_check_false_on_non_200() -> None:
    async def scenario() -> bool:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(return_value=httpx.Response(503))
            return await _adapter().health_check()

    assert asyncio.run(scenario()) is False


def test_health_check_false_on_transport_error() -> None:
    async def scenario() -> bool:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(side_effect=httpx.ConnectError("no route"))
            return await _adapter().health_check()

    assert asyncio.run(scenario()) is False


def test_health_check_false_on_broken_body() -> None:
    async def scenario() -> bool:
        with respx.mock() as router:
            router.get(f"{_URL}/health").mock(return_value=httpx.Response(200, text="not json"))
            return await _adapter().health_check()

    assert asyncio.run(scenario()) is False


# --- маршруты действий (спека §4.3) -------------------------------------------------


def test_create_cabinet_posts_object_and_returns_external_ref() -> None:
    async def scenario() -> tuple[str, dict[str, Any]]:
        with respx.mock() as router:
            route = router.post(f"{_URL}/cabinets").mock(
                return_value=httpx.Response(200, json={"external_ref": "86937"})
            )
            ref = await _adapter().create_cabinet(
                1,
                "client-7",
                ad_object_url="https://vk.com/club1",
                ad_object_name="Клуб",
            )
        return ref, _body(route.calls.last.request)

    ref, body = asyncio.run(scenario())
    assert ref == "86937"
    assert body == {
        "client_ref": "client-7",
        "ad_object_url": "https://vk.com/club1",
        "ad_object_name": "Клуб",
    }


def test_create_campaign_serializes_spec_as_dict() -> None:
    async def scenario() -> tuple[str, dict[str, Any]]:
        with respx.mock() as router:
            route = router.post(f"{_URL}/campaigns").mock(
                return_value=httpx.Response(200, json={"external_id": "555", "status": "draft"})
            )
            external_id = await _adapter().create_campaign("86937", "socialengagement", spec=_SPEC)
        return external_id, _body(route.calls.last.request)

    external_id, body = asyncio.run(scenario())
    assert external_id == "555"
    assert body["cabinet_ref"] == "86937"
    assert body["spec"]["objective"] == "socialengagement"
    assert body["spec"]["object_url"] == "https://vk.com/club1"
    assert body["spec"]["age_list"] == [18, 19]


def test_create_campaign_without_spec_sends_goal_only() -> None:
    async def scenario() -> dict[str, Any]:
        with respx.mock() as router:
            route = router.post(f"{_URL}/campaigns").mock(
                return_value=httpx.Response(200, json={"external_id": "555"})
            )
            await _adapter().create_campaign("86937", "socialengagement")
        return _body(route.calls.last.request)

    assert asyncio.run(scenario())["spec"] == {"objective": "socialengagement"}


def test_create_campaign_from_spec_sends_spec_and_creative() -> None:
    async def scenario() -> tuple[str, dict[str, Any], dict[str, Any]]:
        with respx.mock() as router:
            campaigns = router.post(f"{_URL}/campaigns").mock(
                return_value=httpx.Response(200, json={"external_id": "555"})
            )
            creative = router.post(f"{_URL}/campaigns/555/creative").mock(
                return_value=httpx.Response(200, json={"creative_ref": "crt-1"})
            )
            external_id = await _adapter().create_campaign_from_spec(
                "86937",
                _SPEC,
                creative_ref="/data/creatives/1/x.jpg",
                title="Заголовок",
                body="Текст",
                budget_limit_day=1000.0,
            )
        return (
            external_id,
            _body(campaigns.calls.last.request),
            _body(creative.calls.last.request),
        )

    external_id, campaign_body, creative_body = asyncio.run(scenario())
    assert external_id == "555"
    assert campaign_body["spec"]["name"] == "Кампания"
    assert campaign_body["spec"]["budget_limit_day"] == 1000.0
    assert creative_body == {
        "file_path": "/data/creatives/1/x.jpg",
        "title": "Заголовок",
        "body": "Текст",
    }


def test_create_campaign_from_spec_skips_creative_when_absent() -> None:
    async def scenario() -> bool:
        with respx.mock(assert_all_called=False) as router:
            router.post(f"{_URL}/campaigns").mock(
                return_value=httpx.Response(200, json={"external_id": "555"})
            )
            creative = router.post(f"{_URL}/campaigns/555/creative")
            await _adapter().create_campaign_from_spec("86937", _SPEC)
            return creative.called

    assert asyncio.run(scenario()) is False


def test_upload_creative_returns_creative_ref() -> None:
    async def scenario() -> tuple[str, dict[str, Any]]:
        with respx.mock() as router:
            route = router.post(f"{_URL}/campaigns/555/creative").mock(
                return_value=httpx.Response(200, json={"creative_ref": "crt-1"})
            )
            ref = await _adapter().upload_creative("555", "/x.jpg", title="t", body="b")
        return ref, _body(route.calls.last.request)

    ref, body = asyncio.run(scenario())
    assert ref == "crt-1"
    assert body == {"file_path": "/x.jpg", "title": "t", "body": "b"}


def test_launch_calls_launch_route() -> None:
    async def scenario() -> bool:
        with respx.mock() as router:
            route = router.post(f"{_URL}/campaigns/555/launch").mock(
                return_value=httpx.Response(200, json={"status": "moderation"})
            )
            await _adapter().launch("555")
            return route.called

    assert asyncio.run(scenario()) is True


def test_stop_calls_stop_route() -> None:
    async def scenario() -> bool:
        with respx.mock() as router:
            route = router.post(f"{_URL}/campaigns/555/stop").mock(
                return_value=httpx.Response(200, json={"status": "stopped"})
            )
            await _adapter().stop("555")
            return route.called

    assert asyncio.run(scenario()) is True


def test_get_status_returns_platform_status() -> None:
    async def scenario() -> str:
        with respx.mock() as router:
            router.get(f"{_URL}/campaigns/555/status").mock(
                return_value=httpx.Response(200, json={"status": "moderation"})
            )
            return await _adapter().get_status("555")

    assert asyncio.run(scenario()) == "moderation"


def test_get_status_defaults_to_unknown() -> None:
    async def scenario() -> str:
        with respx.mock() as router:
            router.get(f"{_URL}/campaigns/555/status").mock(
                return_value=httpx.Response(200, json={})
            )
            return await _adapter().get_status("555")

    assert asyncio.run(scenario()) == "unknown"


def test_get_stats_coerces_numbers_to_float() -> None:
    async def scenario() -> dict[str, float]:
        with respx.mock() as router:
            router.get(f"{_URL}/campaigns/555/stats").mock(
                return_value=httpx.Response(
                    200,
                    json={"shows": 100, "clicks": "5", "spent": 250.5, "goals": 3, "note": "x"},
                )
            )
            return await _adapter().get_stats("555")

    assert asyncio.run(scenario()) == {"shows": 100.0, "clicks": 5.0, "spent": 250.5, "goals": 3.0}


def test_injected_client_is_used() -> None:
    # Клиент можно подменить (тесты/переиспользование пула соединений).
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/campaigns/555/status"
        return httpx.Response(200, json={"status": "launched"})

    async def scenario() -> str:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            return await KotbotAdapter(f"{_URL}/", client=client).get_status("555")

    assert asyncio.run(scenario()) == "launched"


# --- карта ошибок -------------------------------------------------------------------


def test_409_raises_reauth_required() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/campaigns").mock(
                return_value=httpx.Response(409, json={"detail": "reauth_required"})
            )
            await _adapter().create_campaign("86937", "socialengagement")

    with pytest.raises(KotbotReauthRequired):
        asyncio.run(scenario())


def test_502_flow_failed_carries_step_name() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/campaigns/555/launch").mock(
                return_value=httpx.Response(502, json={"detail": "flow_failed:select_object"})
            )
            await _adapter().launch("555")

    with pytest.raises(KotbotFlowFailed) as exc:
        asyncio.run(scenario())
    assert exc.value.step == "select_object"
    assert "select_object" in str(exc.value)


def test_501_raises_request_error_not_silent_success() -> None:
    # Живые флоу ещё не написаны: 501 обязан быть ошибкой, а не «успешным» запуском.
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/campaigns").mock(
                return_value=httpx.Response(501, json={"detail": "not_implemented"})
            )
            await _adapter().create_campaign("86937", "socialengagement")

    with pytest.raises(KotbotRequestError) as exc:
        asyncio.run(scenario())
    assert "not_implemented" in str(exc.value)


def test_transport_error_on_action_raises_request_error() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/campaigns/555/stop").mock(
                side_effect=httpx.ConnectError("no route")
            )
            await _adapter().stop("555")

    with pytest.raises(KotbotRequestError):
        asyncio.run(scenario())


def test_non_object_body_raises_request_error() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.get(f"{_URL}/campaigns/555/stats").mock(
                return_value=httpx.Response(200, text="<html/>")
            )
            await _adapter().get_stats("555")

    with pytest.raises(KotbotRequestError):
        asyncio.run(scenario())


def test_adapter_is_platform_adapter() -> None:
    assert isinstance(_adapter(), PlatformAdapter)
