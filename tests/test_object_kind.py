"""Площадка подписки берётся из брифа, а не угадывается по ссылке.

Короткий (vanity) адрес вида `vk.ru/fin_dolm` не позволяет отличить человека от
сообщества, но в брифе площадка указана явно. Раньше подсказка терялась по пути, и
личная страница уезжала в VK как сообщество: не тот пакет, не та цель.

С появлением рассылки, каналов и Одноклассников правило уточнилось: числовой адрес ВК
перевешивает бриф только в паре «сообщество или страница» — остальные площадки адресом
не выражаются вовсе.
"""

from __future__ import annotations

from integrations.vk_api import campaign_objective, resolve_ad_object
from integrations.vk_surfaces import (
    MAX_CHANNEL,
    OK_COMMUNITY,
    OK_PROFILE,
    VK_CHANNEL,
    VK_COMMUNITY,
    VK_NEWSLETTER,
    VK_PERSONAL,
)
from services.brief_parser import BriefVariant, parse_brief
from services.mapping import (
    OBJECT_KIND_COMMUNITY,
    OBJECT_KIND_MAX_CHANNEL,
    OBJECT_KIND_NEWSLETTER,
    OBJECT_KIND_OK_COMMUNITY,
    OBJECT_KIND_OK_PROFILE,
    OBJECT_KIND_PERSONAL,
    OBJECT_KIND_VK_CHANNEL,
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
    assert _spec(_RAW_PERSONAL).object_kind == OBJECT_KIND_PERSONAL


def test_spec_carries_community_kind_from_brief() -> None:
    raw = {**_RAW_PERSONAL, "target_type": "сообщество", "object_url": "https://vk.ru/my_club"}
    assert _spec(raw).object_kind == OBJECT_KIND_COMMUNITY


def test_vanity_personal_page_resolves_as_profile_when_kind_known() -> None:
    # Ключевой случай: по ссылке не понять, но бриф говорит «личная страница».
    ad_object = resolve_ad_object("https://vk.ru/fin_dolm", kind=OBJECT_KIND_PERSONAL)
    assert ad_object.surface is VK_PERSONAL
    assert ad_object.package_id == 3268
    assert ad_object.objective == "socialengagement_profile"


def test_vanity_community_still_resolves_as_community() -> None:
    ad_object = resolve_ad_object("https://vk.ru/my_club", kind=OBJECT_KIND_COMMUNITY)
    assert ad_object.surface is VK_COMMUNITY
    assert ad_object.package_id == 3122
    assert ad_object.objective == "socialengagement"


def test_numeric_url_wins_over_wrong_kind() -> None:
    # Числовой адрес — факт, а не догадка: он авторитетнее подсказки из брифа,
    # но только в паре «сообщество или страница».
    ad_object = resolve_ad_object("https://vk.com/club228817082", kind=OBJECT_KIND_PERSONAL)
    assert ad_object.surface is VK_COMMUNITY
    assert ad_object.url_object_id == "228817082"


def test_unknown_kind_falls_back_to_url_heuristic() -> None:
    # Старое поведение сохраняется, когда подсказки нет.
    assert resolve_ad_object("https://vk.ru/some_name").surface is VK_COMMUNITY
    assert resolve_ad_object("https://vk.com/id777").surface is VK_PERSONAL


def test_campaign_objective_uses_brief_kind_for_vanity_personal_page() -> None:
    # Именно здесь ломалось: цель уезжала как для сообщества.
    assert campaign_objective(_spec(_RAW_PERSONAL)) == "socialengagement_profile"


def test_newsletter_kind_survives_numeric_vk_url() -> None:
    # Ссылка рассылки — это мини-приложение вида vk.com/app…: числовой club/id в ней
    # не встречается, но даже если бы встретился, площадку адресом не опровергнуть.
    ad_object = resolve_ad_object(
        "https://vk.com/app5898182_-228817082#s=3688556", kind=OBJECT_KIND_NEWSLETTER
    )
    assert ad_object.surface is VK_NEWSLETTER
    assert ad_object.objective == "vk_miniapps"


def test_channel_kinds_resolve_to_own_packages() -> None:
    vk = resolve_ad_object("https://vk.com/some_channel", kind=OBJECT_KIND_VK_CHANNEL)
    assert vk.surface is VK_CHANNEL
    assert vk.package_id == 4606

    mx = resolve_ad_object("https://max.ru/joinchat/xyz", kind=OBJECT_KIND_MAX_CHANNEL)
    assert mx.surface is MAX_CHANNEL
    assert mx.package_id == 4686


def test_odnoklassniki_kinds_resolve_to_own_packages() -> None:
    group = resolve_ad_object("https://ok.ru/group/70000001051417", kind=OBJECT_KIND_OK_COMMUNITY)
    assert group.surface is OK_COMMUNITY
    assert group.objective == "odkl"

    profile = resolve_ad_object("https://ok.ru/profile/580483489443", kind=OBJECT_KIND_OK_PROFILE)
    assert profile.surface is OK_PROFILE
    assert profile.objective == "odkl_profile"


def test_odnoklassniki_url_guessed_without_brief_hint() -> None:
    assert resolve_ad_object("https://ok.ru/group/70000001051417").surface is OK_COMMUNITY
    assert resolve_ad_object("https://ok.ru/profile/580483489443").surface is OK_PROFILE


def test_only_communities_exclude_existing_members() -> None:
    # Таргетинг «исключить подписчиков» осмыслен лишь для сообществ — в обеих сетях.
    assert resolve_ad_object("https://vk.ru/c", kind=OBJECT_KIND_COMMUNITY).is_community
    assert resolve_ad_object("https://ok.ru/group/1", kind=OBJECT_KIND_OK_COMMUNITY).is_community
    assert not resolve_ad_object("https://vk.ru/p", kind=OBJECT_KIND_PERSONAL).is_community
    assert not resolve_ad_object("https://vk.com/app1", kind=OBJECT_KIND_NEWSLETTER).is_community
