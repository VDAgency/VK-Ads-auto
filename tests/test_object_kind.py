"""Тип объекта рекламы берётся из брифа, а не угадывается по ссылке.

Короткий (vanity) адрес вида `vk.ru/fin_dolm` не позволяет отличить человека от
сообщества, но в брифе тип указан явно. Раньше он терялся по пути, и личная
страница уезжала в VK как сообщество: не тот пакет, не та цель, не тот тип
объекта. Здесь закрепляем, что бриф авторитетнее эвристики по ссылке.
"""

from __future__ import annotations

from integrations.vk_api import (
    OBJECTIVE_COMMUNITY,
    OBJECTIVE_PROFILE,
    PACKAGE_COMMUNITY,
    PACKAGE_PROFILE,
    URL_OBJECT_COMMUNITY,
    URL_OBJECT_PROFILE,
    campaign_objective,
    resolve_ad_object,
)
from services.brief_parser import BriefVariant, parse_brief
from services.mapping import (
    OBJECT_KIND_COMMUNITY,
    OBJECT_KIND_PERSONAL,
    CampaignSpec,
    build_campaign_spec,
)

# Бриф Вячеслава от 2026-07-26: личная страница под коротким адресом.
_RAW_PERSONAL = {
    "geo": "вся Россия",
    "term": "1 месяц",
    "email": "operator@example.com",
    "phone": "+70000000000",
    "age_to": "50",
    "budget": "5 000 ₽",
    "gender": "любой",
    "tax_id": "000000000000",
    "age_from": "30",
    "full_name": "Иванов Иван Иванович",
    "materials": "ничего нет, нужна помощь",
    "object_url": "https://vk.ru/fin_dolm",
    "target_type": "личная страница",
    "vk_ad_cabinet_id": "1090721382",
    "audience_description": "Руководители бизнеса",
}


def _spec(raw: dict[str, str], variant: BriefVariant = BriefVariant.INDIVIDUAL) -> CampaignSpec:
    return build_campaign_spec(parse_brief(raw, variant))


def test_spec_carries_personal_page_kind_from_brief() -> None:
    spec = _spec(_RAW_PERSONAL)
    assert spec.object_kind == OBJECT_KIND_PERSONAL


def test_spec_carries_community_kind_from_brief() -> None:
    raw = {**_RAW_PERSONAL, "target_type": "сообщество", "object_url": "https://vk.ru/my_club"}
    assert _spec(raw).object_kind == OBJECT_KIND_COMMUNITY


def test_vanity_personal_page_resolves_as_profile_when_kind_known() -> None:
    # Ключевой случай: по ссылке не понять, но бриф говорит «личная страница».
    ad_object = resolve_ad_object("https://vk.ru/fin_dolm", kind=OBJECT_KIND_PERSONAL)
    assert ad_object.url_object_type == URL_OBJECT_PROFILE
    assert ad_object.package_id == PACKAGE_PROFILE
    assert ad_object.objective == OBJECTIVE_PROFILE


def test_vanity_community_still_resolves_as_community() -> None:
    ad_object = resolve_ad_object("https://vk.ru/my_club", kind=OBJECT_KIND_COMMUNITY)
    assert ad_object.url_object_type == URL_OBJECT_COMMUNITY
    assert ad_object.package_id == PACKAGE_COMMUNITY
    assert ad_object.objective == OBJECTIVE_COMMUNITY


def test_numeric_url_wins_over_wrong_kind() -> None:
    # Числовой адрес — факт, а не догадка: он авторитетнее подсказки из брифа.
    ad_object = resolve_ad_object("https://vk.com/club228817082", kind=OBJECT_KIND_PERSONAL)
    assert ad_object.url_object_type == URL_OBJECT_COMMUNITY
    assert ad_object.url_object_id == "228817082"


def test_unknown_kind_falls_back_to_url_heuristic() -> None:
    # Старое поведение сохраняется, когда подсказки нет.
    assert resolve_ad_object("https://vk.ru/some_name").url_object_type == URL_OBJECT_COMMUNITY
    assert resolve_ad_object("https://vk.com/id777").url_object_type == URL_OBJECT_PROFILE


def test_campaign_objective_uses_brief_kind_for_vanity_personal_page() -> None:
    # Именно здесь ломалось: цель уезжала как для сообщества.
    assert campaign_objective(_spec(_RAW_PERSONAL)) == OBJECTIVE_PROFILE
