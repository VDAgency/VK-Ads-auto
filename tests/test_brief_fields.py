"""Тесты нумерованной карты полей брифа (`services/brief_fields`)."""

from __future__ import annotations

import pytest
from services.brief_fields import (
    COMMUNITY_FIELDS,
    INDIVIDUAL_FIELDS,
    apply_edits,
    fields_for,
    numbered,
)


def test_fields_for_returns_variant_lists() -> None:
    assert fields_for("individual") is INDIVIDUAL_FIELDS
    assert fields_for("community") is COMMUNITY_FIELDS


def test_fields_for_unknown_variant_raises() -> None:
    with pytest.raises(ValueError):
        fields_for("legal")


def test_field_keys_match_web_form_names_individual() -> None:
    # Ключи == name-инпутов web/app/brief-individual/page.tsx (в том же порядке).
    keys = [f.key for f in INDIVIDUAL_FIELDS]
    assert keys == [
        "full_name",
        "phone",
        "telegram",
        "email",
        "tax_id",
        "object_url",
        "vk_ad_cabinet_id",
        "target_type",
        "audience_description",
        "gender",
        "age_from",
        "age_to",
        "geo",
        "budget",
        "term",
        "materials",
        "materials_url",
        "competitors",
        "extra",
    ]


def test_field_keys_match_web_form_names_community() -> None:
    keys = [f.key for f in COMMUNITY_FIELDS]
    assert keys == [
        "full_name",
        "company",
        "phone",
        "telegram",
        "email",
        "niche",
        "org_type",
        "tax_id",
        "org_name",
        "target_type",
        "object_url",
        "vk_ad_cabinet_id",
        "site_url",
        "product_description",
        "avg_check",
        "usp",
        "offers",
        "audience_description",
        "gender",
        "age_from",
        "age_to",
        "geo",
        "exclusions",
        "goal",
        "budget",
        "term",
        "materials",
        "materials_url",
        "competitors",
        "extra",
    ]


def test_bank_details_not_collected_by_brief() -> None:
    """Реквизиты — не параметр VK и не идентификация (BRIEF_SPEC §0).

    Их место в личном кабинете клиента, а не в брифе: иначе банковские данные
    попадают в payload и в карточку Telegram-бота.
    """
    for fields in (INDIVIDUAL_FIELDS, COMMUNITY_FIELDS):
        assert "bank_details" not in [f.key for f in fields]


def test_every_field_key_is_unique_within_variant() -> None:
    # Дубль ключа тихо ломает правки `номер.значение`: два номера пишут в одно поле.
    for variant in ("individual", "community"):
        keys = [f.key for f in fields_for(variant)]
        assert len(keys) == len(set(keys)), variant


def test_numbered_starts_at_one_and_fills_from_payload() -> None:
    payload = {"full_name": "Вячеслав", "geo": "  Самара  "}
    rows = numbered(payload, "individual")
    assert rows[0] == (1, INDIVIDUAL_FIELDS[0], "Вячеслав")
    # Значение обрезается по пробелам.
    geo_row = next(r for r in rows if r[1].key == "geo")
    assert geo_row[2] == "Самара"
    # Незаполненное поле → пустая строка, номер всё равно есть.
    tg_row = next(r for r in rows if r[1].key == "telegram")
    assert tg_row[2] == ""


def test_numbered_covers_all_canonical_fields() -> None:
    rows = numbered({}, "community")
    assert [r[0] for r in rows] == list(range(1, len(COMMUNITY_FIELDS) + 1))


def test_apply_edits_maps_number_to_key() -> None:
    payload = {"full_name": "Старое имя"}
    new_payload, unknown = apply_edits(payload, "individual", {1: "Новое имя", 13: "Москва"})
    assert new_payload["full_name"] == "Новое имя"  # поле №1
    assert new_payload["geo"] == "Москва"  # поле №13 в порядке секций макета
    assert unknown == []


def test_apply_edits_reports_unknown_numbers() -> None:
    _, unknown = apply_edits({}, "individual", {99: "x", 0: "y"})
    assert unknown == [0, 99]


def test_apply_edits_does_not_mutate_input() -> None:
    payload = {"full_name": "Имя"}
    new_payload, _ = apply_edits(payload, "individual", {1: "Другое"})
    assert payload == {"full_name": "Имя"}  # исходный не тронут
    assert new_payload["full_name"] == "Другое"
