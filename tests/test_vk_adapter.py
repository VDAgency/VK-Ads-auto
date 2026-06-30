import asyncio
from pathlib import Path

import httpx
from integrations.vk_api import VkApiAdapter
from pydantic import SecretStr


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("Authorization") == "Bearer tok"
    path = request.url.path
    if path.endswith("/user.json"):
        return httpx.Response(200, json={"id": 1, "username": "u"})
    if path.endswith("/agency/clients.json"):
        return httpx.Response(200, json={"id": 888})
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
