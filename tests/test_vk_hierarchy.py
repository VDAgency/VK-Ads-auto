"""Иерархия кампании VK Ads создаётся ОДНИМ вложенным POST /ad_plans.json (respx, без сети).

Терминология myTarget: `ad_plan` → `campaigns` → `banners`, где `campaigns` — это то,
что в брифе и в ядре зовётся «группой объявлений». Попытка создать план отдельным
запросом отвечает HTTP 400 `campaigns: required`. Факты — docs/VK_API_REFERENCE.md,
раздел «Иерархия создаётся ОДНИМ вложенным запросом» (боевая проверка 2026-07-26).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

import httpx
import pytest
import respx
from integrations.vk_api import (
    AUTOBIDDING_MAX_GOALS,
    BASE_URL,
    IMAGE_CONTENT_SLOT,
    PACKAGE_COMMUNITY,
    PACKAGE_PROFILE,
    VIDEO_CONTENT_SLOT,
    VkApiAdapter,
    _banner_body,
    _parse_summary,
    content_slot,
    resolve_ad_object,
)
from pydantic import SecretStr
from services.mapping import CampaignSpec

_REGIONS: list[dict[str, Any]] = [
    {"id": 188, "name": "Россия", "parent_id": None},
    {"id": 5506, "name": "Москва", "parent_id": 70},
    {"id": 5560, "name": "Санкт-Петербург", "parent_id": 72},
]

T = TypeVar("T")


def _spec(**overrides: Any) -> CampaignSpec:
    defaults: dict[str, Any] = {
        "objective": "socialengagement",
        "name": "Подписчики · Клиент",
        "object_url": "https://vk.com/club228817082",
        "geo_raw": "Москва",
        "age_list": [18, 19, 20],
        "sex": ["female"],
        "budget_rub": 30000,
    }
    defaults.update(overrides)
    return CampaignSpec(**defaults)


def _adapter() -> VkApiAdapter:
    # Клиент не подменяем: respx перехватывает транспорт httpx глобально.
    return VkApiAdapter(SecretStr("tok"))


def _run(scenario: Callable[[respx.MockRouter], Awaitable[T]]) -> T:
    async def wrapper() -> T:
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{BASE_URL}/regions.json").mock(
                return_value=httpx.Response(200, json={"items": _REGIONS})
            )
            return await scenario(router)

    return asyncio.run(wrapper())


def _body(route: respx.Route) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(route.calls[0].request.content)
    return payload


# --- resolve_ad_object: тип объекта рекламы, package_id и objective ----------------


def test_club_url_is_community() -> None:
    ad_object = resolve_ad_object("https://vk.com/club228817082")
    assert ad_object.url_object_type == "vk_group"
    assert ad_object.url_object_id == "228817082"
    assert ad_object.package_id == PACKAGE_COMMUNITY
    assert ad_object.objective == "socialengagement"


def test_public_url_is_community() -> None:
    assert resolve_ad_object("https://vk.com/public123").url_object_id == "123"


def test_personal_page_uses_profile_package_and_objective() -> None:
    ad_object = resolve_ad_object("https://vk.com/id777")
    assert ad_object.url_object_type == "vk_user"
    assert ad_object.url_object_id == "777"
    assert ad_object.package_id == PACKAGE_PROFILE
    assert ad_object.objective == "socialengagement_profile"


def test_vanity_url_defaults_to_community_without_object_id() -> None:
    ad_object = resolve_ad_object("https://vk.ru/my_community/?from=ads")
    assert ad_object.package_id == PACKAGE_COMMUNITY
    assert ad_object.url_object_id is None
    assert ad_object.url == "https://vk.ru/my_community/?from=ads"


def test_mobile_host_and_trailing_slash_are_handled() -> None:
    assert resolve_ad_object("m.vk.com/club42/").url_object_id == "42"


# --- один вложенный запрос вместо трёх --------------------------------------------


def _create_from_spec(spec: CampaignSpec, **kwargs: Any) -> dict[str, Any]:
    """Прогнать `create_campaign_from_spec` и вернуть id, тело плана и число запросов.

    Эндпоинты `/ad_groups.json` и `/banners.json` замоканы намеренно: если адаптер
    снова начнёт создавать уровни по отдельности, счётчик их вызовов это поймает.
    """

    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        plan = router.post(f"{BASE_URL}/ad_plans.json").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        groups = router.post(f"{BASE_URL}/ad_groups.json").mock(
            return_value=httpx.Response(200, json={"id": 42})
        )
        banners = router.post(f"{BASE_URL}/banners.json").mock(
            return_value=httpx.Response(200, json={"id": 99})
        )

        async def call() -> str:
            plan_id = await _adapter().create_campaign_from_spec("cab-1", spec, **kwargs)
            return json.dumps(
                {
                    "id": plan_id,
                    "body": _body(plan),
                    "plan_calls": plan.call_count,
                    "legacy_calls": groups.call_count + banners.call_count,
                }
            )

        return call()

    payload: dict[str, Any] = json.loads(_run(scenario))
    return payload


def _nested_campaign(spec: CampaignSpec, **kwargs: Any) -> dict[str, Any]:
    """Единственная вложенная кампания (то, что раньше создавалось как ad_group)."""
    campaigns: list[dict[str, Any]] = _create_from_spec(spec, **kwargs)["body"]["campaigns"]
    assert len(campaigns) == 1
    return campaigns[0]


def _nested_banner(spec: CampaignSpec, **kwargs: Any) -> dict[str, Any]:
    banners: list[dict[str, Any]] = _nested_campaign(spec, **kwargs)["banners"]
    assert len(banners) == 1
    return banners[0]


def test_create_campaign_from_spec_sends_exactly_one_request() -> None:
    payload = _create_from_spec(_spec(), title="Заголовок", body="Текст", budget_limit_day=1000.0)
    assert payload["id"] == "555"
    assert payload["plan_calls"] == 1
    assert payload["legacy_calls"] == 0


def test_ad_plan_level_matches_live_fields() -> None:
    body = _create_from_spec(_spec(), budget_limit_day=1000.0)["body"]
    assert set(body) == {"name", "objective", "date_start", "campaigns"}
    assert body["name"] == "Подписчики · Клиент"
    assert body["objective"] == "socialengagement"
    assert body["date_start"] == date.today().isoformat()
    # Дневной лимит живёт на уровне campaigns[]: у живого плана он null.
    assert "budget_limit_day" not in body


def test_ad_plan_objective_upgrades_for_personal_page() -> None:
    spec = _spec(object_url="https://vk.com/id777")
    assert _create_from_spec(spec)["body"]["objective"] == "socialengagement_profile"


# --- вложенная кампания (бывшая ad_group) -----------------------------------------


def test_nested_campaign_carries_package_autobidding_and_budget() -> None:
    campaign = _nested_campaign(_spec(), budget_limit_day=300)
    assert campaign["name"] == "Подписчики · Клиент"
    assert campaign["package_id"] == PACKAGE_COMMUNITY
    assert campaign["autobidding_mode"] == AUTOBIDDING_MAX_GOALS
    assert campaign["budget_limit_day"] == 300.0


def test_nested_campaign_targetings_match_live_structure() -> None:
    assert _nested_campaign(_spec())["targetings"] == {
        "age": {"age_list": [0, 18, 19, 20], "expand": False},
        "sex": ["female"],
        "group_members": "not_group_member",
        "geo": {"regions": [5506]},
    }


def test_nested_campaign_omits_budget_when_not_given() -> None:
    assert "budget_limit_day" not in _nested_campaign(_spec())


def test_nested_campaign_omits_empty_sex_and_age() -> None:
    targetings = _nested_campaign(_spec(sex=[], age_list=[]))["targetings"]
    assert "sex" not in targetings
    assert "age" not in targetings


def test_nested_campaign_for_personal_page_uses_profile_package() -> None:
    campaign = _nested_campaign(_spec(object_url="https://vk.com/id777"))
    assert campaign["package_id"] == PACKAGE_PROFILE
    assert "group_members" not in campaign["targetings"]


def test_nested_campaign_geo_falls_back_to_russia_on_unknown_value() -> None:
    campaign = _nested_campaign(_spec(geo_raw="Урюпинск-Сити"))
    assert campaign["targetings"]["geo"] == {"regions": [188]}


# --- вложенный banner --------------------------------------------------------------


def test_nested_banner_carries_textblocks_and_urls() -> None:
    banner = _nested_banner(_spec(), title="Заголовок", body="Текст объявления")
    assert banner["textblocks"] == {
        "title_40_vkads": {"text": "Заголовок"},
        "text_2000": {"text": "Текст объявления"},
        "cta_community_vk": {"text": "signUp"},
    }
    assert banner["urls"] == {
        "primary": {
            "url": "https://vk.com/club228817082",
            "url_object_id": "228817082",
            "url_object_type": "vk_group",
        }
    }
    assert banner["content"] == {}


def test_nested_banner_omits_url_object_id_for_vanity_url() -> None:
    banner = _nested_banner(_spec(object_url="https://vk.com/my_community"))
    assert banner["urls"]["primary"] == {
        "url": "https://vk.com/my_community",
        "url_object_type": "vk_group",
    }


def test_banner_texts_fall_back_to_campaign_name_and_are_trimmed() -> None:
    textblocks = _nested_banner(_spec(name="и" * 60))["textblocks"]
    assert textblocks["title_40_vkads"]["text"] == "и" * 40
    assert textblocks["text_2000"]["text"] == "и" * 60


def test_creative_is_uploaded_before_the_plan_and_lands_in_banner_slot() -> None:
    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        plan = router.post(f"{BASE_URL}/ad_plans.json").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        router.post(f"{BASE_URL}/content/static.json").mock(
            return_value=httpx.Response(200, json={"id": 777})
        )

        async def call() -> str:
            path = Path(tempfile.gettempdir()) / "vk-ads-auto-test-creative.jpg"
            path.write_bytes(b"\xff\xd8\xff")
            try:
                await _adapter().create_campaign_from_spec("cab-1", _spec(), creative_ref=str(path))
            finally:
                path.unlink(missing_ok=True)
            return json.dumps(_body(plan))

        return call()

    banner = json.loads(_run(scenario))["campaigns"][0]["banners"][0]
    assert banner["content"] == {"icon_256x256": {"id": 777}}


def test_content_slot_depends_on_media_kind() -> None:
    assert content_slot("/data/creatives/1/ad.jpg") == IMAGE_CONTENT_SLOT
    assert content_slot("/data/creatives/1/ad.MP4") == VIDEO_CONTENT_SLOT


# --- построитель banner как чистая функция ----------------------------------------


def test_banner_body_adds_about_company_when_given() -> None:
    banner = _banner_body(
        _spec(),
        title="З",
        text="Т",
        content={},
        about_company="ИП Иванов, ИНН 000000000000",
    )
    assert banner["textblocks"]["about_company_115"] == {"text": "ИП Иванов, ИНН 000000000000"}


def test_banner_body_omits_about_company_when_absent() -> None:
    banner = _banner_body(_spec(), title="З", text="Т", content={})
    assert "about_company_115" not in banner["textblocks"]


def test_banner_body_rejects_unknown_content_slot() -> None:
    with pytest.raises(ValueError, match="content slot"):
        _banner_body(_spec(), title="З", text="Т", content={"banner_240x400": "777"})


# --- контрактный create_campaign ---------------------------------------------------


def test_create_campaign_with_spec_builds_the_full_nested_plan() -> None:
    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        route = router.post(f"{BASE_URL}/ad_plans.json").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )

        async def call() -> str:
            plan_id = await _adapter().create_campaign("cab-1", "socialengagement", spec=_spec())
            return json.dumps({"id": plan_id, "body": _body(route)})

        return call()

    payload = json.loads(_run(scenario))
    assert payload["id"] == "555"
    assert payload["body"]["campaigns"][0]["banners"][0]["urls"]["primary"]["url_object_id"] == (
        "228817082"
    )


def test_create_campaign_without_spec_still_sends_one_campaign() -> None:
    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        route = router.post(f"{BASE_URL}/ad_plans.json").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )

        async def call() -> str:
            plan_id = await _adapter().create_campaign("cab-1", "socialengagement")
            return json.dumps({"id": plan_id, "body": _body(route)})

        return call()

    payload = json.loads(_run(scenario))
    assert payload["id"] == "555"
    body = payload["body"]
    assert body["objective"] == "socialengagement"
    # Пустой план без спеки: одна кампания-заглушка, иначе VK отвечает
    # `campaigns: required`. Таргетинга и объявления у неё нет.
    campaigns = body["campaigns"]
    assert len(campaigns) == 1
    assert campaigns[0]["package_id"] == PACKAGE_COMMUNITY
    assert "banners" not in campaigns[0]


# --- статус и остановка -----------------------------------------------------------


def test_get_status_reads_ad_plan_status() -> None:
    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        router.get(f"{BASE_URL}/ad_plans/555.json").mock(
            return_value=httpx.Response(200, json={"id": 555, "status": "active"})
        )
        return _adapter().get_status("555")

    assert _run(scenario) == "active"


def test_get_status_unknown_when_field_missing() -> None:
    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        router.get(f"{BASE_URL}/ad_plans/555.json").mock(
            return_value=httpx.Response(200, json={"id": 555})
        )
        return _adapter().get_status("555")

    assert _run(scenario) == "unknown"


def test_stop_blocks_ad_plan() -> None:
    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        route = router.post(f"{BASE_URL}/ad_plans/555.json").mock(
            return_value=httpx.Response(200, json={})
        )

        async def call() -> str:
            await _adapter().stop("555")
            return str(_body(route)["status"])

        return call()

    assert _run(scenario) == "blocked"


# --- статистика: результат лежит в base.vk.result ---------------------------------

_LIVE_SUMMARY: dict[str, Any] = {
    "total": {
        "base": {
            "shows": 361305,
            "clicks": 1124,
            "goals": 0,
            "spent": "49481.81",
            "ctr": 0.311,
            "cpc": "44.02",
            "cpm": "136.95",
            "vk": {"goals": 602, "result": 602, "cpa": "82.2", "cpr": "82.2"},
        }
    }
}


def test_parse_summary_takes_result_from_vk_block() -> None:
    stats = _parse_summary(_LIVE_SUMMARY)
    assert stats["goals"] == 602.0
    assert stats["spent"] == 49481.81
    assert stats["shows"] == 361305.0
    assert stats["cpc"] == 44.02


def test_parse_summary_falls_back_to_vk_goals() -> None:
    payload = {"total": {"base": {"shows": 10, "vk": {"goals": 7}}}}
    assert _parse_summary(payload)["goals"] == 7.0


def test_parse_summary_falls_back_to_top_level_goals() -> None:
    payload = {"total": {"base": {"shows": 10, "goals": 3}}}
    assert _parse_summary(payload)["goals"] == 3.0


def test_parse_summary_supports_items_envelope() -> None:
    payload = {"items": [{"id": 1, "total": {"base": {"shows": 10, "vk": {"result": 4}}}}]}
    assert _parse_summary(payload)["goals"] == 4.0


def test_parse_summary_empty_payload() -> None:
    assert _parse_summary({}) == {}


def test_get_stats_requests_base_metrics() -> None:
    def scenario(router: respx.MockRouter) -> Awaitable[str]:
        route = router.get(f"{BASE_URL}/statistics/ad_plans/summary.json").mock(
            return_value=httpx.Response(200, json=_LIVE_SUMMARY)
        )

        async def call() -> str:
            stats = await _adapter().get_stats("555")
            request = route.calls[0].request
            return json.dumps({"goals": stats["goals"], "query": str(request.url.params)})

        return call()

    payload = json.loads(_run(scenario))
    assert payload["goals"] == 602.0
    assert "metrics=base" in payload["query"]
