"""Тесты разбора брифа (Фаза 1). Фикстуры — из HTML-макетов заказчика, без живого
Google Sheets. Данные синтетические (не ПДн): example.com / телефоны 900-000-…"""

from __future__ import annotations

import pytest
from services.brief_parser import (
    BriefValidationError,
    BriefVariant,
    Gender,
    Goal,
    OrgType,
    TargetType,
    normalize_phone,
    normalize_telegram,
    parse_age,
    parse_brief,
    parse_budget,
    parse_gender,
    parse_materials,
    parse_org_type,
    parse_target_type,
    split_competitors,
)

# --- parse_budget -----------------------------------------------------------


def test_budget_plain_amount() -> None:
    amount, discuss = parse_budget("5 000 ₽")
    assert amount == 5000
    assert discuss is False


def test_budget_with_do_prefix() -> None:
    # «до 50 000 ₽» → берём верхнюю границу как ориентир.
    amount, discuss = parse_budget("до 50 000 ₽")
    assert amount == 50000
    assert discuss is False


def test_budget_discussion_option() -> None:
    amount, discuss = parse_budget("Готов(а) обсудить")
    assert amount is None
    assert discuss is True


def test_budget_empty_is_discussion() -> None:
    amount, discuss = parse_budget("")
    assert amount is None
    assert discuss is True


# --- normalize_phone --------------------------------------------------------


def test_phone_formatted_plus7() -> None:
    assert normalize_phone("+7 (900) 123-45-67") == "+79001234567"


def test_phone_leading_eight_becomes_seven() -> None:
    assert normalize_phone("8 900 123 45 67") == "+79001234567"


def test_phone_ten_digits_gets_country_code() -> None:
    assert normalize_phone("9001234567") == "+79001234567"


def test_phone_invalid_returns_none() -> None:
    assert normalize_phone("123") is None
    assert normalize_phone("") is None


# --- normalize_telegram -----------------------------------------------------


def test_telegram_plain_username_gets_at() -> None:
    assert normalize_telegram("username") == "@username"


def test_telegram_keeps_existing_at() -> None:
    assert normalize_telegram("@user_name") == "@user_name"


def test_telegram_strips_url_prefix() -> None:
    assert normalize_telegram("https://t.me/user_name") == "@user_name"


def test_telegram_empty_returns_none() -> None:
    assert normalize_telegram("") is None


# --- parse_gender -----------------------------------------------------------


def test_gender_male_female_any() -> None:
    assert parse_gender("Мужской") is Gender.MALE
    assert parse_gender("Женский") is Gender.FEMALE
    assert parse_gender("Любой") is Gender.ANY


def test_gender_missing_defaults_to_any() -> None:
    assert parse_gender("") is Gender.ANY


# --- parse_age --------------------------------------------------------------


def test_age_range_parsed() -> None:
    assert parse_age("18", "35") == (18, 35)


def test_age_missing_parts_none() -> None:
    assert parse_age("", "") == (None, None)
    assert parse_age("25", "") == (25, None)


def test_age_non_numeric_none() -> None:
    assert parse_age("молодёжь", "") == (None, None)


# --- parse_materials --------------------------------------------------------


def test_materials_photo() -> None:
    m = parse_materials("📸 Да, есть фото")
    assert m.has_photo is True
    assert m.has_video is False
    assert m.needs_help is False


def test_materials_both() -> None:
    m = parse_materials("📸🎬 Есть и фото, и видео")
    assert m.has_photo is True
    assert m.has_video is True


def test_materials_needs_help() -> None:
    m = parse_materials("❌ Ничего нет, нужна помощь")
    assert m.needs_help is True
    assert m.has_photo is False
    assert m.has_video is False


# --- parse_target_type / parse_org_type -------------------------------------


def test_target_type_personal_and_community() -> None:
    assert parse_target_type("🧑 Личная страница") is TargetType.PERSONAL_PAGE
    assert parse_target_type("👥 Сообщество / группа") is TargetType.COMMUNITY


def test_org_type_variants() -> None:
    assert parse_org_type("ИП") is OrgType.SOLE_TRADER
    assert parse_org_type("Самозанятый") is OrgType.SELF_EMPLOYED
    assert parse_org_type("ООО / Юр. лицо") is OrgType.LEGAL_ENTITY
    assert parse_org_type("Физ. лицо") is OrgType.INDIVIDUAL


# --- split_competitors ------------------------------------------------------


def test_split_competitors_by_lines() -> None:
    raw = "https://vk.com/a\nhttps://vk.com/b\n"
    assert split_competitors(raw) == ["https://vk.com/a", "https://vk.com/b"]


def test_split_competitors_empty() -> None:
    assert split_competitors("") == []


# --- parse_brief: вариант физлицо -------------------------------------------


def _individual_raw() -> dict[str, str]:
    return {
        "full_name": "Иванов Иван",
        "phone": "+7 (900) 123-45-67",
        "telegram": "@ivanov",
        "email": "ivanov@example.com",
        "object_url": "https://vk.com/ivanov",
        "target_type": "🧑 Личная страница",
        "audience_description": "девушки 20-35, мода и красота",
        "gender": "Женский",
        "age_from": "20",
        "age_to": "35",
        "geo": "Москва, СПб",
        "budget": "10 000 ₽",
        "term": "1 месяц",
        "materials": "📸 Да, есть фото",
        "materials_url": "https://disk.yandex.ru/d/abc",
        "competitors": "https://vk.com/comp1\nhttps://vk.com/comp2",
        "extra": "сезонность весна",
    }


def test_parse_individual_brief_core_fields() -> None:
    brief = parse_brief(_individual_raw(), BriefVariant.INDIVIDUAL)
    assert brief.variant is BriefVariant.INDIVIDUAL
    assert brief.goal is Goal.SUBSCRIBERS
    assert brief.full_name == "Иванов Иван"
    assert brief.object_url == "https://vk.com/ivanov"
    assert brief.target_type is TargetType.PERSONAL_PAGE


def test_parse_individual_contact_normalized() -> None:
    brief = parse_brief(_individual_raw(), BriefVariant.INDIVIDUAL)
    assert brief.contact.phone == "+79001234567"
    assert brief.contact.telegram == "@ivanov"
    assert brief.contact.email == "ivanov@example.com"


def test_parse_individual_audience_and_budget() -> None:
    brief = parse_brief(_individual_raw(), BriefVariant.INDIVIDUAL)
    assert brief.audience.gender is Gender.FEMALE
    assert brief.audience.age_from == 20
    assert brief.audience.age_to == 35
    assert brief.audience.geo == "Москва, СПб"
    assert brief.budget.amount_rub == 10000
    assert brief.budget.needs_discussion is False


def test_individual_has_no_business_fields() -> None:
    brief = parse_brief(_individual_raw(), BriefVariant.INDIVIDUAL)
    assert brief.niche is None
    assert brief.org_type is None


# --- parse_brief: вариант ИП/сообщество -------------------------------------


def _community_raw() -> dict[str, str]:
    return {
        "full_name": "Петрова Анна",
        "company": "ООО Ромашка",
        "phone": "89001234567",
        "telegram": "petrova",
        "email": "anna@example.com",
        "niche": "доставка цветов",
        "org_type": "ИП",
        "tax_id": "770700000000",
        "object_url": "https://vk.com/romashka",
        "site_url": "https://romashka.example",
        "product_description": "доставка букетов за 2 часа",
        "avg_check": "от 3000 до 8000 руб",
        "usp": "бесплатная доставка",
        "offers": "скидка 20%",
        "audience_description": "женщины 25-45",
        "gender": "Любой",
        "age_from": "25",
        "age_to": "45",
        "geo": "вся Россия",
        "exclusions": "конкуренты",
        "goal": "👥 Подписчики",
        "budget": "до 50 000 ₽",
        "term": "2 недели",
        "materials": "📸🎬 Есть и фото, и видео",
        "competitors": "https://vk.com/c1",
    }


def test_parse_community_business_fields() -> None:
    brief = parse_brief(_community_raw(), BriefVariant.COMMUNITY)
    assert brief.variant is BriefVariant.COMMUNITY
    assert brief.niche == "доставка цветов"
    assert brief.org_type is OrgType.SOLE_TRADER
    assert brief.company == "ООО Ромашка"
    assert brief.product_description == "доставка букетов за 2 часа"


def test_community_target_type_defaults_to_community() -> None:
    # У варианта ИП нет поля «куда привлекаем» — объект всегда сообщество.
    brief = parse_brief(_community_raw(), BriefVariant.COMMUNITY)
    assert brief.target_type is TargetType.COMMUNITY


def test_community_goal_is_subscribers_by_scope() -> None:
    brief = parse_brief(_community_raw(), BriefVariant.COMMUNITY)
    assert brief.goal is Goal.SUBSCRIBERS


def test_community_budget_upper_bound() -> None:
    brief = parse_brief(_community_raw(), BriefVariant.COMMUNITY)
    assert brief.budget.amount_rub == 50000
    assert brief.materials.has_photo is True
    assert brief.materials.has_video is True


# --- валидация обязательных полей -------------------------------------------


def test_missing_required_raises() -> None:
    raw = _individual_raw()
    del raw["geo"]
    del raw["budget"]
    with pytest.raises(BriefValidationError) as exc:
        parse_brief(raw, BriefVariant.INDIVIDUAL)
    assert "geo" in exc.value.missing
    assert "budget" in exc.value.missing


def test_at_least_one_contact_required() -> None:
    raw = _individual_raw()
    raw["email"] = ""
    raw["phone"] = ""
    raw["telegram"] = ""
    with pytest.raises(BriefValidationError) as exc:
        parse_brief(raw, BriefVariant.INDIVIDUAL)
    assert "contact" in exc.value.missing


def test_community_requires_niche_and_org_type() -> None:
    raw = _community_raw()
    del raw["niche"]
    del raw["org_type"]
    with pytest.raises(BriefValidationError) as exc:
        parse_brief(raw, BriefVariant.COMMUNITY)
    assert "niche" in exc.value.missing
    assert "org_type" in exc.value.missing


def test_one_contact_is_enough() -> None:
    raw = _individual_raw()
    raw["phone"] = ""
    raw["telegram"] = ""
    # email остаётся — брифа достаточно для идентификации
    brief = parse_brief(raw, BriefVariant.INDIVIDUAL)
    assert brief.contact.email == "ivanov@example.com"
    assert brief.contact.phone is None
