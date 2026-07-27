"""Площадки, которым креатив не нужен, запускаются без него.

Продвижение готового поста, клипа или трека: объявлением служит сам объект, и VK
принимает баннер с одной ссылкой. Требовать при этом картинку — выдумка на пустом
месте, поэтому у таких брифов есть отдельный запуск.
"""

from __future__ import annotations

from integrations.vk_surfaces import SURFACES, surface_for


def test_only_ready_object_surfaces_skip_the_creative() -> None:
    without = {surface.kind for surface in SURFACES if not surface.needs_creative}
    assert without == {
        "vk_post_community",
        "vk_post_personal",
        "vk_post_promoted",
        "vk_music",
        "vk_clip",
    }


def test_surfaces_without_creative_have_no_patterns() -> None:
    # Шаблон описывает раскладку креатива; там, где креатива нет, шаблону взяться
    # неоткуда — и VK действительно принимает объявление с одной ссылкой.
    for kind in ("vk_post_community", "vk_music", "vk_clip"):
        surface = surface_for(kind)
        assert surface.patterns == ()
        assert surface.default_pattern is None


def test_subscription_surfaces_still_require_a_creative() -> None:
    for kind in ("community", "personal_page", "newsletter", "dzen_channel", "lead_form"):
        assert surface_for(kind).needs_creative, kind


def test_core_exposes_a_launch_without_creative_entry_point() -> None:
    # Бот и веб-кабинет ходят через ядро; без этой точки входа операторам пришлось
    # бы придумывать картинку для поста, у которого своя обложка.
    from services.creative_intake import launch_without_creative

    assert callable(launch_without_creative)
