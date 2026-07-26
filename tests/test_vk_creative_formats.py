"""Справочник форматов креатива: подбор слота и честный отказ.

Формы шаблонов сняты живьём с `GET /banner_patterns.json` 2026-07-27.
"""

from __future__ import annotations

import pytest
from integrations.vk_creative_formats import (
    COMMUNITY_FORMATS,
    CTA_SLOT_COMMUNITY,
    CTA_SLOT_PERSONAL,
    ICON_SLOT,
    PERSONAL_FORMATS,
    cta_slot,
    formats_for,
    pick_format,
)
from services.mapping import OBJECT_KIND_COMMUNITY, OBJECT_KIND_PERSONAL


def test_cta_slot_differs_by_object_kind() -> None:
    assert cta_slot(OBJECT_KIND_PERSONAL) == CTA_SLOT_PERSONAL
    assert cta_slot(OBJECT_KIND_COMMUNITY) == CTA_SLOT_COMMUNITY


def test_personal_page_has_no_portrait_formats() -> None:
    # Ключевое ограничение VK: вертикаль доступна только сообществам.
    assert not any(fmt.is_portrait for fmt in PERSONAL_FORMATS)
    assert any(fmt.is_portrait for fmt in COMMUNITY_FORMATS)


def test_square_image_picked_for_personal_page() -> None:
    fmt = pick_format(OBJECT_KIND_PERSONAL, width=1024, height=1024, is_video=False)
    assert fmt.slot == "image_600x600"
    assert 535 in fmt.patterns


def test_landscape_image_picked_for_personal_page() -> None:
    fmt = pick_format(OBJECT_KIND_PERSONAL, width=1920, height=1080, is_video=False)
    assert fmt.slot == "image_1080x607"


def test_vertical_image_rejected_for_personal_page_with_reason() -> None:
    # Вертикальная картинка под личную страницу — отказ с объяснением, а не молча.
    with pytest.raises(ValueError, match="9:16"):
        pick_format(OBJECT_KIND_PERSONAL, width=768, height=1376, is_video=False)


def test_vertical_image_allowed_for_community() -> None:
    fmt = pick_format(OBJECT_KIND_COMMUNITY, width=768, height=1376, is_video=False)
    assert fmt.slot == "image_607x1080"


def test_vertical_video_allowed_for_community() -> None:
    fmt = pick_format(OBJECT_KIND_COMMUNITY, width=1080, height=1920, is_video=True)
    assert fmt.slot.startswith("video_portrait_9_16")


def test_every_format_belongs_to_at_least_one_pattern() -> None:
    for kind in (OBJECT_KIND_PERSONAL, OBJECT_KIND_COMMUNITY):
        for fmt in formats_for(kind):
            assert fmt.patterns, f"{fmt.slot} без шаблона"


def test_icon_slot_is_stable() -> None:
    assert ICON_SLOT == "icon_256x256"
