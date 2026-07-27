"""Площадки подписки: разбор брифа, справочник для интерфейсов, сборка объявления.

Все семь площадок прошли боевое создание кампании в живом кабинете 2026-07-27
(ad_plan 26146102…26146113, все остановлены, 0 показов, 0 ₽). Здесь закрепляем
контракт, который при этом подтвердился.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import respx
from integrations.vk_api import BASE_URL, VkApiAdapter, resolve_ad_object
from integrations.vk_surfaces import SURFACES, surface_for
from pydantic import SecretStr
from services.brief_parser import TargetType, parse_target_type
from services.goals import subscription_targets, target_title
from services.mapping import CampaignSpec

_UPLOADED = 777
_URL_OBJECT = 128898271


def test_every_surface_has_a_distinct_package() -> None:
    packages = [surface.package_id for surface in SURFACES]
    assert len(packages) == len(set(packages)), "пакеты площадок обязаны различаться"


def test_every_surface_kind_is_a_brief_target_type() -> None:
    # Ключи справочника и значения брифа обязаны совпадать — иначе выбор клиента
    # не доедет до адаптера и молча станет сообществом.
    for surface in SURFACES:
        assert TargetType(surface.kind).value == surface.kind


def test_brief_parses_every_surface_wording() -> None:
    cases = {
        "личная страница": TargetType.PERSONAL_PAGE,
        "сообщество": TargetType.COMMUNITY,
        "сообщество / группа": TargetType.COMMUNITY,
        "рассылка": TargetType.NEWSLETTER,
        "канал ВКонтакте": TargetType.VK_CHANNEL,
        "канал MAX": TargetType.MAX_CHANNEL,
        "сообщество в Одноклассниках": TargetType.OK_COMMUNITY,
        "профиль в Одноклассниках": TargetType.OK_PROFILE,
    }
    for text, expected in cases.items():
        assert parse_target_type(text) is expected, text


def test_odnoklassniki_wins_over_the_word_community() -> None:
    # Порядок проверок важен: «сообщество в Одноклассниках» — это ОК, а не ВК.
    assert parse_target_type("сообщество в Одноклассниках") is TargetType.OK_COMMUNITY
    assert parse_target_type("группа ok.ru") is TargetType.OK_COMMUNITY


def test_unknown_wording_stays_personal_page() -> None:
    assert parse_target_type("") is TargetType.PERSONAL_PAGE
    assert parse_target_type("что-то невнятное") is TargetType.PERSONAL_PAGE


def test_subscription_targets_expose_all_surfaces_to_interfaces() -> None:
    targets = subscription_targets()
    assert len(targets) == len(SURFACES)
    assert all(target.available for target in targets), "все площадки прошли боевую проверку"
    assert target_title("newsletter") == "Рассылка ВКонтакте"
    assert target_title("нет такой") == "нет такой"


def _spec(kind: str, url: str) -> CampaignSpec:
    return CampaignSpec(
        objective="socialengagement",
        name="Подписчики · Тест",
        object_url=url,
        geo_raw="Москва",
        object_kind=kind,
        budget_rub=5000,
    )


def _created_plan(kind: str, url: str, creative: str) -> dict[str, Any]:
    """Собрать кампанию через адаптер и вернуть отправленное в VK тело запроса."""
    with respx.mock:
        respx.get(f"{BASE_URL}/regions.json").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 5506, "name": "Москва"}]})
        )
        respx.post(f"{BASE_URL}/urls.json").mock(
            return_value=httpx.Response(201, json={"id": _URL_OBJECT})
        )
        respx.post(f"{BASE_URL}/content/static.json").mock(
            return_value=httpx.Response(200, json={"id": _UPLOADED})
        )
        create = respx.post(f"{BASE_URL}/ad_plans.json").mock(
            return_value=httpx.Response(200, json={"id": 26146102})
        )
        respx.get(f"{BASE_URL}/ad_groups.json").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 147221579}]})
        )
        respx.post(url__regex=rf"{BASE_URL}/ad_plans/\d+\.json").mock(
            return_value=httpx.Response(204)
        )
        respx.post(url__regex=rf"{BASE_URL}/ad_groups/\d+\.json").mock(
            return_value=httpx.Response(204)
        )

        asyncio.run(
            VkApiAdapter(SecretStr("t")).create_campaign_from_spec(
                "29506243",
                _spec(kind, url),
                creative_ref=creative,
                title="Подписывайтесь",
                body="Текст объявления",
            )
        )
        payload: dict[str, Any] = json.loads(create.calls.last.request.content)
        return payload


def test_each_surface_sends_its_own_package_and_objective(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from PIL import Image

    creative = tmp_path / "square.png"
    Image.new("RGB", (900, 900), (5, 5, 5)).save(creative)

    cases = {
        "community": "https://vk.ru/my_club",
        "personal_page": "https://vk.ru/fin_dolm",
        "newsletter": "https://vk.com/app5898182_-228817082",
        "vk_channel": "https://vk.com/some_channel",
        "max_channel": "https://max.ru/some_channel",
        "ok_community": "https://ok.ru/group/70000001051417",
        "ok_profile": "https://ok.ru/profile/580483489443",
        "dzen_channel": "https://dzen.ru/tehnologii",
    }
    for kind, url in cases.items():
        surface = surface_for(kind)
        payload = _created_plan(kind, url, str(creative))
        campaign = payload["campaigns"][0]
        assert payload["objective"] == surface.objective, kind
        assert campaign["package_id"] == surface.package_id, kind

        textblocks = campaign["banners"][0]["textblocks"]
        pattern = surface.default_pattern
        assert pattern.title_slot in textblocks, kind
        assert pattern.text_slot in textblocks, kind
        if pattern.cta_slot:  # у Дзена кнопки нет вовсе
            assert textblocks[pattern.cta_slot] == {"text": surface.default_cta}, kind


def test_channels_cut_the_text_to_ninety_characters(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Каналы VK и MAX — единственные площадки со слотом text_90 вместо text_2000.
    from PIL import Image

    creative = tmp_path / "square.png"
    Image.new("RGB", (900, 900), (5, 5, 5)).save(creative)

    payload = _created_plan("vk_channel", "https://vk.com/c", str(creative))
    textblocks = payload["campaigns"][0]["banners"][0]["textblocks"]
    assert "text_90" in textblocks
    assert "text_2000" not in textblocks


def test_communities_exclude_existing_members_but_others_do_not() -> None:
    for kind in ("community", "ok_community"):
        assert resolve_ad_object("https://x/y", kind).is_community, kind
    for kind in ("personal_page", "newsletter", "vk_channel", "max_channel", "ok_profile"):
        assert not resolve_ad_object("https://x/y", kind).is_community, kind


def test_dzen_is_the_odd_one_out(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """У Дзена свои правила, и объявление обязано их соблюдать.

    Ссылка идёт в слот `dzen_publication`, кнопки нет вовсе, заголовок ограничен
    25 символами, и требуется отдельное имя канала (боевая проверка 2026-07-27).
    """
    from PIL import Image

    creative = tmp_path / "square.png"
    Image.new("RGB", (900, 900), (5, 5, 5)).save(creative)

    payload = _created_plan("dzen_channel", "https://dzen.ru/tehnologii", str(creative))
    banner = payload["campaigns"][0]["banners"][0]

    assert "dzen_publication" in banner["urls"]
    assert "primary" not in banner["urls"]

    textblocks = banner["textblocks"]
    assert not [slot for slot in textblocks if slot.startswith("cta")], "у Дзена кнопки нет"
    assert "name_140" in textblocks
    assert len(textblocks["title_25"]["text"]) <= 25
    assert len(textblocks["text_40"]["text"]) <= 40


def test_other_surfaces_keep_the_primary_url_slot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from PIL import Image

    creative = tmp_path / "square.png"
    Image.new("RGB", (900, 900), (5, 5, 5)).save(creative)

    banner = _created_plan("community", "https://vk.ru/c", str(creative))["campaigns"][0][
        "banners"
    ][0]
    assert "primary" in banner["urls"]


def test_dzen_accepts_only_images() -> None:
    # Видео-шаблонов у пакета нет: подсовывать ролик бессмысленно.
    from integrations.vk_surfaces import DZEN_CHANNEL

    assert not DZEN_CHANNEL.patterns_for(is_video=True)
    assert DZEN_CHANNEL.patterns_for(is_video=False)
