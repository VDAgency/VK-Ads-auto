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
    # Ключи == name-инпутов web/static/brief-individual.html (в том же порядке).
    keys = [f.key for f in INDIVIDUAL_FIELDS]
    assert keys == [
        "full_name",
        "object_url",
        "vk_ad_cabinet_id",
        "email",
        "phone",
        "telegram",
        "audience_description",
        "geo",
        "gender",
        "age_from",
        "age_to",
        "budget",
        "term",
        "target_type",
        "materials",
    ]


def test_field_keys_match_web_form_names_community() -> None:
    keys = [f.key for f in COMMUNITY_FIELDS]
    assert keys == [
        "full_name",
        "object_url",
        "vk_ad_cabinet_id",
        "email",
        "phone",
        "telegram",
        "niche",
        "org_type",
        "product_description",
        "site_url",
        "usp",
        "audience_description",
        "geo",
        "gender",
        "budget",
        "term",
    ]


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
    new_payload, unknown = apply_edits(payload, "individual", {1: "Новое имя", 8: "Москва"})
    assert new_payload["full_name"] == "Новое имя"  # поле №1
    assert new_payload["geo"] == "Москва"  # поле №8 (после вставки ID кабинета сдвиг +1)
    assert unknown == []


def test_apply_edits_reports_unknown_numbers() -> None:
    _, unknown = apply_edits({}, "individual", {99: "x", 0: "y"})
    assert unknown == [0, 99]


def test_apply_edits_does_not_mutate_input() -> None:
    payload = {"full_name": "Имя"}
    new_payload, _ = apply_edits(payload, "individual", {1: "Другое"})
    assert payload == {"full_name": "Имя"}  # исходный не тронут
    assert new_payload["full_name"] == "Другое"
