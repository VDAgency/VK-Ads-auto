"""Цель «Сообщения»: площадка прошла боевой зонд 2026-08-23 и включена.

Требование договора (ТЗ §3): «Сообщения — объект „Сообщество", цель „Написать
сообщение"». Боевой зонд `POST /ad_plans.json` с package_id=3127 и баннером без
содержимого подтвердил пакет (VK перешёл к проверке шаблонов, а не отклонил
`objective`) и вернул полный список из 13 разрешённых id шаблонов:
`[486, 422, 525, 527, 400, 401, 530, 338, 529, 339, 145, 150, 537]` — тот же список
(с точностью до порядка), что и у пакета сообщества 3122 (`VK_COMMUNITY`).

В `integrations.vk_surfaces.VK_MESSAGES.patterns` заведены 10 из этих 13 id — те, для
которых медиа-слот уже известен (общий с `VK_COMMUNITY`). Раскрытие оставшихся трёх
(486, 422, 537) через `/banner_patterns.json` в этой сессии не проводилось — это не
блокирует площадку (10 заведённых шаблонов сами по себе валидны для пакета 3127),
просто клиенту пока не предлагаются три дополнительные формы креатива.
"""

from __future__ import annotations

from bot.handlers.creative import GOALS
from integrations.vk_surfaces import SURFACES, surface_for
from services.brief_parser import Goal, TargetType, parse_target_type
from services.goals import goal_for_target_type, subscription_targets
from services.launch_service import SUPPORTED_GOALS

# Полный список id шаблонов, подтверждённый зондом 2026-08-23 для пакета 3127
# (и, с точностью до порядка 145/150, для пакета сообщества 3122).
CONFIRMED_PACKAGE_PATTERN_IDS = {486, 422, 525, 527, 400, 401, 530, 338, 529, 339, 145, 150, 537}


def test_messages_surface_is_in_the_directory_with_the_documented_package() -> None:
    """Площадка «Сообщения» заведена по пакету 3127, зонд подтвердил его и objective."""
    surface = surface_for("messages")
    assert surface.kind == "messages"
    assert surface.package_id == 3127
    assert surface.objective == "socialengagement"
    assert surface.verified is True


def test_messages_surface_is_registered_in_surfaces_tuple() -> None:
    assert "messages" in {surface.kind for surface in SURFACES}


def test_messages_surface_patterns_are_a_confirmed_subset_of_the_live_probe() -> None:
    """10 из 13 подтверждённых id заведены (у всех известен медиа-слот); оставшиеся
    три (486, 422, 537) подтверждены зондом как допустимые пакетом, но их медиа-слот
    в этой сессии не раскрывался — не фабрикуем данные, которых не проверяли."""
    surface = surface_for("messages")
    ids = {pattern.pattern_id for pattern in surface.patterns}
    assert len(ids) == 10
    assert ids <= CONFIRMED_PACKAGE_PATTERN_IDS
    assert ids == {529, 400, 525, 339, 530, 401, 527, 145, 338, 150}


def test_brief_wording_resolves_to_the_messages_target_type() -> None:
    assert parse_target_type("написать сообщение") is TargetType.MESSAGES
    # Не путается со словом «сообщество» — разные площадки, разные цели.
    assert parse_target_type("сообщество") is TargetType.COMMUNITY


def test_goal_for_messages_target_type_is_messages() -> None:
    assert goal_for_target_type(TargetType.MESSAGES) is Goal.MESSAGES


def test_messages_goal_is_offered_as_available_to_the_operator() -> None:
    """Проверенная площадка доступна к выбору — как и остальные шесть/семь."""
    target = next(t for t in subscription_targets() if t.kind == "messages")
    assert target.available is True


def test_messages_goal_is_unlocked_in_the_bot_goal_keyboard() -> None:
    """В боте кнопка «Сообщения» больше не заблокирована."""
    entry = next(item for item in GOALS if item[0] == "messages")
    _code, _label, enabled = entry
    assert enabled is True


def test_supported_goals_contains_messages() -> None:
    assert "messages" in SUPPORTED_GOALS
