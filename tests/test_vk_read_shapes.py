"""Чтение из VK: какие эндпоинты реально отдают нужные поля.

Два места, где живой API повёл себя не так, как выглядит по документации, и из-за
этого молча ломались остановка и синхронизация статусов (боевая проверка 2026-07-27):

- `GET /ad_plans/{id}.json` отвечает 200, но БЕЗ поля `status` — статус всегда
  получался `unknown`, и статусы кампаний в БД не обновлялись никогда;
- `GET /ad_plans.json` не отдаёт вложенные `campaigns` (приходит пустой список) —
  двухуровневая остановка вырождалась в остановку одного плана, а деньги
  списываются по группам.

Оба читаются иначе, и тесты закрепляют именно способ чтения, а не только результат.
"""

from __future__ import annotations

import asyncio

import httpx
import respx
from integrations.vk_api import BASE_URL, VkApiAdapter
from pydantic import SecretStr

_PLAN = "26148092"
_GROUP = 147221594


def _adapter() -> VkApiAdapter:
    return VkApiAdapter(SecretStr("test-token"))


@respx.mock
def test_status_is_read_from_the_list_endpoint_with_explicit_fields() -> None:
    # Одиночный эндпоинт отдаёт объект без статуса — если бы адаптер ходил туда,
    # этот мок остался бы невостребованным, а статус пришёл бы `unknown`.
    single = respx.get(f"{BASE_URL}/ad_plans/{_PLAN}.json").mock(
        return_value=httpx.Response(200, json={"id": int(_PLAN), "name": "Подписчики"})
    )
    listing = respx.get(f"{BASE_URL}/ad_plans.json").mock(
        return_value=httpx.Response(200, json={"items": [{"id": int(_PLAN), "status": "blocked"}]})
    )

    assert asyncio.run(_adapter().get_status(_PLAN)) == "blocked"
    assert listing.called
    assert not single.called

    request = listing.calls.last.request
    assert "status" in request.url.params["fields"]


@respx.mock
def test_unknown_status_when_plan_is_missing() -> None:
    respx.get(f"{BASE_URL}/ad_plans.json").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    assert asyncio.run(_adapter().get_status(_PLAN)) == "unknown"


@respx.mock
def test_stop_finds_groups_through_ad_groups_endpoint() -> None:
    # Группы приходят только отсюда: вложенное поле `campaigns` на чтение пустое.
    groups = respx.get(f"{BASE_URL}/ad_groups.json").mock(
        return_value=httpx.Response(200, json={"items": [{"id": _GROUP}]})
    )
    stop_plan = respx.post(f"{BASE_URL}/ad_plans/{_PLAN}.json").mock(
        return_value=httpx.Response(204)
    )
    stop_group = respx.post(f"{BASE_URL}/ad_groups/{_GROUP}.json").mock(
        return_value=httpx.Response(204)
    )

    asyncio.run(_adapter().stop(_PLAN))

    assert stop_plan.called, "план обязан гаситься"
    assert stop_group.called, "группа обязана гаситься — деньги списываются по ней"
    assert groups.calls.last.request.url.params["_ad_plan_id"] == _PLAN


@respx.mock
def test_stop_does_not_rely_on_nested_campaigns_field() -> None:
    # Воспроизводим боевой ответ: план есть, вложенных campaigns нет.
    respx.get(f"{BASE_URL}/ad_plans.json").mock(
        return_value=httpx.Response(200, json={"items": [{"id": int(_PLAN), "campaigns": []}]})
    )
    respx.get(f"{BASE_URL}/ad_groups.json").mock(
        return_value=httpx.Response(200, json={"items": [{"id": _GROUP}]})
    )
    respx.post(f"{BASE_URL}/ad_plans/{_PLAN}.json").mock(return_value=httpx.Response(204))
    stop_group = respx.post(f"{BASE_URL}/ad_groups/{_GROUP}.json").mock(
        return_value=httpx.Response(204)
    )

    asyncio.run(_adapter().stop(_PLAN))

    assert stop_group.called
