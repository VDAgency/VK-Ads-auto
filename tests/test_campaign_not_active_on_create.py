"""Созданная кампания не должна тратить деньги без явного разрешения.

Боевая проверка 2026-07-27: VK создаёт кампанию сразу в статусе `active`, даже если
`launch()` не вызывать. Прежний предохранитель «не вызывать запуск» был фикцией —
кампания уже откручивалась. Теперь создание неактивно по умолчанию и гасится сразу,
а деньги тратятся только при явном `activate=True`.
"""

from __future__ import annotations

import asyncio

import httpx
import respx
from integrations.vk_api import BASE_URL, VkApiAdapter
from pydantic import SecretStr
from services.launch import run_campaign
from services.mapping import OBJECT_KIND_PERSONAL, CampaignSpec

_PLAN_ID = 26135882
_GROUP_ID = 147206867

_SPEC = CampaignSpec(
    objective="socialengagement",
    name="Подписчики · Тест",
    object_url="https://vk.ru/fin_dolm",
    geo_raw="вся Россия",
    object_kind=OBJECT_KIND_PERSONAL,
    age_list=[30, 31],
    budget_rub=5000,
)


def _mock_vk() -> dict[str, respx.Route]:
    """Минимальный VK: справочник гео, регистрация ссылки, создание и остановка."""
    return {
        "regions": respx.get(f"{BASE_URL}/regions.json").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 188, "name": "Россия"}]})
        ),
        "url": respx.post(f"{BASE_URL}/urls.json").mock(
            return_value=httpx.Response(201, json={"id": 128898271})
        ),
        "create": respx.post(f"{BASE_URL}/ad_plans.json").mock(
            return_value=httpx.Response(200, json={"id": _PLAN_ID})
        ),
        # Группы плана читаются через /ad_groups.json: вложенное поле `campaigns`
        # у /ad_plans.json на чтение приходит пустым (боевая проверка 2026-07-27).
        "read": respx.get(f"{BASE_URL}/ad_groups.json").mock(
            return_value=httpx.Response(200, json={"items": [{"id": _GROUP_ID}]})
        ),
        "stop_plan": respx.post(f"{BASE_URL}/ad_plans/{_PLAN_ID}.json").mock(
            return_value=httpx.Response(204)
        ),
        "stop_group": respx.post(f"{BASE_URL}/ad_groups/{_GROUP_ID}.json").mock(
            return_value=httpx.Response(204)
        ),
    }


def _adapter() -> VkApiAdapter:
    return VkApiAdapter(SecretStr("test-token"))


@respx.mock
def test_creation_is_inactive_by_default() -> None:
    routes = _mock_vk()

    plan_id = asyncio.run(_adapter().create_campaign_from_spec("29506243", _SPEC))

    assert plan_id == str(_PLAN_ID)
    # Кампания и группа погашены сразу после создания — деньги не тратятся.
    assert routes["stop_plan"].called
    assert routes["stop_group"].called


@respx.mock
def test_explicit_activate_leaves_campaign_running() -> None:
    routes = _mock_vk()

    asyncio.run(_adapter().create_campaign_from_spec("29506243", _SPEC, activate=True))

    assert not routes["stop_plan"].called
    assert not routes["stop_group"].called


@respx.mock
def test_run_campaign_without_autostart_stops_campaign() -> None:
    # Сквозная проверка: снятый автозапуск обязан гасить кампанию в VK,
    # а не просто вернуть launched=False.
    routes = _mock_vk()

    result = asyncio.run(run_campaign(_adapter(), "29506243", _SPEC, autostart=False))

    assert result.launched is False
    assert routes["stop_plan"].called
    assert routes["stop_group"].called


@respx.mock
def test_stop_covers_both_levels() -> None:
    # Остановки одного ad_plan мало: деньги списываются по группам.
    routes = _mock_vk()

    asyncio.run(_adapter().stop(str(_PLAN_ID)))

    assert routes["stop_plan"].called
    assert routes["stop_group"].called
