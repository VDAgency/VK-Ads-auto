"""Подбор шаблона объявления под креатив и площадку.

Наборы шаблонов сняты живьём: VK на объявление без содержимого отвечает 400 и
перечисляет разрешённые пакетом шаблоны (`patterns.arguments.patterns`, 2026-07-27).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from integrations.vk_creative_formats import fit_to_slot, image_size, is_video, pattern_for_creative
from integrations.vk_surfaces import (
    CONTENT_SLOTS,
    ICON_SLOT,
    MAX_CHANNEL,
    OK_COMMUNITY,
    OK_PROFILE,
    SURFACES,
    VK_CHANNEL,
    VK_COMMUNITY,
    VK_NEWSLETTER,
    VK_PERSONAL,
    pick_pattern,
    ratio_of,
    slot_size,
    surface_for,
    text_limit,
)


def _image(tmp_path: Path, width: int, height: int) -> str:
    from PIL import Image

    path = tmp_path / f"pic_{width}x{height}.png"
    Image.new("RGB", (width, height), (10, 20, 30)).save(path)
    return str(path)


def test_every_surface_has_icon_and_one_main_slot() -> None:
    for surface in SURFACES:
        assert surface.patterns, f"{surface.kind} без шаблонов"
        for pattern in surface.patterns:
            assert pattern.media_slot != ICON_SLOT
            assert pattern.media_slot in CONTENT_SLOTS


def test_cta_slot_differs_by_surface() -> None:
    # Именно из-за разных имён кнопки объявление и не совпадало с шаблоном.
    assert pick_pattern(VK_COMMUNITY, ratio="1:1", is_video=False).cta_slot == "cta_community_vk"
    assert pick_pattern(VK_PERSONAL, ratio="1:1", is_video=False).cta_slot == "cta_profile_vk"
    assert pick_pattern(VK_NEWSLETTER, ratio="1:1", is_video=False).cta_slot == "cta_miniapp_vk"
    assert pick_pattern(VK_CHANNEL, ratio="1:1", is_video=False).cta_slot == "cta_sites_full"
    assert pick_pattern(MAX_CHANNEL, ratio="1:1", is_video=False).cta_slot == "cta_sites_full"


def test_channels_use_short_text_slot() -> None:
    # У каналов VK и MAX текст режется до 90 символов, а не до 2000.
    pattern = pick_pattern(VK_CHANNEL, ratio="1:1", is_video=False)
    assert pattern.text_slot == "text_90"
    assert text_limit(pattern.text_slot) == 90


def test_personal_page_has_no_tall_portrait_image() -> None:
    # У страницы нет шаблона под картинку 9:16 (525 в пакет 3268 не входит), поэтому
    # вертикальный кадр уходит в ближайшее доступное — 4:5, а не отвергается.
    pattern = pick_pattern(VK_PERSONAL, ratio="9:16", is_video=False)
    assert pattern.ratio == "4:5"
    assert pattern.media_slot == "image_4_5"


def test_community_keeps_tall_portrait_image() -> None:
    pattern = pick_pattern(VK_COMMUNITY, ratio="9:16", is_video=False)
    assert pattern.media_slot == "image_607x1080"


def test_odnoklassniki_has_no_portrait_at_all() -> None:
    for surface in (OK_COMMUNITY, OK_PROFILE):
        assert not any(pattern.is_portrait for pattern in surface.patterns)
        # Вертикальный кадр всё равно принимаем — вписываем в квадрат.
        assert pick_pattern(surface, ratio="9:16", is_video=False).ratio == "1:1"


def test_longest_video_variant_wins() -> None:
    # Из двух длительностей берём ту, что не ограничивает ролик.
    pattern = pick_pattern(VK_NEWSLETTER, ratio="9:16", is_video=True)
    assert pattern.max_seconds == 180


def test_pattern_picked_from_real_image(tmp_path: Path) -> None:
    square = _image(tmp_path, 1024, 1024)
    assert pattern_for_creative(VK_NEWSLETTER, square).media_slot == "image_600x600"

    tall = _image(tmp_path, 768, 1376)
    assert pattern_for_creative(VK_COMMUNITY, tall).media_slot == "image_607x1080"


def test_video_goes_to_square_pattern() -> None:
    assert pattern_for_creative(VK_COMMUNITY, "clip.mp4").is_video
    assert is_video("clip.mp4") and not is_video("pic.png")


def test_surface_lookup_falls_back_to_community() -> None:
    assert surface_for("no-such-surface") is VK_COMMUNITY
    assert surface_for("newsletter") is VK_NEWSLETTER


def test_ratio_detection() -> None:
    assert ratio_of(600, 600) == "1:1"
    assert ratio_of(1920, 1080) == "16:9"
    assert ratio_of(1080, 1920) == "9:16"
    assert ratio_of(1080, 1350) == "4:5"


def test_image_resized_to_slot(tmp_path: Path) -> None:
    source = _image(tmp_path, 1024, 1024)
    target = slot_size("image_600x600")
    assert target is not None
    out = fit_to_slot(source, target, tmp_path / "_vk")
    assert image_size(str(out)) == (600, 600)


def test_video_slots_have_no_fixed_size() -> None:
    assert slot_size("video_square_300s") is None
    assert slot_size(ICON_SLOT) == (256, 256)


def test_unknown_image_format_is_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image at all")
    with pytest.raises(ValueError):
        image_size(str(broken))
