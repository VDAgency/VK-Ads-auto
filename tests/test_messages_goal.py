"""Цель «Сообщения»: площадка заведена, но недоступна до боевой проверки.

Требование договора (ТЗ §3): «Сообщения — объект „Сообщество", цель „Написать
сообщение"». Пакет VK для неё (3127, `_contact` — заметка в docs/VK_API_REFERENCE.md
рядом с пакетом сообщества 3122/`_join`) не проверен живым запросом: разведка через
`POST /ad_plans.json` в боевом кабинете с чужими кампаниями запрещена (см. задачу).
Площадка заведена с `verified=False` — здесь закрепляем, что это НЕ забыли сделать
непроверяемым: код существует и правильно связан, но клиенту/оператору не предлагается.
"""

from __future__ import annotations

from bot.handlers.creative import GOALS
from integrations.vk_surfaces import SURFACES, surface_for
from services.brief_parser import Goal, TargetType, parse_target_type
from services.goals import goal_for_target_type, subscription_targets
from services.launch_service import SUPPORTED_GOALS


def test_messages_surface_is_in_the_directory_with_the_documented_package() -> None:
    """Площадка «Сообщения» заведена по пакету 3127 (docs/VK_API_REFERENCE.md)."""
    surface = surface_for("messages")
    assert surface.kind == "messages"
    assert surface.package_id == 3127
    assert surface.verified is False


def test_messages_surface_is_registered_in_surfaces_tuple() -> None:
    assert "messages" in {surface.kind for surface in SURFACES}


def test_brief_wording_resolves_to_the_messages_target_type() -> None:
    assert parse_target_type("написать сообщение") is TargetType.MESSAGES
    # Не путается со словом «сообщество» — разные площадки, разные цели.
    assert parse_target_type("сообщество") is TargetType.COMMUNITY


def test_goal_for_messages_target_type_is_messages() -> None:
    assert goal_for_target_type(TargetType.MESSAGES) is Goal.MESSAGES


def test_messages_goal_is_not_offered_as_available_to_the_operator() -> None:
    """Непроверенную площадку показываем (её видно в /surfaces), но выбрать не даём."""
    target = next(t for t in subscription_targets() if t.kind == "messages")
    assert target.available is False


def test_messages_goal_is_locked_in_the_bot_goal_keyboard() -> None:
    """В боте кнопка «Сообщения» остаётся заблокированной (goal:soon)."""
    entry = next(item for item in GOALS if item[0] == "messages")
    _code, _label, enabled = entry
    assert enabled is False


def test_supported_goals_contains_messages() -> None:
    """Ядро больше не отклоняет `goal="messages"` валидатором — блокировка на
    уровне UI/`Surface.verified`, не здесь."""
    assert "messages" in SUPPORTED_GOALS
